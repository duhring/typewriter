#!/usr/bin/env python3
"""
Lightweight local memory retrieval for PKA.

Indexes text from:
- knowledge_base
- journal_entries
- meetings
- compiled wiki pages
- curated memory markdown files
- Discord note archive

Then retrieves the most relevant prior context for a new request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import lmstudio


PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
DISCORD_DIR = PKA_ROOT / "team-inbox" / "discord"
OWNERS_INBOX_DIR = PKA_ROOT / "owners-inbox"
WIKI_DIR = PKA_ROOT / "wiki"
_LEGACY_CURATED_MEMORY_DIR = (
    Path.home() / ".claude" / "projects" / "-Volumes-Home-Remote-Home-PKA" / "memory"
)


def _resolve_curated_memory_dir() -> Path:
    """Resolve this machine's curated-memory directory from machine config."""
    config_paths = [
        PKA_ROOT / "config" / "machine.local.json",
        PKA_ROOT / "config" / "machine.json",
    ]
    for config_path in config_paths:
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            configured = (config.get("paths") or {}).get("curated_memory_dir")
            if configured:
                return Path(configured).expanduser()
        except (OSError, ValueError):
            continue
    return _LEGACY_CURATED_MEMORY_DIR


CURATED_MEMORY_DIR = _resolve_curated_memory_dir()

MAX_CHUNK_CHARS = 1200
CHUNK_OVERLAP = 160
MAX_DISCORD_NOTES = 400
MIN_DISCORD_BODY_CHARS = 20

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of",
    "on", "or", "our", "so", "that", "the", "their", "them", "this", "to",
    "use", "was", "we", "what", "with", "you", "your",
}

FOLLOWUP_MARKERS = (
    "earlier",
    "yesterday",
    "from before",
    "last time",
    "we just",
    "remember",
    "that thing",
    "the one from",
)


@dataclass
class Chunk:
    source_kind: str
    source_group: str
    source_key: str
    chunk_index: int
    title: str
    text_content: str
    source_path: str | None
    metadata_json: str
    content_hash: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_kind TEXT NOT NULL,
            source_group TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            text_content TEXT NOT NULL,
            source_path TEXT,
            metadata_json TEXT,
            content_hash TEXT NOT NULL,
            embedding_json TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_chunks_group ON memory_chunks(source_group)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_chunks_kind ON memory_chunks(source_kind)"
    )
    conn.commit()


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _excerpt(text: str, limit: int = 280) -> str:
    compact = _normalize_space(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _tokenize(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]{2,}", text.lower()))
    return {token for token in tokens if token not in STOPWORDS}


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + max_chars)
        if end < length:
            split_at = text.rfind("\n\n", start, end)
            if split_at == -1:
                split_at = text.rfind(". ", start, end)
            if split_at != -1 and split_at > start + max_chars // 2:
                end = split_at + (2 if text[split_at:split_at + 2] == ". " else 0)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= length:
            break
        start = max(0, end - overlap)
    return chunks


def _hash_payload(title: str, text_content: str, metadata_json: str) -> str:
    digest = hashlib.sha256()
    digest.update(title.encode("utf-8"))
    digest.update(b"\n")
    digest.update(text_content.encode("utf-8"))
    digest.update(b"\n")
    digest.update(metadata_json.encode("utf-8"))
    return digest.hexdigest()


def _make_chunks(
    *,
    source_kind: str,
    source_group: str,
    title: str,
    text_content: str,
    source_path: str | None,
    metadata: dict,
) -> list[Chunk]:
    metadata_json = json.dumps(metadata, sort_keys=True)
    parts = _chunk_text(text_content)
    chunks: list[Chunk] = []
    for index, part in enumerate(parts):
        source_key = f"{source_group}:{index}"
        chunks.append(
            Chunk(
                source_kind=source_kind,
                source_group=source_group,
                source_key=source_key,
                chunk_index=index,
                title=title,
                text_content=part,
                source_path=source_path,
                metadata_json=metadata_json,
                content_hash=_hash_payload(title, part, metadata_json),
            )
        )
    return chunks


def _iter_knowledge_base(conn: sqlite3.Connection) -> Iterable[Chunk]:
    rows = conn.execute(
        """
        SELECT id, title, content, category, tags, source_file, created_at, updated_at, source_url
        FROM knowledge_base
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        kb_id, title, content, category, tags, source_file, created_at, updated_at, source_url = row
        body_parts = [title]
        if category:
            body_parts.append(f"Category: {category}")
        if tags:
            body_parts.append(f"Tags: {tags}")
        if content:
            body_parts.append(content)
        text_content = "\n\n".join(part for part in body_parts if part)
        metadata = {
            "created_at": created_at,
            "updated_at": updated_at,
            "category": category,
            "tags": tags,
            "source_url": source_url,
        }
        yield from _make_chunks(
            source_kind="knowledge_base",
            source_group=f"knowledge_base:{kb_id}",
            title=title,
            text_content=text_content,
            source_path=source_file,
            metadata=metadata,
        )


def _iter_journal_entries(conn: sqlite3.Connection) -> Iterable[Chunk]:
    rows = conn.execute(
        """
        SELECT id, title, summary, content, mood, energy_score, tags, created_at
        FROM journal_entries
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        entry_id, title, summary, content, mood, energy_score, tags, created_at = row
        body_parts = [title]
        if summary:
            body_parts.append(summary)
        if mood:
            body_parts.append(f"Mood: {mood}")
        if energy_score is not None:
            body_parts.append(f"Energy: {energy_score}/10")
        if tags:
            body_parts.append(f"Tags: {tags}")
        if content:
            body_parts.append(content)
        text_content = "\n\n".join(part for part in body_parts if part)
        metadata = {
            "created_at": created_at,
            "mood": mood,
            "energy_score": energy_score,
            "tags": tags,
        }
        yield from _make_chunks(
            source_kind="journal_entry",
            source_group=f"journal_entry:{entry_id}",
            title=title,
            text_content=text_content,
            source_path=None,
            metadata=metadata,
        )


def _iter_meetings(conn: sqlite3.Connection) -> Iterable[Chunk]:
    rows = conn.execute(
        """
        SELECT id, title, date, attendees, notes, action_items, calendar_id, calendar_event_id
        FROM meetings
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        meeting_id, title, date_value, attendees, notes, action_items, calendar_id, event_id = row
        body_parts = [title]
        if date_value:
            body_parts.append(f"Date: {date_value}")
        if attendees:
            body_parts.append(f"Attendees: {attendees}")
        if notes:
            body_parts.append(notes)
        if action_items:
            body_parts.append(f"Action items: {action_items}")
        text_content = "\n\n".join(part for part in body_parts if part)
        metadata = {
            "created_at": date_value,
            "attendees": attendees,
            "calendar_id": calendar_id,
            "calendar_event_id": event_id,
        }
        yield from _make_chunks(
            source_kind="meeting",
            source_group=f"meeting:{meeting_id}",
            title=title,
            text_content=text_content,
            source_path=None,
            metadata=metadata,
        )


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("\n")
    metadata: dict[str, str] = {}
    end_index = None
    for index, line in enumerate(parts[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    if end_index is None:
        return {}, text.strip()
    body = "\n".join(parts[end_index + 1:]).strip()
    return metadata, body


def _infer_markdown_title(path: Path, body: str, metadata: dict[str, str]) -> str:
    if metadata.get("title"):
        return metadata["title"]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem.replace("-", " ")


def _owner_doc_category(path: Path) -> str:
    rel_parts = path.relative_to(OWNERS_INBOX_DIR).parts
    if len(rel_parts) <= 1:
        return "owner-document"
    return rel_parts[0]


def _iter_discord_notes() -> Iterable[Chunk]:
    note_files = sorted(
        [
            path
            for path in DISCORD_DIR.iterdir()
            if path.is_file() and (path.name.endswith("_note.md") or path.name.endswith("_reply.md"))
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:MAX_DISCORD_NOTES]
    for note_path in note_files:
        text = note_path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(text)
        if len(_normalize_space(body)) < MIN_DISCORD_BODY_CHARS:
            continue
        message_id = metadata.get("message_id") or note_path.stem
        author = metadata.get("author", "unknown")
        timestamp = metadata.get("timestamp")
        title = _excerpt(body, limit=70) or f"Discord note by {author}"
        metadata["source_path"] = str(note_path)
        prefixed_body = body
        if author:
            prefixed_body = f"Author: {author}\n\n{prefixed_body}"
        yield from _make_chunks(
            source_kind="discord_note",
            source_group=f"discord_note:{message_id}",
            title=title,
            text_content=prefixed_body,
            source_path=str(note_path),
            metadata={
                "timestamp": timestamp,
                "author": author,
                "message_id": metadata.get("message_id"),
            },
        )


def _iter_owner_markdown_files() -> Iterable[Chunk]:
    if not OWNERS_INBOX_DIR.exists():
        return
    for path in sorted(OWNERS_INBOX_DIR.rglob("*.md")):
        if any(part.startswith(".") for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(text)
        text_body = body or text.strip()
        if len(_normalize_space(text_body)) < 40:
            continue
        rel_path = str(path.relative_to(PKA_ROOT))
        title = _infer_markdown_title(path, text_body, metadata)
        category = _owner_doc_category(path)
        metadata_payload = {
            "category": category,
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "source_path": rel_path,
        }
        body_parts = [title, f"Archive category: {category}", text_body]
        yield from _make_chunks(
            source_kind="owner_document",
            source_group=f"owner_document:{rel_path}",
            title=title,
            text_content="\n\n".join(part for part in body_parts if part),
            source_path=rel_path,
            metadata=metadata_payload,
        )


def _iter_wiki_pages() -> Iterable[Chunk]:
    if not WIKI_DIR.exists():
        return
    for path in sorted(WIKI_DIR.rglob("*.md")):
        if any(part.startswith(".") for part in path.parts):
            continue
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(text)
        text_body = body or text.strip()
        if len(_normalize_space(text_body)) < 40:
            continue
        rel_path = str(path.relative_to(PKA_ROOT))
        title = _infer_markdown_title(path, text_body, metadata)
        page_type = metadata.get("type") or "wiki"
        status = metadata.get("status")
        source_ids = metadata.get("source_kb_ids")
        body_parts = [title, f"Compiled wiki page type: {page_type}"]
        if status:
            body_parts.append(f"Status: {status}")
        if source_ids:
            body_parts.append(f"Source KB IDs: {source_ids}")
        body_parts.append(text_body)
        yield from _make_chunks(
            source_kind="wiki_page",
            source_group=f"wiki_page:{rel_path}",
            title=title,
            text_content="\n\n".join(part for part in body_parts if part),
            source_path=rel_path,
            metadata={
                "type": page_type,
                "status": status,
                "last_compiled": metadata.get("last_compiled"),
                "confidence": metadata.get("confidence"),
                "source_path": rel_path,
            },
        )


def _iter_curated_memories() -> Iterable[Chunk]:
    if not CURATED_MEMORY_DIR.exists():
        return
    for memory_path in sorted(CURATED_MEMORY_DIR.glob("*.md")):
        if memory_path.name == "MEMORY.md":
            continue
        text = memory_path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(text)
        title = metadata.get("title") or memory_path.stem.replace("-", " ")
        description = metadata.get("description")
        body_parts = [title]
        if description:
            body_parts.append(description)
        if body:
            body_parts.append(body)
        yield from _make_chunks(
            source_kind="curated_memory",
            source_group=f"curated_memory:{memory_path.stem}",
            title=title,
            text_content="\n\n".join(part for part in body_parts if part),
            source_path=str(memory_path),
            metadata={
                "description": description,
                "created_at": metadata.get("created_at"),
                "updated_at": metadata.get("updated_at"),
            },
        )


def collect_chunks(conn: sqlite3.Connection) -> list[Chunk]:
    chunks: list[Chunk] = []
    chunks.extend(_iter_knowledge_base(conn))
    chunks.extend(_iter_journal_entries(conn))
    chunks.extend(_iter_meetings(conn))
    chunks.extend(_iter_wiki_pages())
    chunks.extend(_iter_discord_notes())
    chunks.extend(_iter_owner_markdown_files())
    chunks.extend(_iter_curated_memories())
    return chunks


def sync_memory(conn: sqlite3.Connection) -> dict[str, int]:
    _ensure_schema(conn)
    existing = {
        row[0]: (row[1], row[2] or "")
        for row in conn.execute(
            "SELECT source_key, content_hash, embedding_json FROM memory_chunks"
        )
    }

    all_chunks = collect_chunks(conn)
    seen_keys = {chunk.source_key for chunk in all_chunks}
    pending: list[Chunk] = []
    unchanged = 0

    for chunk in all_chunks:
        current = existing.get(chunk.source_key)
        if current and current[0] == chunk.content_hash and current[1]:
            unchanged += 1
            continue
        pending.append(chunk)

    if pending:
        vectors = lmstudio.embed_texts(chunk.text_content for chunk in pending)
        for chunk, vector in zip(pending, vectors):
            conn.execute(
                """
                INSERT INTO memory_chunks (
                    source_kind, source_group, source_key, chunk_index, title,
                    text_content, source_path, metadata_json, content_hash,
                    embedding_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    source_kind=excluded.source_kind,
                    source_group=excluded.source_group,
                    chunk_index=excluded.chunk_index,
                    title=excluded.title,
                    text_content=excluded.text_content,
                    source_path=excluded.source_path,
                    metadata_json=excluded.metadata_json,
                    content_hash=excluded.content_hash,
                    embedding_json=excluded.embedding_json,
                    updated_at=excluded.updated_at
                """,
                (
                    chunk.source_kind,
                    chunk.source_group,
                    chunk.source_key,
                    chunk.chunk_index,
                    chunk.title,
                    chunk.text_content,
                    chunk.source_path,
                    chunk.metadata_json,
                    chunk.content_hash,
                    json.dumps(vector),
                    _utc_now(),
                ),
            )

    stale_keys = [key for key in existing.keys() if key not in seen_keys]
    if stale_keys:
        conn.executemany(
            "DELETE FROM memory_chunks WHERE source_key = ?",
            ((key,) for key in stale_keys),
        )

    conn.commit()

    return {
        "total_chunks": len(all_chunks),
        "updated_chunks": len(pending),
        "unchanged_chunks": unchanged,
        "deleted_chunks": len(stale_keys),
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _keyword_overlap(query: str, text: str) -> float:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    text_tokens = _tokenize(text)
    if not text_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens)


def _recency_bonus(metadata: dict) -> float:
    raw_value = (
        metadata.get("timestamp")
        or metadata.get("created_at")
        or metadata.get("updated_at")
        or metadata.get("last_compiled")
    )
    if not raw_value:
        return 0.0
    try:
        normalized = raw_value.replace("Z", "+00:00")
        moment = datetime.fromisoformat(normalized)
    except ValueError:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds() / 86400)
    if age_days <= 2:
        return 0.07
    if age_days <= 7:
        return 0.045
    if age_days <= 30:
        return 0.02
    return 0.0


def _source_bonus(source_kind: str) -> float:
    if source_kind == "wiki_page":
        return 0.08
    if source_kind == "discord_note":
        return 0.06
    if source_kind == "journal_entry":
        return 0.03
    if source_kind == "owner_document":
        return 0.02
    if source_kind == "curated_memory":
        return 0.015
    return 0.0


def _followup_bonus(query: str, source_kind: str) -> float:
    lowered = query.lower()
    if not any(marker in lowered for marker in FOLLOWUP_MARKERS):
        return 0.0
    if source_kind == "discord_note":
        return 0.08
    if source_kind == "wiki_page":
        return 0.06
    if source_kind == "journal_entry":
        return 0.04
    if source_kind == "curated_memory":
        return 0.02
    return 0.0


def _intent_bonus(source_kind: str, metadata: dict, intent: str) -> float:
    if intent != "writing":
        return 0.0

    category = (metadata.get("category") or "").lower()
    bonus = 0.0

    if source_kind == "owner_document":
        bonus += 0.09
    elif source_kind == "wiki_page":
        bonus += 0.12
    elif source_kind == "knowledge_base":
        bonus += 0.05
    elif source_kind == "discord_note":
        bonus += 0.02
    elif source_kind == "journal_entry":
        bonus += 0.015

    if category in {"blog", "youtube", "research", "transcript", "transcripts", "linkedin", "social"}:
        bonus += 0.04
    elif category == "presentation":
        bonus += 0.015

    return bonus


def recall(conn: sqlite3.Connection, query: str, *, limit: int = 4, intent: str = "general") -> list[dict]:
    _ensure_schema(conn)
    sync_memory(conn)

    rows = conn.execute(
        """
        SELECT source_kind, source_group, source_key, title, text_content, source_path, metadata_json, embedding_json
        FROM memory_chunks
        WHERE embedding_json IS NOT NULL
        """
    ).fetchall()
    if not rows:
        return []

    query_vector = lmstudio.embed_texts([query])[0]
    candidates: list[dict] = []
    for source_kind, source_group, source_key, title, text_content, source_path, metadata_json, embedding_json in rows:
        metadata = json.loads(metadata_json or "{}")
        vector = json.loads(embedding_json)
        semantic = _cosine_similarity(query_vector, vector)
        lexical = _keyword_overlap(query, f"{title}\n{text_content}")
        score = (
            (semantic * 0.78)
            + (lexical * 0.14)
            + _recency_bonus(metadata)
            + _source_bonus(source_kind)
            + _followup_bonus(query, source_kind)
            + _intent_bonus(source_kind, metadata, intent)
        )
        candidates.append(
            {
                "source_kind": source_kind,
                "source_group": source_group,
                "source_key": source_key,
                "title": title,
                "text_content": text_content,
                "source_path": source_path,
                "metadata": metadata,
                "score": round(score, 6),
            }
        )

    best_by_group: dict[str, dict] = {}
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        group = candidate["source_group"]
        if group not in best_by_group:
            best_by_group[group] = candidate

    unique_hits: list[dict] = []
    seen_signatures: set[tuple[str, str]] = set()
    for hit in sorted(best_by_group.values(), key=lambda item: item["score"], reverse=True):
        signature = (hit["source_kind"], _normalize_space(hit["text_content"])[:220])
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        unique_hits.append(hit)
        if len(unique_hits) >= limit:
            break
    return unique_hits


def _label_for_hit(hit: dict) -> str:
    metadata = hit["metadata"]
    source_kind = hit["source_kind"]
    title = hit.get("title") or "Untitled"
    if source_kind == "discord_note":
        timestamp = metadata.get("timestamp", "unknown time")
        author = metadata.get("author", "unknown")
        return f"Discord note — {timestamp} by {author}"
    if source_kind == "journal_entry":
        return f"Journal entry — {title}"
    if source_kind == "knowledge_base":
        return f"Knowledge base — {title}"
    if source_kind == "wiki_page":
        page_type = hit["metadata"].get("type", "wiki")
        return f"Compiled wiki ({page_type}) — {title}"
    if source_kind == "meeting":
        return f"Meeting — {title}"
    if source_kind == "curated_memory":
        return f"Curated memory — {title}"
    if source_kind == "owner_document":
        category = hit["metadata"].get("category", "archive")
        return f"Archive file ({category}) — {title}"
    return title


def format_prompt_block(hits: list[dict], *, intent: str = "general") -> str:
    if not hits:
        return ""
    if intent == "writing":
        lines = [
            "[Relevant archive context for drafting. Review these prior materials before writing. Use them to borrow recurring ideas, voice, examples, and claims when helpful, but do not force references that do not fit the current piece. If you delegate drafting to Reed, Maven, or Pax, pass this archive context or its key points along.]",
        ]
    else:
        lines = [
            "[Relevant prior context from PKA memory. Use this only if it genuinely helps resolve references to earlier conversations, notes, or work. If these items materially shape your answer, include one short grounding phrase near the start, for example: 'Based on the note you sent earlier today...' or 'Pulling from the earlier CueCam thread...']",
        ]
    for index, hit in enumerate(hits, start=1):
        lines.append(f"{index}. {_label_for_hit(hit)}")
        lines.append(f"   Excerpt: {_excerpt(hit['text_content'])}")
        if hit.get("source_path"):
            lines.append(f"   Source: {hit['source_path']}")
    return "\n".join(lines)


def status(conn: sqlite3.Connection) -> dict:
    _ensure_schema(conn)
    total = conn.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0]
    by_kind = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT source_kind, COUNT(*) FROM memory_chunks GROUP BY source_kind ORDER BY source_kind"
        )
    }
    last_updated = conn.execute("SELECT MAX(updated_at) FROM memory_chunks").fetchone()[0]
    return {
        "total_chunks": total,
        "by_kind": by_kind,
        "last_updated": last_updated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve prior context from PKA memory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync", help="Refresh the local memory chunk index")
    subparsers.add_parser("status", help="Show current chunk counts")

    recall_parser = subparsers.add_parser("recall", help="Recall prior context for a query")
    recall_parser.add_argument("--query", help="Query text. Reads stdin if omitted.")
    recall_parser.add_argument("--limit", type=int, default=4)
    recall_parser.add_argument(
        "--intent",
        choices=("general", "writing"),
        default="general",
        help="Ranking/prompt style",
    )
    recall_parser.add_argument(
        "--format",
        choices=("prompt", "json"),
        default="prompt",
        help="Output format",
    )

    args = parser.parse_args()
    query = getattr(args, "query", None)
    if args.command == "recall" and query is None:
        query = sys.stdin.read().strip()
    if args.command == "recall" and not query:
        print("A query is required.", file=sys.stderr)
        return 1

    try:
        conn = _open_db(args.command)
        if args.command == "sync":
            print(json.dumps(sync_memory(conn), indent=2))
            return 0
        if args.command == "status":
            print(json.dumps(status(conn), indent=2))
            return 0
        if args.command == "recall":
            hits = recall(conn, query, limit=args.limit, intent=args.intent)
            if args.format == "json":
                print(json.dumps({"query": query, "hits": hits}, indent=2))
            else:
                print(format_prompt_block(hits, intent=args.intent))
            return 0
    except lmstudio.LMStudioError as exc:
        print(f"Local memory retrieval could not reach LM Studio: {exc}", file=sys.stderr)
        return 1
    finally:
        if "conn" in locals():
            conn.close()

    return 0


def _open_db(command: str) -> sqlite3.Connection:
    """Open writable storage only for the guarded sync operation."""
    if command == "sync":
        from machine_role import guard_connection

        return guard_connection(
            sqlite3.connect(str(DB_PATH)),
            "memory retrieval sync",
        )
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


if __name__ == "__main__":
    raise SystemExit(main())
