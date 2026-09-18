#!/usr/bin/env python3
"""
Helpers and CLI for indexing durable PKA artifacts.

This keeps `knowledge_base` and `files` aligned with important saved outputs.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from records import upsert_record


PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
DISCORD_DIR = PKA_ROOT / "team-inbox" / "discord"
PRESENTATIONS_DIR = PKA_ROOT / "owners-inbox" / "presentations"
TRANSCRIPTS_DIR = PKA_ROOT / "owners-inbox" / "transcripts"
CATEGORIES_FILE = PKA_ROOT / "data" / "categories.json"


def _registered_categories() -> set[str]:
    """Return the set of registered category names from data/categories.json."""
    try:
        raw = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
        return {k for k in raw if not k.startswith("_")}
    except Exception:
        return set()


def _warn_if_unregistered(category: str) -> None:
    """Print a stderr warning when indexing with an unregistered category."""
    registered = _registered_categories()
    if registered and category not in registered:
        print(
            f"⚠️  Category '{category}' is not in data/categories.json.\n"
            f"   Add it there to keep the health digest clean.",
            file=sys.stderr,
        )


def _connect() -> sqlite3.Connection:
    # Single-writer rule (docs/devendorf-contract.md): satellites must not
    # write shared state. _connect() is the chokepoint for every write path.
    from machine_role import require_writer
    require_writer("indexing")
    return sqlite3.connect(str(DB_PATH))


def _rel_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PKA_ROOT))
    except ValueError:
        return str(resolved)


def _infer_file_type(path: Path) -> str:
    if path.is_dir():
        return path.suffix or "directory"
    return path.suffix.lower() or "file"


def _normalize_tags(tags: list[str] | tuple[str, ...] | str | None) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        return tags
    cleaned = []
    seen = set()
    for tag in tags:
        normalized = tag.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    return ", ".join(cleaned)


def _excerpt(text: str, limit: int = 320) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _extract_markdown_title(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def upsert_file_record(
    path: Path,
    *,
    category: str,
    description: str = "",
    file_type: Optional[str] = None,
    ocr_text: Optional[str] = None,
) -> int:
    path = path.resolve()
    filepath = _rel_path(path)
    filename = path.name
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM files WHERE filepath = ?",
            (filepath,),
        ).fetchone()
        payload = (
            filename,
            filepath,
            category,
            description,
            file_type or _infer_file_type(path),
            ocr_text,
        )
        if row:
            conn.execute(
                """
                UPDATE files
                SET filename = ?, category = ?, description = ?, file_type = ?, ocr_text = ?
                WHERE id = ?
                """,
                (filename, category, description, file_type or _infer_file_type(path), ocr_text, row[0]),
            )
            return int(row[0])
        cursor = conn.execute(
            """
            INSERT INTO files (filename, filepath, category, description, file_type, ocr_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        return int(cursor.lastrowid)


def upsert_knowledge_base(
    *,
    title: str,
    content: str,
    category: str,
    tags: list[str] | tuple[str, ...] | str | None,
    source_file: str,
    source_url: Optional[str] = None,
    confidence: str = "medium",
) -> int:
    normalized_tags = _normalize_tags(tags)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM knowledge_base WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE knowledge_base
                SET title = ?, content = ?, category = ?, tags = ?, source_url = ?, confidence = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, content, category, normalized_tags, source_url, confidence, now, row[0]),
            )
            return int(row[0])
        cursor = conn.execute(
            """
            INSERT INTO knowledge_base (
                title, content, category, tags, source_file, created_at, updated_at, source_url, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, content, category, normalized_tags, source_file, now, now, source_url, confidence),
        )
        return int(cursor.lastrowid)


class GovernedRecordError(RuntimeError):
    """Raised when index rows are still referenced by a governed record."""


# Deletion is restricted to the categories that generate their own notes. Widen
# this only alongside a caller that owns the files it is deleting.
FORGETTABLE_CATEGORIES = ("video-media",)


def forget_markdown_artifact(
    path: Path,
    *,
    allowed_categories: Optional[tuple[str, ...]] = FORGETTABLE_CATEGORIES,
) -> dict:
    """Remove the index rows for a generated markdown note that is going away.

    Deleting the file alone leaves orphan `files` and `knowledge_base` rows,
    which surface later as kb_provenance failures. Deleting the `knowledge_base`
    row alone leaves dangling `kb_links` in both directions — SQLite foreign-key
    enforcement is off in this database, so nothing else catches that.

    Everything happens in one transaction: resolve the ids, refuse if a governed
    `records` row points at either of them, drop the links, then drop the rows.
    Pass allowed_categories=None to lift the category guard deliberately.
    """
    rel_path = _rel_path(path.resolve())
    with _connect() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        kb_row = conn.execute(
            "SELECT id, category FROM knowledge_base WHERE source_file = ?", (rel_path,)
        ).fetchone()
        file_row = conn.execute(
            "SELECT id FROM files WHERE filepath = ?", (rel_path,)
        ).fetchone()
        if not kb_row and not file_row:
            return {
                "source_file": rel_path,
                "knowledge_base_rows": 0,
                "file_rows": 0,
                "kb_links": 0,
            }

        kb_id = kb_row[0] if kb_row else None
        category = kb_row[1] if kb_row else None
        file_id = file_row[0] if file_row else None

        if allowed_categories is not None and category is not None:
            if category not in allowed_categories:
                raise ValueError(
                    f"Refusing to forget {rel_path}: category '{category}' is outside "
                    f"{list(allowed_categories)}."
                )

        governed = conn.execute(
            "SELECT record_key FROM records WHERE knowledge_base_id IS ? OR file_id IS ?",
            (kb_id, file_id),
        ).fetchall()
        if governed:
            raise GovernedRecordError(
                f"Refusing to forget {rel_path}: governed record(s) reference it "
                f"({', '.join(row[0] for row in governed)})."
            )

        links = 0
        if kb_id is not None:
            links = conn.execute(
                "DELETE FROM kb_links WHERE source_id = ? OR target_id = ?",
                (kb_id, kb_id),
            ).rowcount
        kb = conn.execute(
            "DELETE FROM knowledge_base WHERE source_file = ?", (rel_path,)
        ).rowcount
        files = conn.execute(
            "DELETE FROM files WHERE filepath = ?", (rel_path,)
        ).rowcount
    return {
        "source_file": rel_path,
        "knowledge_base_rows": kb,
        "file_rows": files,
        "kb_links": links,
    }


def index_markdown_artifact(
    path: Path,
    *,
    title: str,
    category: str,
    tags: list[str] | tuple[str, ...] | str | None = None,
    source_url: Optional[str] = None,
    summary: Optional[str] = None,
) -> dict:
    path = path.resolve()
    content = path.read_text(encoding="utf-8")
    rel_path = _rel_path(path)
    brief = summary or _excerpt(content, 320)
    file_id = upsert_file_record(
        path,
        category=category,
        description=brief,
        file_type=".md",
    )
    kb_id = upsert_knowledge_base(
        title=title,
        content=content,
        category=category,
        tags=tags,
        source_file=rel_path,
        source_url=source_url,
    )
    return {
        "file_id": file_id,
        "knowledge_base_id": kb_id,
        "path": rel_path,
        "title": title,
    }


def index_markdown_cli(
    path: Path,
    *,
    title: Optional[str],
    category: str,
    tags: Optional[str],
    source_url: Optional[str],
    summary: Optional[str],
    record_class: Optional[str] = None,
    record_series: Optional[str] = None,
    event_date: Optional[str] = None,
    subject: Optional[str] = None,
    verification_status: str = "unreviewed",
    retention_class: str = "manual-review",
    record_state: str = "active",
    sensitivity: str = "normal",
    record_notes: Optional[str] = None,
) -> dict:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Markdown file not found: {path}")
    if path.suffix.lower() != ".md":
        raise ValueError(f"Expected a markdown file, got: {path}")
    _warn_if_unregistered(category)
    actual_title = title or _extract_markdown_title(path) or path.stem
    tag_list = [item.strip() for item in (tags or "").split(",") if item.strip()] or None
    result = index_markdown_artifact(
        path,
        title=actual_title,
        category=category,
        tags=tag_list,
        source_url=source_url,
        summary=summary,
    )
    if record_class or record_series:
        if not (record_class and record_series):
            raise ValueError("record_class and record_series must be provided together")
        result["record"] = upsert_record(
            title=actual_title,
            record_class=record_class,
            record_series=record_series,
            event_date=event_date,
            subject=subject,
            verification_status=verification_status,
            retention_class=retention_class,
            record_state=record_state,
            sensitivity=sensitivity,
            file_id=result["file_id"],
            knowledge_base_id=result["knowledge_base_id"],
            source_path=result["path"],
            notes=record_notes,
        )
    return result


def _extract_headline_from_card(card: dict) -> Optional[str]:
    text = card.get("text") or ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            headline = line.lstrip("#").strip()
            if headline:
                return headline
    return None


def _extract_embedded_image_filenames(card: dict) -> set[str]:
    text = card.get("text") or ""
    found = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("! "):
            continue
        filename = line[2:].strip()
        if filename:
            found.add(filename)
    return found


def index_cuecam_bundle(
    bundle_path: Path,
    *,
    title: Optional[str] = None,
    cards: Optional[int] = None,
    images: Optional[int] = None,
    videos: Optional[int] = None,
) -> dict:
    bundle_path = bundle_path.resolve()
    script_path = bundle_path / "Script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"CueCam bundle missing Script.json: {bundle_path}")

    script = json.loads(script_path.read_text(encoding="utf-8"))
    card_rows = script.get("cards", [])
    headline_candidates = []
    inferred_videos = 0
    inferred_image_names: set[str] = set()
    for card in card_rows:
        headline = _extract_headline_from_card(card)
        if headline:
            headline_candidates.append(headline)
        slide = card.get("slide", {})
        if slide.get("mediaItem"):
            inferred_videos += 1
        for key in ("backgroundImageFilename", "imageFilename"):
            filename = slide.get(key)
            if filename:
                inferred_image_names.add(filename)
        inferred_image_names.update(_extract_embedded_image_filenames(card))

    actual_title = title or (headline_candidates[0] if headline_candidates else bundle_path.stem)
    actual_cards = cards if cards is not None else len(card_rows)
    actual_images = images if images is not None else len(inferred_image_names)
    actual_videos = videos if videos is not None else inferred_videos

    summary_parts = [f"CueCam presentation with {actual_cards} card"]
    if actual_cards != 1:
        summary_parts[0] += "s"
    media_bits = []
    if actual_images:
        media_bits.append(f"{actual_images} image")
        if actual_images != 1:
            media_bits[-1] += "s"
    if actual_videos:
        media_bits.append(f"{actual_videos} video")
        if actual_videos != 1:
            media_bits[-1] += "s"
    if media_bits:
        summary_parts.append("including " + " and ".join(media_bits))
    if headline_candidates:
        summary_parts.append("Key cards: " + ", ".join(headline_candidates[:4]))
    summary = ". ".join(summary_parts) + "."

    rel_path = _rel_path(bundle_path)
    file_id = upsert_file_record(
        bundle_path,
        category="presentation",
        description=summary,
        file_type=".cuecam",
    )
    kb_id = upsert_knowledge_base(
        title=actual_title,
        content=summary,
        category="presentation",
        tags=["cuecam", "presentation"],
        source_file=rel_path,
    )
    return {
        "file_id": file_id,
        "knowledge_base_id": kb_id,
        "path": rel_path,
        "title": actual_title,
    }


def index_transcript_file(
    transcript_path: Path,
    *,
    title: str,
    source_url: str,
    video_id: str,
) -> dict:
    transcript_path = transcript_path.resolve()
    result = index_markdown_artifact(
        transcript_path,
        title=f"{title} — YouTube Transcript",
        category="transcript",
        tags=["youtube", "transcript", video_id],
        source_url=source_url,
        summary=f"YouTube transcript for {title} ({video_id}).",
    )
    result["title"] = title
    return result


def index_discord_artifact(path: Path) -> dict:
    path = path.resolve()
    is_note = path.name.endswith("_note.md") or path.name.endswith("_reply.md")
    if path.name.endswith("_reply.md"):
        category = "discord-reply"
    else:
        category = "discord-note" if is_note else "discord-attachment"
    description = ""
    if is_note:
        text = path.read_text(encoding="utf-8")
        body = text.split("---", 2)[-1].strip() if text.startswith("---") else text
        description = _excerpt(body, 240)
    else:
        description = f"Discord attachment archived from #larry: {path.name}"
    file_id = upsert_file_record(
        path,
        category=category,
        description=description,
    )
    return {"file_id": file_id, "path": _rel_path(path), "category": category}


def backfill_cuecam() -> dict:
    indexed = 0
    for bundle_path in sorted(PRESENTATIONS_DIR.glob("*.cuecam")):
        if not bundle_path.is_dir():
            continue
        index_cuecam_bundle(bundle_path)
        indexed += 1
    return {"indexed_cuecam_bundles": indexed}


def backfill_discord() -> dict:
    indexed = 0
    for path in sorted(DISCORD_DIR.iterdir()):
        if path.name == "processed":
            continue
        if path.is_dir():
            continue
        index_discord_artifact(path)
        indexed += 1
    return {"indexed_discord_artifacts": indexed}


def backfill_transcripts() -> dict:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    indexed = 0
    for path in sorted(TRANSCRIPTS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        title = path.stem
        source_url = ""
        video_id = ""
        for line in lines[:6]:
            if line.startswith("# "):
                title = line[2:].strip()
            if line.startswith("**Video:** "):
                source_url = line.replace("**Video:** ", "", 1).strip()
                match = re.search(r"v=([\w-]{11})", source_url)
                if not match:
                    match = re.search(r"youtu\.be/([\w-]{11})", source_url)
                if match:
                    video_id = match.group(1)
        if not source_url:
            source_url = f"https://youtube.com/watch?v={video_id}" if video_id else ""
        index_transcript_file(
            path,
            title=title,
            source_url=source_url,
            video_id=video_id or path.stem,
        )
        indexed += 1
    return {"indexed_transcripts": indexed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Index durable PKA artifacts")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("backfill-cuecam", help="Index existing CueCam bundles")
    sub.add_parser("backfill-discord", help="Index existing Discord notes and attachments")
    sub.add_parser("backfill-transcripts", help="Index saved transcript markdown files")
    p_markdown = sub.add_parser("index-markdown", help="Index a markdown deliverable into files and knowledge_base")
    p_markdown.add_argument("--file", required=True, help="Markdown file to index")
    p_markdown.add_argument("--title", help="Override title; defaults to first markdown heading")
    p_markdown.add_argument("--category", required=True, help="knowledge_base/files category")
    p_markdown.add_argument("--tags", help="Comma-separated tags")
    p_markdown.add_argument("--source-url", help="Optional source URL")
    p_markdown.add_argument("--summary", help="Optional short description for files table")
    p_markdown.add_argument("--record-class", help="Optional governed record class")
    p_markdown.add_argument("--record-series", help="Optional governed record series")
    p_markdown.add_argument("--event-date", help="Optional record event date (YYYY-MM-DD)")
    p_markdown.add_argument("--subject", help="Optional record subject")
    p_markdown.add_argument(
        "--verification-status",
        default="unreviewed",
        help="Optional governed verification status",
    )
    p_markdown.add_argument(
        "--retention-class",
        default="manual-review",
        help="Optional governed retention class",
    )
    p_markdown.add_argument(
        "--record-state",
        default="active",
        help="Optional governed record state",
    )
    p_markdown.add_argument(
        "--sensitivity",
        default="normal",
        help="Optional governed sensitivity level",
    )
    p_markdown.add_argument("--record-notes", help="Optional governed record notes")

    args = parser.parse_args()
    if args.command == "backfill-cuecam":
        print(json.dumps(backfill_cuecam(), indent=2))
        return 0
    if args.command == "backfill-discord":
        print(json.dumps(backfill_discord(), indent=2))
        return 0
    if args.command == "backfill-transcripts":
        print(json.dumps(backfill_transcripts(), indent=2))
        return 0
    if args.command == "index-markdown":
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = PKA_ROOT / file_path
        print(
            json.dumps(
                index_markdown_cli(
                    file_path,
                    title=args.title,
                    category=args.category,
                    tags=args.tags,
                    source_url=args.source_url,
                    summary=args.summary,
                    record_class=args.record_class,
                    record_series=args.record_series,
                    event_date=args.event_date,
                    subject=args.subject,
                    verification_status=args.verification_status,
                    retention_class=args.retention_class,
                    record_state=args.record_state,
                    sensitivity=args.sensitivity,
                    record_notes=args.record_notes,
                ),
                indent=2,
            )
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
