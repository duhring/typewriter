#!/usr/bin/env python3
"""
Create structured extracts from transcripts and meeting notes.

The extraction is the owner's assistant's; this tool prepares the request,
validates the answer, writes the durable markdown artifact to owners-inbox,
and indexes it. One handoff per extract (docs/handoff-contract.md):

  bin/pka extract_structured transcript prepare --file owners-inbox/transcripts/<file>.md
      -> <dir>/transcript-extract.request.json with the transcript text, title, and URL
  (the assistant writes transcript-extract.response.json beside it: one item, id "extract")
  bin/pka extract_structured transcript import --dir <dir>
      -> owners-inbox/transcript-extracts/<date>-<slug>.md written and indexed

  bin/pka extract_structured meeting prepare --file <notes.md> [--title --date --attendees --meeting-id N]
  bin/pka extract_structured meeting import --dir <dir>

<dir> defaults to owners-inbox/transcript-extracts/requests/<transcript stem>/
(or owners-inbox/meeting-extracts/requests/<slug>/). Every timestamp a
response cites must appear in the transcript; a response that cites one that
does not is refused.

Transcript extract item:
  {"id": "extract", "summary": str,
   "timestamp_highlights": [{"timestamp": str, "label": str}],
   "key_points": [str], "reusable_claims": [str], "questions_raised": [str],
   "action_ideas": [str], "tools_mentioned": [str], "recommended_tags": [str]}
Meeting extract item:
  {"id": "extract", "summary": str, "decisions": [str], "action_items": [str],
   "open_questions": [str], "follow_up_topics": [str], "tools_mentioned": [str],
   "recommended_tags": [str]}
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Ensure tools/ is on sys.path so local modules (handoff, pka_index) resolve from any cwd.
_tools_dir = str(Path(__file__).resolve().parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

import handoff
from pka_index import index_markdown_artifact


PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
TRANSCRIPT_EXTRACTS_DIR = PKA_ROOT / "owners-inbox" / "transcript-extracts"
MEETING_EXTRACTS_DIR = PKA_ROOT / "owners-inbox" / "meeting-extracts"

STAGE_TRANSCRIPT = "transcript-extract"
STAGE_MEETING = "meeting-extract"
TRANSCRIPT_LISTS = {"key_points": 8, "reusable_claims": 8, "questions_raised": 6,
                    "action_ideas": 6, "tools_mentioned": 10, "recommended_tags": 10}
MEETING_LISTS = {"decisions": 12, "action_items": 12, "open_questions": 8,
                 "follow_up_topics": 8, "tools_mentioned": 10, "recommended_tags": 10}


def _slugify(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:80] or fallback


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_first_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _extract_video_url(text: str) -> str:
    match = re.search(r"\*\*Video:\*\*\s*(https?://\S+)", text)
    return match.group(1) if match else ""


def _extract_date_from_name(path: Path) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", path.stem)
    return match.group(1) if match else date.today().isoformat()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in items:
        value = " ".join((item or "").split()).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned


def _limit(items: list[str], max_items: int) -> list[str]:
    return _dedupe(items)[:max_items]


def _split_lines(text: str | None) -> list[str]:
    if not text:
        return []
    items: list[str] = []
    for raw_line in text.splitlines():
        cleaned = raw_line.strip()
        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()
        if cleaned:
            items.append(cleaned)
    return items


def _format_bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- None."]


def transcript_instructions() -> str:
    return (
        "Extract structured knowledge from the transcript in payload.text (the file is also listed under inputs).\n"
        "Write exactly one item with id \"extract\" and these fields:\n"
        "  summary: string\n"
        "  timestamp_highlights: [{timestamp: string, label: string}]\n"
        "  key_points, reusable_claims, questions_raised, action_ideas, tools_mentioned, recommended_tags: [string]\n"
        "Rules:\n"
        "- Be specific and concise; keep each list item short and standalone.\n"
        "- Use a timestamp only when it appears in the transcript exactly as written; every cited timestamp is checked on import.\n"
        "- Prefer reusable claims that could help future writing or strategy work.\n"
        "- An empty list is the correct answer when nothing qualifies."
    )


def meeting_instructions() -> str:
    return (
        "Extract structured meeting knowledge from payload.text.\n"
        "Write exactly one item with id \"extract\" and these fields:\n"
        "  summary: string\n"
        "  decisions, action_items, open_questions, follow_up_topics, tools_mentioned, recommended_tags: [string]\n"
        "Rules:\n"
        "- Be factual and concise.\n"
        "- Do not invent decisions or action items that are not supported by the notes.\n"
        "- If something is missing, return an empty list."
    )


def _string_list(item: dict, key: str) -> list[str]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise handoff.HandoffError("item_invalid", f"{key} must be a list of strings", field=key)
    return value


def normalize_transcript_item(item: dict, transcript_text: str) -> dict[str, Any]:
    """Validate the assistant's extract and apply the same limits as before."""
    if not isinstance(item.get("summary"), str):
        raise handoff.HandoffError("item_invalid", "summary must be a string", field="summary")
    highlights = item.get("timestamp_highlights", [])
    if not isinstance(highlights, list):
        raise handoff.HandoffError("item_invalid", "timestamp_highlights must be a list", field="timestamp_highlights")
    cleaned_highlights = []
    for entry in highlights:
        if not isinstance(entry, dict) or not isinstance(entry.get("timestamp"), str) or not isinstance(entry.get("label"), str):
            raise handoff.HandoffError("item_invalid", "each timestamp highlight needs a timestamp and a label", field="timestamp_highlights")
        stamp, label = entry["timestamp"].strip(), entry["label"].strip()
        if not stamp or not label:
            continue
        if stamp not in transcript_text:
            raise handoff.HandoffError(
                "timestamp_not_found", f"timestamp {stamp!r} does not appear in the transcript", timestamp=stamp
            )
        cleaned_highlights.append({"timestamp": stamp, "label": label})
    result: dict[str, Any] = {"summary": item["summary"].strip(), "timestamp_highlights": cleaned_highlights[:8]}
    for key, limit in TRANSCRIPT_LISTS.items():
        result[key] = _limit(_string_list(item, key), limit)
    return result


def normalize_meeting_item(item: dict) -> dict[str, Any]:
    if not isinstance(item.get("summary"), str):
        raise handoff.HandoffError("item_invalid", "summary must be a string", field="summary")
    result: dict[str, Any] = {"summary": item["summary"].strip()}
    for key, limit in MEETING_LISTS.items():
        result[key] = _limit(_string_list(item, key), limit)
    return result


def _save_markdown(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _transcript_extract_path(transcript_path: Path, title: str) -> Path:
    base_date = _extract_date_from_name(transcript_path)
    slug = _slugify(title, transcript_path.stem)
    path = TRANSCRIPT_EXTRACTS_DIR / f"{base_date}-{slug}.md"
    version = 2
    while path.exists():
        path = TRANSCRIPT_EXTRACTS_DIR / f"{base_date}-{slug}-v{version}.md"
        version += 1
    return path


def _meeting_extract_path(title: str, date_value: str) -> Path:
    base_date = date_value[:10] if re.match(r"\d{4}-\d{2}-\d{2}", date_value or "") else date.today().isoformat()
    slug = _slugify(title, "meeting")
    path = MEETING_EXTRACTS_DIR / f"{base_date}-{slug}.md"
    version = 2
    while path.exists():
        path = MEETING_EXTRACTS_DIR / f"{base_date}-{slug}-v{version}.md"
        version += 1
    return path


def save_transcript_extract(
    transcript_path: Path,
    *,
    title: str,
    source_url: str,
    extracted: dict[str, Any],
) -> Path:
    output_path = _transcript_extract_path(transcript_path, title)
    lines = [
        f"# {title} — Structured Transcript Extract",
        "",
        f"**Source Transcript:** {transcript_path}",
    ]
    if source_url:
        lines.append(f"**Video:** {source_url}")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            extracted.get("summary", "") or "None.",
            "",
            "## Timestamp Highlights",
            "",
        ]
    )
    if extracted.get("timestamp_highlights"):
        for item in extracted["timestamp_highlights"]:
            lines.append(f"- {item['timestamp']} — {item['label']}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Key Points", ""])
    lines.extend(_format_bullets(extracted.get("key_points", [])))
    lines.extend(["", "## Reusable Claims", ""])
    lines.extend(_format_bullets(extracted.get("reusable_claims", [])))
    lines.extend(["", "## Questions Raised", ""])
    lines.extend(_format_bullets(extracted.get("questions_raised", [])))
    lines.extend(["", "## Action Ideas", ""])
    lines.extend(_format_bullets(extracted.get("action_ideas", [])))
    lines.extend(["", "## Tools Mentioned", ""])
    lines.extend(_format_bullets(extracted.get("tools_mentioned", [])))
    lines.extend(["", "## Recommended Tags", ""])
    lines.extend(_format_bullets(extracted.get("recommended_tags", [])))
    return _save_markdown(output_path, "\n".join(lines) + "\n")


def save_meeting_extract(
    *,
    source_path: Path | None,
    title: str,
    date_value: str,
    attendees: str,
    extracted: dict[str, Any],
) -> Path:
    output_path = _meeting_extract_path(title, date_value)
    lines = [
        f"# {title} — Meeting Extract",
        "",
    ]
    if source_path:
        lines.append(f"**Source:** {source_path}")
    if date_value:
        lines.append(f"**Date:** {date_value}")
    if attendees:
        lines.append(f"**Attendees:** {attendees}")
    lines.extend(["", "## Summary", "", extracted.get("summary", "") or "None.", "", "## Decisions", ""])
    lines.extend(_format_bullets(extracted.get("decisions", [])))
    lines.extend(["", "## Action Items", ""])
    lines.extend(_format_bullets(extracted.get("action_items", [])))
    lines.extend(["", "## Open Questions", ""])
    lines.extend(_format_bullets(extracted.get("open_questions", [])))
    lines.extend(["", "## Follow-Up Topics", ""])
    lines.extend(_format_bullets(extracted.get("follow_up_topics", [])))
    lines.extend(["", "## Tools Mentioned", ""])
    lines.extend(_format_bullets(extracted.get("tools_mentioned", [])))
    lines.extend(["", "## Recommended Tags", ""])
    lines.extend(_format_bullets(extracted.get("recommended_tags", [])))
    return _save_markdown(output_path, "\n".join(lines) + "\n")


def _update_meeting_row(meeting_id: int, extracted: dict[str, Any], extract_path: Path) -> None:
    marker = "\n\n---\nStructured Extract\n"
    notes_sections = [extracted.get("summary", "").strip()]
    if extracted.get("decisions"):
        notes_sections.append("Decisions:\n" + "\n".join(f"- {item}" for item in extracted["decisions"]))
    if extracted.get("open_questions"):
        notes_sections.append("Open Questions:\n" + "\n".join(f"- {item}" for item in extracted["open_questions"]))
    notes_sections.append(f"Structured extract: {extract_path.relative_to(PKA_ROOT)}")
    structured_notes = "\n\n".join(section for section in notes_sections if section.strip())
    with sqlite3.connect(str(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT notes, action_items FROM meetings WHERE id = ?",
            (meeting_id,),
        ).fetchone()
        existing_notes = (row[0] if row else "") or ""
        existing_actions = (row[1] if row else "") or ""
        base_notes = existing_notes.split(marker, 1)[0].rstrip()
        notes = f"{base_notes}{marker}{structured_notes}" if base_notes else structured_notes
        action_items = _dedupe(_split_lines(existing_actions) + extracted.get("action_items", []))
        conn.execute(
            """
            UPDATE meetings
            SET notes = ?, action_items = ?
            WHERE id = ?
            """,
            (
                notes,
                "\n".join(f"- {item}" for item in action_items) or None,
                meeting_id,
            ),
        )
        conn.commit()


def default_transcript_dir(transcript_path: Path) -> Path:
    return TRANSCRIPT_EXTRACTS_DIR / "requests" / transcript_path.stem


def default_meeting_dir(title: str) -> Path:
    return MEETING_EXTRACTS_DIR / "requests" / _slugify(title, "meeting")


def prepare_transcript_extract(transcript_path: Path, *, title: str, source_url: str,
                               directory: Path | None = None) -> handoff.Request:
    transcript_path = transcript_path.resolve()
    text = _read_text(transcript_path)
    return handoff.prepare(
        stage=STAGE_TRANSCRIPT,
        directory=directory or default_transcript_dir(transcript_path),
        inputs={"transcript": transcript_path},
        payload={"title": title, "source_url": source_url, "transcript_path": str(transcript_path), "text": text},
        expects_ids=["extract"],
        instructions=transcript_instructions(),
    )


def import_transcript_extract(directory: Path) -> dict[str, Any]:
    request = handoff.load_request(directory / f"{STAGE_TRANSCRIPT}.request.json")
    payload = request.data["payload"]
    transcript_path = Path(payload["transcript_path"])
    text = _read_text(transcript_path)
    title, source_url = payload["title"], payload["source_url"]

    def apply(items: list[dict]) -> dict[str, Any]:
        extracted = normalize_transcript_item(items[0], text)
        output_path = save_transcript_extract(transcript_path, title=title, source_url=source_url, extracted=extracted)
        summary = extracted.get("summary", "") or f"Structured transcript extract for {title}."
        index_result = index_markdown_artifact(
            output_path,
            title=f"{title} — Structured Transcript Extract",
            category="transcript-extract",
            tags=["transcript-extract", "youtube"] + extracted.get("recommended_tags", []),
            source_url=source_url or None,
            summary=summary,
        )
        return {
            "status": "created", "type": STAGE_TRANSCRIPT, "title": title, "source": str(transcript_path),
            "path": str(output_path), "knowledge_base_id": index_result["knowledge_base_id"],
            "file_id": index_result["file_id"], **extracted,
        }

    result = handoff.import_response(request.path, apply=apply,
                                     item_validator=lambda it: normalize_transcript_item(it, text))
    if result.status == "already_imported":
        return {"status": "already_imported", "type": STAGE_TRANSCRIPT, "request_id": request.request_id}
    return result.result


def prepare_meeting_extract(*, source_path: Path | None, text: str, title: str, date_value: str,
                            attendees: str, meeting_id: int | None, directory: Path | None = None) -> handoff.Request:
    inputs = {"notes": source_path.resolve()} if source_path else {}
    return handoff.prepare(
        stage=STAGE_MEETING,
        directory=directory or default_meeting_dir(title),
        inputs=inputs,
        payload={"title": title, "date": date_value, "attendees": attendees, "meeting_id": meeting_id,
                 "source_path": str(source_path.resolve()) if source_path else None, "text": text},
        expects_ids=["extract"],
        instructions=meeting_instructions(),
    )


def import_meeting_extract(directory: Path) -> dict[str, Any]:
    request = handoff.load_request(directory / f"{STAGE_MEETING}.request.json")
    payload = request.data["payload"]
    source_path = Path(payload["source_path"]) if payload.get("source_path") else None
    title, date_value, attendees = payload["title"], payload.get("date", ""), payload.get("attendees", "")
    meeting_id = payload.get("meeting_id")

    def apply(items: list[dict]) -> dict[str, Any]:
        extracted = normalize_meeting_item(items[0])
        output_path = save_meeting_extract(source_path=source_path, title=title, date_value=date_value,
                                           attendees=attendees, extracted=extracted)
        summary = extracted.get("summary", "") or f"Structured meeting extract for {title}."
        index_result = index_markdown_artifact(
            output_path, title=f"{title} — Meeting Extract", category="meeting-extract",
            tags=["meeting-extract", "meeting"] + extracted.get("recommended_tags", []), summary=summary,
        )
        if meeting_id is not None:
            _update_meeting_row(meeting_id, extracted, output_path)
        return {
            "status": "created", "type": STAGE_MEETING, "title": title, "path": str(output_path),
            "source": str(source_path) if source_path else None, "meeting_id": meeting_id,
            "knowledge_base_id": index_result["knowledge_base_id"], "file_id": index_result["file_id"], **extracted,
        }

    result = handoff.import_response(request.path, apply=apply, item_validator=normalize_meeting_item)
    if result.status == "already_imported":
        return {"status": "already_imported", "type": STAGE_MEETING, "request_id": request.request_id}
    return result.result


def cmd_transcript(args: argparse.Namespace) -> int:
    if args.action == "import":
        if not args.dir:
            print(json.dumps({"error": "transcript import needs --dir (the request directory)"}), file=sys.stderr)
            return 1
        print(json.dumps(import_transcript_extract(Path(args.dir).expanduser().resolve()), indent=2))
        return 0
    if not args.file:
        print(json.dumps({"error": "transcript prepare needs --file"}), file=sys.stderr)
        return 1
    transcript_path = Path(args.file)
    if not transcript_path.is_absolute():
        transcript_path = PKA_ROOT / transcript_path
    if not transcript_path.exists():
        print(json.dumps({"error": f"Transcript file not found: {transcript_path}"}), file=sys.stderr)
        return 1
    text = _read_text(transcript_path)
    title = args.title or _extract_first_heading(text) or transcript_path.stem
    source_url = args.url or _extract_video_url(text)
    request = prepare_transcript_extract(
        transcript_path, title=title, source_url=source_url,
        directory=Path(args.dir).expanduser().resolve() if args.dir else None,
    )
    print(json.dumps({"status": "prepared", "stage": STAGE_TRANSCRIPT, "title": title, "request": str(request.path),
                      "response": str(request.response_path), "dir": str(request.path.parent)}, indent=2))
    return 0


def cmd_meeting(args: argparse.Namespace) -> int:
    if args.action == "import":
        if not args.dir:
            print(json.dumps({"error": "meeting import needs --dir (the request directory)"}), file=sys.stderr)
            return 1
        print(json.dumps(import_meeting_extract(Path(args.dir).expanduser().resolve()), indent=2))
        return 0
    source_path: Path | None = None
    text = ""
    if args.file:
        source_path = Path(args.file)
        if not source_path.is_absolute():
            source_path = PKA_ROOT / source_path
        if not source_path.exists():
            print(json.dumps({"error": f"Meeting file not found: {source_path}"}), file=sys.stderr)
            return 1
        text = _read_text(source_path)
    elif args.text:
        text = args.text
    else:
        print(json.dumps({"error": "Provide --file or --text for meeting extraction."}), file=sys.stderr)
        return 1
    title = args.title or (source_path.stem if source_path else "Untitled Meeting")
    request = prepare_meeting_extract(
        source_path=source_path, text=text, title=title, date_value=args.date or "",
        attendees=args.attendees or "", meeting_id=args.meeting_id,
        directory=Path(args.dir).expanduser().resolve() if args.dir else None,
    )
    print(json.dumps({"status": "prepared", "stage": STAGE_MEETING, "title": title, "request": str(request.path),
                      "response": str(request.response_path), "dir": str(request.path.parent)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create structured extracts from transcripts and meetings")
    sub = parser.add_subparsers(dest="command", required=True)

    p_transcript = sub.add_parser("transcript", help="Structured notes from a transcript markdown file")
    p_transcript.add_argument("action", choices=["prepare", "import"])
    p_transcript.add_argument("--file", help="Transcript markdown file (prepare)")
    p_transcript.add_argument("--title", help="Override title")
    p_transcript.add_argument("--url", help="Override source URL")
    p_transcript.add_argument("--dir", help="Request directory (default: owners-inbox/transcript-extracts/requests/<stem>)")

    p_meeting = sub.add_parser("meeting", help="Structured notes from meeting notes or a transcript")
    p_meeting.add_argument("action", choices=["prepare", "import"])
    p_meeting.add_argument("--dir", help="Request directory (default: owners-inbox/meeting-extracts/requests/<slug>)")
    p_meeting.add_argument("--file", help="Meeting notes/transcript file")
    p_meeting.add_argument("--text", help="Inline meeting notes/transcript")
    p_meeting.add_argument("--title", help="Meeting title")
    p_meeting.add_argument("--date", help="Meeting date")
    p_meeting.add_argument("--attendees", help="Comma-separated attendees")
    p_meeting.add_argument("--meeting-id", type=int, help="Existing meetings.id to update")

    args = parser.parse_args(argv)
    try:
        if args.command == "transcript":
            return cmd_transcript(args)
        if args.command == "meeting":
            return cmd_meeting(args)
    except handoff.HandoffError as exc:
        print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
