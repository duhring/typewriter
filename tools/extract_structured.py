#!/usr/bin/env python3
"""
Create structured extracts from transcripts and meeting notes.

Outputs durable markdown artifacts to owners-inbox and indexes them into PKA.
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

# Ensure tools/ is on sys.path so local modules (llm, pka_index, etc.) resolve from any cwd.
_tools_dir = str(Path(__file__).resolve().parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

import lmstudio
import llm
from pka_index import index_markdown_artifact


PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
TRANSCRIPT_EXTRACTS_DIR = PKA_ROOT / "owners-inbox" / "transcript-extracts"
MEETING_EXTRACTS_DIR = PKA_ROOT / "owners-inbox" / "meeting-extracts"


def _slugify(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:80] or fallback


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


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


def _prompt_json(system: str, user: str, *, max_tokens: int = 1400) -> dict[str, Any]:
    response = llm.chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=max_tokens,
        label="extract_structured",
    )
    choices = response.get("choices", [])
    if not choices:
        raise llm.LLMError("LLM provider returned no choices.")
    content = choices[0].get("message", {}).get("content", "")
    return _extract_json(content)


def _format_bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- None."]


def _transcript_system_prompt() -> str:
    return (
        "You extract structured knowledge from transcripts.\n"
        "Return valid JSON only with this shape:\n"
        "{"
        "\"summary\": string,"
        "\"timestamp_highlights\": [{\"timestamp\": string, \"label\": string}],"
        "\"key_points\": [string],"
        "\"reusable_claims\": [string],"
        "\"questions_raised\": [string],"
        "\"action_ideas\": [string],"
        "\"tools_mentioned\": [string],"
        "\"recommended_tags\": [string]"
        "}\n"
        "Rules:\n"
        "- Be specific and concise.\n"
        "- Use exact timestamps only when they clearly appear in the transcript.\n"
        "- Prefer reusable claims that could help future writing or strategy work.\n"
        "- Keep each list item short and standalone.\n"
        "- No markdown fences."
    )


def _meeting_system_prompt() -> str:
    return (
        "You extract structured meeting knowledge from notes or transcripts.\n"
        "Return valid JSON only with this shape:\n"
        "{"
        "\"summary\": string,"
        "\"decisions\": [string],"
        "\"action_items\": [string],"
        "\"open_questions\": [string],"
        "\"follow_up_topics\": [string],"
        "\"tools_mentioned\": [string],"
        "\"recommended_tags\": [string]"
        "}\n"
        "Rules:\n"
        "- Be factual and concise.\n"
        "- Do not invent decisions or action items that are not supported.\n"
        "- If something is missing, return an empty list.\n"
        "- No markdown fences."
    )


def extract_transcript_text(text: str, *, title: str, source_url: str = "") -> dict[str, Any]:
    user_prompt = (
        f"Transcript title: {title}\n"
        f"Video URL: {source_url or 'Unknown'}\n\n"
        "Transcript:\n"
        f"{text}"
    )
    data = _prompt_json(_transcript_system_prompt(), user_prompt, max_tokens=1800)
    return {
        "summary": data.get("summary", "").strip(),
        "timestamp_highlights": [
            {
                "timestamp": (item.get("timestamp") or "").strip(),
                "label": (item.get("label") or "").strip(),
            }
            for item in data.get("timestamp_highlights", [])
            if (item.get("timestamp") or "").strip() and (item.get("label") or "").strip()
        ][:8],
        "key_points": _limit(data.get("key_points", []), 8),
        "reusable_claims": _limit(data.get("reusable_claims", []), 8),
        "questions_raised": _limit(data.get("questions_raised", []), 6),
        "action_ideas": _limit(data.get("action_ideas", []), 6),
        "tools_mentioned": _limit(data.get("tools_mentioned", []), 10),
        "recommended_tags": _limit(data.get("recommended_tags", []), 10),
    }


def extract_meeting_text(text: str, *, title: str, date_value: str = "", attendees: str = "") -> dict[str, Any]:
    user_prompt = (
        f"Meeting title: {title}\n"
        f"Meeting date: {date_value or 'Unknown'}\n"
        f"Attendees: {attendees or 'Unknown'}\n\n"
        "Meeting notes/transcript:\n"
        f"{text}"
    )
    data = _prompt_json(_meeting_system_prompt(), user_prompt, max_tokens=1400)
    return {
        "summary": data.get("summary", "").strip(),
        "decisions": _limit(data.get("decisions", []), 8),
        "action_items": _limit(data.get("action_items", []), 8),
        "open_questions": _limit(data.get("open_questions", []), 8),
        "follow_up_topics": _limit(data.get("follow_up_topics", []), 8),
        "tools_mentioned": _limit(data.get("tools_mentioned", []), 10),
        "recommended_tags": _limit(data.get("recommended_tags", []), 10),
    }


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


def build_transcript_extract_artifact(
    transcript_path: Path,
    *,
    title: str,
    source_url: str,
) -> dict[str, Any]:
    transcript_path = transcript_path.resolve()
    text = _read_text(transcript_path)
    extracted = extract_transcript_text(text, title=title, source_url=source_url)
    output_path = save_transcript_extract(
        transcript_path,
        title=title,
        source_url=source_url,
        extracted=extracted,
    )
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
        "status": "created",
        "type": "transcript-extract",
        "title": title,
        "source": str(transcript_path),
        "path": str(output_path),
        "knowledge_base_id": index_result["knowledge_base_id"],
        "file_id": index_result["file_id"],
        "summary": extracted.get("summary", ""),
        "timestamp_highlights": extracted.get("timestamp_highlights", []),
        "key_points": extracted.get("key_points", []),
        "reusable_claims": extracted.get("reusable_claims", []),
        "questions_raised": extracted.get("questions_raised", []),
        "action_ideas": extracted.get("action_ideas", []),
        "tools_mentioned": extracted.get("tools_mentioned", []),
        "recommended_tags": extracted.get("recommended_tags", []),
    }


def cmd_transcript(args: argparse.Namespace) -> int:
    transcript_path = Path(args.file)
    if not transcript_path.is_absolute():
        transcript_path = PKA_ROOT / transcript_path
    if not transcript_path.exists():
        print(json.dumps({"error": f"Transcript file not found: {transcript_path}"}), file=sys.stderr)
        return 1
    text = _read_text(transcript_path)
    title = args.title or _extract_first_heading(text) or transcript_path.stem
    source_url = args.url or _extract_video_url(text)
    result = build_transcript_extract_artifact(
        transcript_path,
        title=title,
        source_url=source_url,
    )
    print(json.dumps(result, indent=2))
    return 0


def cmd_meeting(args: argparse.Namespace) -> int:
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
    date_value = args.date or ""
    attendees = args.attendees or ""
    extracted = extract_meeting_text(text, title=title, date_value=date_value, attendees=attendees)
    output_path = save_meeting_extract(
        source_path=source_path,
        title=title,
        date_value=date_value,
        attendees=attendees,
        extracted=extracted,
    )
    summary = extracted.get("summary", "") or f"Structured meeting extract for {title}."
    index_result = index_markdown_artifact(
        output_path,
        title=f"{title} — Meeting Extract",
        category="meeting-extract",
        tags=["meeting-extract", "meeting"] + extracted.get("recommended_tags", []),
        summary=summary,
    )
    if args.meeting_id is not None:
        _update_meeting_row(args.meeting_id, extracted, output_path)
    result = {
        "status": "created",
        "type": "meeting-extract",
        "title": title,
        "path": str(output_path),
        "source": str(source_path) if source_path else None,
        "meeting_id": args.meeting_id,
        "knowledge_base_id": index_result["knowledge_base_id"],
        "file_id": index_result["file_id"],
        "summary": extracted.get("summary", ""),
        "decisions": extracted.get("decisions", []),
        "action_items": extracted.get("action_items", []),
        "open_questions": extracted.get("open_questions", []),
        "follow_up_topics": extracted.get("follow_up_topics", []),
        "tools_mentioned": extracted.get("tools_mentioned", []),
        "recommended_tags": extracted.get("recommended_tags", []),
    }
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create structured extracts from transcripts and meetings")
    sub = parser.add_subparsers(dest="command", required=True)

    p_transcript = sub.add_parser("transcript", help="Extract structured notes from a transcript markdown file")
    p_transcript.add_argument("--file", required=True, help="Transcript markdown file")
    p_transcript.add_argument("--title", help="Override title")
    p_transcript.add_argument("--url", help="Override source URL")

    p_meeting = sub.add_parser("meeting", help="Extract structured notes from meeting notes or transcript")
    p_meeting.add_argument("--file", help="Meeting notes/transcript file")
    p_meeting.add_argument("--text", help="Inline meeting notes/transcript")
    p_meeting.add_argument("--title", help="Meeting title")
    p_meeting.add_argument("--date", help="Meeting date")
    p_meeting.add_argument("--attendees", help="Comma-separated attendees")
    p_meeting.add_argument("--meeting-id", type=int, help="Existing meetings.id to update")

    args = parser.parse_args()
    try:
        if args.command == "transcript":
            return cmd_transcript(args)
        if args.command == "meeting":
            return cmd_meeting(args)
    except (llm.LLMError, lmstudio.LMStudioError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"Could not parse structured extraction JSON: {exc}"}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
