#!/usr/bin/env python3
"""
Compile durable PKA source material into wiki pages.

The wiki is a generated context layer, not a source of truth. This tool starts
with project pages because canonical task records already connect DB state,
source artifacts, decisions, and open loops.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pka_index import index_markdown_artifact
from machine_role import SatelliteWriteError


PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
TASKS_DIR = PKA_ROOT / "owners-inbox" / "tasks"
WIKI_DIR = PKA_ROOT / "wiki"
PROJECTS_DIR = WIKI_DIR / "projects"
CONCEPTS_DIR = WIKI_DIR / "concepts"
PEOPLE_DIR = WIKI_DIR / "people"
OPERATIONS_DIR = WIKI_DIR / "operations"
VIDEO_PROJECTS_DIR = PKA_ROOT / "owners-inbox" / "video-projects"

GENERATED_CATEGORIES = {
    "dreamer",
    "session-artifacts",
}

GENERATED_SOURCE_PREFIXES = (
    "wiki/",
    "owners-inbox/dreamer-",
)


CONCEPT_SPECS = {
    "hybrid-context-layer": {
        "title": "Hybrid Context Layer",
        "summary": (
            "PKA's hybrid context layer keeps structured source data in SQLite while "
            "Dreamer compiles recurring knowledge into wiki pages for faster, deeper agent context."
        ),
        "keywords": [
            "hybrid-memory",
            "hybrid context",
            "openbrain",
            "karpathy",
            "wiki",
            "kb-links",
            "graph-database",
            "pka-architecture",
            "compiled context",
        ],
    },
    "hyperframes": {
        "title": "HyperFrames",
        "summary": (
            "HyperFrames is the CueCam post-production layer for timing visual overlays, "
            "motion graphics, and emphasis cues against spoken anchors."
        ),
        "keywords": [
            "hyperframes",
            "motion-graphics",
            "audio-triggers",
            "overlay",
            "post-production",
            "cuecam-hyperframes",
        ],
    },
    "cuecam-mobile-workflow": {
        "title": "CueCam Mobile Workflow",
        "summary": (
            "The CueCam mobile workflow turns iPhone and Discord field notes into "
            "presentation decks, scripts, media bundles, and publishable videos."
        ),
        "keywords": [
            "cuecam",
            "mobile",
            "discord",
            "iphone",
            "remote contribution",
            "presentation",
            "tour guide",
        ],
    },
    "pka-brand-voice": {
        "title": "PKA Brand Voice",
        "summary": (
            "PKA's brand voice guides Reed, Maven, and image generation toward the owner's "
            "plainspoken, builder-centered framing rather than generic AI content."
        ),
        "keywords": [
            "brand-system",
            "brand voice",
            "voice",
            "tone",
            "reed",
            "maven",
            "brand-system",
            "visual-identity",
        ],
    },
}


PERSON_SPECS = {
    "john-ittelson": {
        "title": "John Ittelson",
        "summary": (
            "John Ittelson is a relationship gateway for the Creator Lab content-extension "
            "thread, connecting the concept pitch toward Oliver Breidenbach and Robin Raskin."
        ),
        "follow_up": "Use John as the warm path for Creator Lab feedback and for reaching Oliver Breidenbach or Robin Raskin.",
        "keywords": [
            "John Ittelson",
            "john-ittelson",
            "Creator Lab",
            "Oliver Breidenbach",
            "Robin Raskin",
            "outreach",
            "content-strategy",
        ],
    },
    "oliver-breidenbach": {
        "title": "Oliver Breidenbach",
        "summary": (
            "Oliver Breidenbach is the Boinx Software / mimoLive collaborator whose decision "
            "on hosting Creator Lab blog content currently gates the editorial extension project."
        ),
        "follow_up": "Wait for or prompt Oliver's decision on whether Creator Lab blog content should live on the mimoLive site.",
        "keywords": [
            "Oliver Breidenbach",
            "oliver-breidenbach",
            "mimoLive",
            "Boinx Software",
            "Creator Lab",
            "NAB",
            "YouTube",
        ],
    },
    "robin-raskin": {
        "title": "Robin Raskin",
        "summary": (
            "Robin Raskin is the Creator Lab-facing partner for the editorial extension idea, "
            "with the program framed around turning event sessions into durable written assets."
        ),
        "follow_up": "Use Robin-facing language around Creator Lab becoming a year-round editorial surface, not only an event track.",
        "keywords": [
            "Robin Raskin",
            "robin-raskin",
            "Creator Lab",
            "NAB Show",
            "content strategy",
            "event publishing",
        ],
    },
    "kern-trembath": {
        "title": "Kern Trembath",
        "summary": (
            "Kern Trembath is tied to the CueCam customer-testimonial workflow through the "
            "HelioRoast video work and the recurring Naomi testimonial context."
        ),
        "follow_up": "Keep Kern context available for HelioRoast testimonial references and future CueCam customer-story examples.",
        "keywords": [
            "Kern Trembath",
            "Kern",
            "HelioRoast",
            "CueCam",
            "testimonial",
            "Naomi",
        ],
    },
    "naomi": {
        "title": "Naomi",
        "summary": (
            "Naomi is connected to the CueCam testimonial workflow as the customer voice in "
            "the HelioRoast recording with Kern Trembath."
        ),
        "follow_up": "Treat Naomi as testimonial/customer-story context unless a new HelioRoast follow-up appears.",
        "keywords": [
            "Naomi",
            "HelioRoast",
            "CueCam",
            "testimonial",
            "Kern Trembath",
        ],
    },
    "gabe-michael": {
        "title": "Gabe Michael",
        "summary": (
            "Gabe Michael is the Creator Lab proof-of-concept subject whose NAB session became "
            "the sample article for the content-extension pitch."
        ),
        "follow_up": "Use the Gabe article as the proof-of-concept artifact when pitching the Creator Lab editorial extension.",
        "keywords": [
            "Gabe Michael",
            "Gabe",
            "Creator Lab",
            "AI filmmaking",
            "NAB Show",
            "generative AI",
        ],
    },
}


@dataclass
class ProjectSource:
    project_id: int
    name: str
    description: str
    status: str
    priority: str
    updated_at: str | None
    task_path: Path | None
    task_frontmatter: dict[str, str]
    task_sections: dict[str, str]


@dataclass
class ConceptSource:
    slug: str
    title: str
    summary: str
    keywords: list[str]


@dataclass
class PersonSource:
    slug: str
    name: str
    summary: str
    keywords: list[str]
    contact_id: int | None
    role: str
    company: str
    notes: str
    contact_summary: str
    follow_up: str


@dataclass
class WikiPage:
    path: Path
    frontmatter: dict[str, Any]
    body: str


@dataclass
class AuditFinding:
    kind: str
    severity: str
    page: str
    detail: str
    recommendation: str


@dataclass
class RepairAction:
    action: str
    status: str
    page: str
    detail: str


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _slugify(text: str, fallback: str = "page") -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:80] or fallback


def _rel_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PKA_ROOT))
    except ValueError:
        return str(resolved)


def _parse_sectioned_markdown(text: str) -> tuple[dict[str, str], dict[str, str]]:
    frontmatter: dict[str, str] = {}
    body = text
    if text.startswith("---\n"):
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            front, body = parts
            for line in front.splitlines()[1:]:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip()

    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return frontmatter, sections


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, text
    front, body = parts
    metadata: dict[str, Any] = {}
    current_key: str | None = None
    for line in front.splitlines()[1:]:
        if not line.strip():
            continue
        if line.startswith("  - ") and current_key:
            current = metadata.setdefault(current_key, [])
            if isinstance(current, list):
                current.append(line[4:].strip())
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        current_key = key
        if value == "[]":
            metadata[key] = []
        elif value:
            metadata[key] = value.strip('"')
        else:
            metadata[key] = []
    return metadata, body


def _parse_bullets(section_text: str) -> list[str]:
    items: list[str] = []
    for line in (section_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value.lower() != "none.":
                items.append(value)
    return items


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean_source_path(value: Any) -> str:
    text = str(value or "").strip().strip("`")
    url_match = re.search(r"https?://\S+", text)
    if url_match:
        return url_match.group(0).rstrip(").,")
    text = re.sub(r"\s+\(.*\)$", "", text).strip()
    text = text.strip("*")
    return text


def _is_pending_or_note_source(value: Any) -> bool:
    text = str(value or "").lower()
    return (
        "pending owner export" in text
        or "pending export" in text
        or "pending owner" in text
    )


def _source_path_exists(source_path: str) -> bool:
    if not source_path:
        return False
    if re.match(r"https?://", source_path):
        return True
    path = Path(source_path)
    if path.is_absolute():
        return path.exists()
    return (PKA_ROOT / path).exists()


def _date_age_days(date_text: str | None) -> int | None:
    if not date_text:
        return None
    try:
        moment = datetime.fromisoformat(str(date_text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0, int((now - moment.astimezone(timezone.utc)).total_seconds() // 86400))


def _clean_section_text(section_text: str) -> str:
    return " ".join((section_text or "").split()).strip()


def _split_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
            if isinstance(values, list):
                return [str(item).strip() for item in values if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in raw.split(",") if item.strip()]


def _project_rows(conn: sqlite3.Connection, project: str | None, all_projects: bool) -> list[tuple]:
    if all_projects:
        return conn.execute(
            """
            SELECT id, name, description, status, priority, updated_at
            FROM projects
            ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, updated_at DESC, id
            """
        ).fetchall()
    if project:
        row = conn.execute(
            """
            SELECT id, name, description, status, priority, updated_at
            FROM projects
            WHERE lower(name) = lower(?)
            LIMIT 1
            """,
            (project,),
        ).fetchone()
        if not row:
            raise SystemExit(f"Project not found in data/pka.db: {project}")
        return [row]
    row = conn.execute(
        """
        SELECT id, name, description, status, priority, updated_at
        FROM projects
        WHERE status = 'active'
        ORDER BY priority = 'high' DESC, updated_at DESC, id
        LIMIT 1
        """
    ).fetchone()
    if not row:
        raise SystemExit("No active project found. Use --project or --all-projects.")
    return [row]


def _task_path_for_project(name: str) -> Path | None:
    expected = TASKS_DIR / f"{_slugify(name, 'project')}.md"
    if expected.exists():
        return expected
    for path in sorted(TASKS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if re.search(rf"^#\s+{re.escape(name)}\s*$", text, flags=re.MULTILINE | re.IGNORECASE):
            return path
    return None


def _load_project_source(conn: sqlite3.Connection, row: tuple) -> ProjectSource:
    project_id, name, description, status, priority, updated_at = row
    task_path = _task_path_for_project(name)
    frontmatter: dict[str, str] = {}
    sections: dict[str, str] = {}
    if task_path:
        frontmatter, sections = _parse_sectioned_markdown(task_path.read_text(encoding="utf-8"))
    return ProjectSource(
        project_id=int(project_id),
        name=name,
        description=description or "",
        status=status or "active",
        priority=priority or "medium",
        updated_at=updated_at,
        task_path=task_path,
        task_frontmatter=frontmatter,
        task_sections=sections,
    )


def _keywords_for_project(project: ProjectSource) -> list[str]:
    tags = _split_tags(project.task_frontmatter.get("tags"))
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", project.name)
    keywords = [project.name] + tags + words
    seen: set[str] = set()
    cleaned: list[str] = []
    for keyword in keywords:
        key = keyword.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(keyword)
    return cleaned[:10]


def _like(value: str) -> str:
    return f"%{value}%"


def _is_generated_artifact(category: str | None, source_file: str | None) -> bool:
    category_key = (category or "").strip().lower()
    source = (source_file or "").strip()
    if category_key in GENERATED_CATEGORIES:
        return True
    return any(source.startswith(prefix) for prefix in GENERATED_SOURCE_PREFIXES)


def _is_ascii_path(source_file: str | None) -> bool:
    try:
        (source_file or "").encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def _kb_matches(conn: sqlite3.Connection, project: ProjectSource, limit: int) -> list[dict[str, Any]]:
    matches: dict[int, dict[str, Any]] = {}

    source_paths = []
    if project.task_path:
        source_paths.append(_rel_path(project.task_path))
    source_paths.extend(_parse_bullets(project.task_sections.get("Related Artifacts", "")))

    for source_index, source_path in enumerate(source_paths):
        if _is_pending_or_note_source(source_path):
            continue
        normalized = source_path.strip().strip("`")
        if not normalized:
            continue
        row = conn.execute(
            """
            SELECT id, title, category, tags, source_file, updated_at, content
            FROM knowledge_base
            WHERE source_file = ?
              AND COALESCE(category, '') NOT IN ('dreamer', 'session-artifacts')
              AND COALESCE(source_file, '') NOT LIKE 'wiki/%'
              AND COALESCE(source_file, '') NOT LIKE 'owners-inbox/dreamer-%'
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
        if row:
            matches[int(row[0])] = _kb_row_to_dict(row, rank=source_index)

    for keyword in _keywords_for_project(project):
        rows = conn.execute(
            """
            SELECT id, title, category, tags, source_file, updated_at, content
            FROM knowledge_base
            WHERE (title LIKE ? OR tags LIKE ? OR content LIKE ? OR source_file LIKE ?)
              AND COALESCE(category, '') NOT IN ('dreamer', 'session-artifacts')
              AND COALESCE(source_file, '') NOT LIKE 'wiki/%'
              AND COALESCE(source_file, '') NOT LIKE 'owners-inbox/dreamer-%'
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (_like(keyword), _like(keyword), _like(keyword), _like(keyword), max(4, limit)),
        ).fetchall()
        for row in rows:
            current = _kb_row_to_dict(row, rank=100)
            if _is_generated_artifact(current["category"], current["source_file"]):
                continue
            existing = matches.get(current["id"])
            if not existing or current["rank"] < existing["rank"]:
                matches[current["id"]] = current
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    return sorted(matches.values(), key=lambda item: (item["rank"], -item["id"]))[:limit]


def _kb_row_to_dict(row: tuple, *, rank: int) -> dict[str, Any]:
    kb_id, title, category, tags, source_file, updated_at, content = row
    return {
        "id": int(kb_id),
        "title": title or "Untitled",
        "category": category or "",
        "tags": tags or "",
        "source_file": source_file or "",
        "updated_at": updated_at or "",
        "excerpt": _excerpt(content or "", 220),
        "rank": rank,
    }


def _journal_matches(conn: sqlite3.Connection, project: ProjectSource, limit: int) -> list[dict[str, Any]]:
    return _journal_matches_for_keywords(conn, _keywords_for_project(project), limit)


def _journal_matches_for_keywords(conn: sqlite3.Connection, keywords: list[str], limit: int) -> list[dict[str, Any]]:
    matches: dict[int, dict[str, Any]] = {}
    for keyword in keywords:
        rows = conn.execute(
            """
            SELECT id, title, summary, tags, created_at
            FROM journal_entries
            WHERE title LIKE ? OR summary LIKE ? OR content LIKE ? OR tags LIKE ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (_like(keyword), _like(keyword), _like(keyword), _like(keyword), limit),
        ).fetchall()
        for entry_id, title, summary, tags, created_at in rows:
            matches[int(entry_id)] = {
                "id": int(entry_id),
                "title": title or "Untitled",
                "summary": summary or "",
                "tags": tags or "",
                "created_at": created_at or "",
            }
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    return sorted(matches.values(), key=lambda item: item["id"], reverse=True)[:limit]


def _journal_matches_for_person(conn: sqlite3.Connection, person: PersonSource, limit: int) -> list[dict[str, Any]]:
    matches: dict[int, dict[str, Any]] = {}
    if person.contact_id is not None:
        rows = conn.execute(
            """
            SELECT je.id, je.title, je.summary, je.tags, je.created_at
            FROM journal_entries je
            JOIN entry_contacts ec ON ec.entry_id = je.id
            WHERE ec.contact_id = ?
            ORDER BY je.created_at DESC, je.id DESC
            LIMIT ?
            """,
            (person.contact_id, limit),
        ).fetchall()
        for entry_id, title, summary, tags, created_at in rows:
            matches[int(entry_id)] = {
                "id": int(entry_id),
                "title": title or "Untitled",
                "summary": summary or "",
                "tags": tags or "",
                "created_at": created_at or "",
            }

    for entry in _journal_matches_for_keywords(conn, person.keywords, limit):
        matches[entry["id"]] = entry
        if len(matches) >= limit:
            break
    return sorted(matches.values(), key=lambda item: item["id"], reverse=True)[:limit]


def _kb_matches_for_keywords(conn: sqlite3.Connection, keywords: list[str], limit: int) -> list[dict[str, Any]]:
    matches: dict[int, dict[str, Any]] = {}
    for keyword in keywords:
        rows = conn.execute(
            """
            SELECT id, title, category, tags, source_file, updated_at, content
            FROM knowledge_base
            WHERE (title LIKE ? OR tags LIKE ? OR content LIKE ? OR source_file LIKE ?)
              AND COALESCE(category, '') NOT IN ('dreamer', 'session-artifacts')
              AND COALESCE(source_file, '') NOT LIKE 'wiki/%'
              AND COALESCE(source_file, '') NOT LIKE 'owners-inbox/dreamer-%'
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (_like(keyword), _like(keyword), _like(keyword), _like(keyword), max(4, limit)),
        ).fetchall()
        for row in rows:
            current = _kb_row_to_dict(row, rank=100)
            if _is_generated_artifact(current["category"], current["source_file"]):
                continue
            existing = matches.get(current["id"])
            if not existing or current["id"] > existing["id"]:
                matches[current["id"]] = current
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    return sorted(matches.values(), key=lambda item: item["id"], reverse=True)[:limit]


def _kb_matches_for_person(conn: sqlite3.Connection, person: PersonSource, limit: int) -> list[dict[str, Any]]:
    matches: dict[int, dict[str, Any]] = {}
    excluded_exact_categories = {"sop-promotion"}

    exact_terms = [person.name, person.slug]
    if " " in person.name:
        exact_terms.append(person.name.replace(" ", "-").lower())

    for term in exact_terms:
        rows = conn.execute(
            """
            SELECT id, title, category, tags, source_file, updated_at, content
            FROM knowledge_base
            WHERE (title LIKE ? OR tags LIKE ? OR content LIKE ? OR source_file LIKE ?)
              AND COALESCE(category, '') NOT IN ('dreamer', 'session-artifacts')
              AND COALESCE(source_file, '') NOT LIKE 'wiki/%'
              AND COALESCE(source_file, '') NOT LIKE 'owners-inbox/dreamer-%'
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (_like(term), _like(term), _like(term), _like(term), max(4, limit)),
        ).fetchall()
        for row in rows:
            current = _kb_row_to_dict(row, rank=0)
            if _is_generated_artifact(current["category"], current["source_file"]):
                continue
            if current["category"] in excluded_exact_categories:
                continue
            if not _is_ascii_path(current["source_file"]):
                continue
            matches[current["id"]] = current
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    if len(matches) >= limit:
        return sorted(matches.values(), key=lambda item: (item["rank"], -item["id"]))[:limit]

    excluded_context_categories = {"brand-system", "sop-promotion", "session-log", "tools"}
    context_terms = [item for item in person.keywords if item not in exact_terms]
    for term in context_terms:
        rows = conn.execute(
            """
            SELECT id, title, category, tags, source_file, updated_at, content
            FROM knowledge_base
            WHERE (title LIKE ? OR tags LIKE ? OR source_file LIKE ?)
              AND COALESCE(category, '') NOT IN ('dreamer', 'session-artifacts')
              AND COALESCE(source_file, '') NOT LIKE 'wiki/%'
              AND COALESCE(source_file, '') NOT LIKE 'owners-inbox/dreamer-%'
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (_like(term), _like(term), _like(term), max(4, limit)),
        ).fetchall()
        for row in rows:
            current = _kb_row_to_dict(row, rank=50)
            if _is_generated_artifact(current["category"], current["source_file"]):
                continue
            if not _is_ascii_path(current["source_file"]):
                continue
            if current["category"] in excluded_context_categories:
                continue
            matches.setdefault(current["id"], current)
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break

    return sorted(matches.values(), key=lambda item: (item["rank"], -item["id"]))[:limit]


def _excerpt(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _ascii_text(text: str) -> str:
    replacements = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2192": "->",
        "\u2705": "[done]",
        "\u00b1": "+/-",
        "\u00f8": "o",
        "\u00d8": "O",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("ascii", errors="ignore").decode("ascii")


def _yaml_list(items: list[Any], indent: str = "  ") -> list[str]:
    if not items:
        return []
    return [f"{indent}- {item}" for item in items]


def _add_yaml_list(lines: list[str], key: str, items: list[Any]) -> None:
    if not items:
        lines.append(f"{key}: []")
        return
    lines.append(f"{key}:")
    lines.extend(_yaml_list(items))


def _source_paths(project: ProjectSource, kb_entries: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    if project.task_path:
        paths.append(_rel_path(project.task_path))
    for entry in kb_entries:
        if entry["source_file"]:
            paths.append(entry["source_file"])
    for artifact in _parse_bullets(project.task_sections.get("Related Artifacts", "")):
        if _is_pending_or_note_source(artifact):
            continue
        cleaned = artifact.strip().strip("`")
        if cleaned and cleaned not in paths:
            paths.append(cleaned)
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result[:30]


def _source_paths_from_kb(kb_entries: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for entry in kb_entries:
        source_file = entry.get("source_file") or ""
        if not source_file:
            continue
        key = source_file.lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(source_file)
    return paths[:30]


def _render_project_page(
    project: ProjectSource,
    kb_entries: list[dict[str, Any]],
    journal_entries: list[dict[str, Any]],
) -> str:
    summary = _clean_section_text(project.task_sections.get("Summary", "")) or project.description
    current_focus = _parse_bullets(project.task_sections.get("Current Focus", ""))
    decisions = _parse_bullets(project.task_sections.get("Key Decisions", ""))
    open_loops = _parse_bullets(project.task_sections.get("Open Loops", ""))
    source_paths = _source_paths(project, kb_entries)
    kb_ids = [entry["id"] for entry in kb_entries]
    journal_ids = [entry["id"] for entry in journal_entries]

    lines = [
        "---",
        f"title: {json.dumps(project.name)}",
        "type: project",
        f"description: {json.dumps(summary or project.description)}",
        f"status: {project.status}",
        f"priority: {project.priority}",
        f"last_compiled: {_today()}",
        f"timestamp: {_today()}T00:00:00Z",
    ]
    _add_yaml_list(lines, "tags", _keywords_for_project(project))
    _add_yaml_list(lines, "source_kb_ids", kb_ids)
    _add_yaml_list(lines, "source_journal_ids", journal_ids)
    _add_yaml_list(lines, "source_paths", source_paths)
    lines.extend(
        [
            "confidence: medium",
            "compiled_by: Dreamer",
            "---",
            "",
            f"# {project.name}",
            "",
            "## Current Synthesis",
            "",
            summary or "No project summary found yet.",
            "",
        ]
    )

    if current_focus:
        lines.extend(["The current focus is:", ""])
        lines.extend(f"- {item}" for item in current_focus[:6])
        lines.append("")

    lines.extend(["## Known Facts", ""])
    known = [
        f"Project status is `{project.status}` with `{project.priority}` priority.",
    ]
    if project.updated_at:
        known.append(f"The project row was last updated at `{project.updated_at}`.")
    if project.task_path:
        known.append(f"The canonical task record is `{_rel_path(project.task_path)}`.")
    known.extend(decisions[:8])
    lines.extend(f"- {item}" for item in known)
    lines.append("")

    lines.extend(["## Open Questions", ""])
    if open_loops:
        lines.extend(f"- {item}" for item in open_loops[:10])
    else:
        lines.append("- No open loops found in the canonical task record.")
    lines.append("")

    lines.extend(["## Related Evidence", ""])
    if kb_entries:
        for entry in kb_entries[:12]:
            label = f"KB #{entry['id']} - {entry['title']}"
            if entry["source_file"]:
                label += f" (`{entry['source_file']}`)"
            lines.append(f"- {label}")
    else:
        lines.append("- No matching knowledge-base entries found.")
    lines.append("")

    if journal_entries:
        lines.extend(["## Journal Signals", ""])
        for entry in journal_entries[:6]:
            detail = entry["summary"] or entry["tags"] or entry["created_at"]
            lines.append(f"- Journal #{entry['id']} - {entry['title']}: {detail}")
        lines.append("")

    lines.extend(
        [
            "## Related Pages",
            "",
            "- [PKA Wiki Index](../index.md)",
            "",
            "## Source Notes",
            "",
            "This page was generated from durable PKA sources. Treat it as compiled context, not primary evidence.",
        ]
    )
    return _ascii_text("\n".join(lines).strip()) + "\n"


def _render_concept_page(
    concept: ConceptSource,
    kb_entries: list[dict[str, Any]],
    journal_entries: list[dict[str, Any]],
) -> str:
    source_paths = _source_paths_from_kb(kb_entries)
    kb_ids = [entry["id"] for entry in kb_entries]
    journal_ids = [entry["id"] for entry in journal_entries]

    lines = [
        "---",
        f"title: {json.dumps(concept.title)}",
        "type: concept",
        f"description: {json.dumps(concept.summary)}",
        "status: active",
        f"last_compiled: {_today()}",
        f"timestamp: {_today()}T00:00:00Z",
    ]
    _add_yaml_list(lines, "tags", concept.keywords)
    _add_yaml_list(lines, "source_kb_ids", kb_ids)
    _add_yaml_list(lines, "source_journal_ids", journal_ids)
    _add_yaml_list(lines, "source_paths", source_paths)
    lines.extend(
        [
            "confidence: medium",
            "compiled_by: Dreamer",
            "---",
            "",
            f"# {concept.title}",
            "",
            "## Current Synthesis",
            "",
            concept.summary,
            "",
        ]
    )

    lines.extend(["## Known Facts", ""])
    if kb_entries:
        for entry in kb_entries[:8]:
            fact = f"KB #{entry['id']} - {entry['title']}"
            if entry["category"]:
                fact += f" is categorized as `{entry['category']}`"
            if entry["source_file"]:
                fact += f" and comes from `{entry['source_file']}`"
            fact += "."
            lines.append(f"- {fact}")
    else:
        lines.append("- No matching knowledge-base entries found.")
    lines.append("")

    lines.extend(["## Reusable Context", ""])
    if kb_entries:
        for entry in kb_entries[:6]:
            if entry["excerpt"]:
                lines.append(f"- {entry['excerpt']}")
    else:
        lines.append("- No reusable excerpts found yet.")
    lines.append("")

    lines.extend(["## Open Questions", ""])
    lines.append("- Which source claims should become stable operating guidance, and which should remain project-specific?")
    lines.append("- Does this concept need a richer hand-authored synthesis after the next Dreamer pass?")
    lines.append("")

    if journal_entries:
        lines.extend(["## Journal Signals", ""])
        for entry in journal_entries[:6]:
            detail = entry["summary"] or entry["tags"] or entry["created_at"]
            lines.append(f"- Journal #{entry['id']} - {entry['title']}: {detail}")
        lines.append("")

    lines.extend(["## Related Pages", ""])
    lines.append("- [PKA Wiki Index](../index.md)")
    if concept.slug == "hybrid-context-layer":
        lines.append("- [PKA System](../projects/pka-system.md)")
    if concept.slug in {"hyperframes", "cuecam-mobile-workflow"}:
        lines.append("- [CueCam One-Command Pipeline](../projects/cuecam-one-command-pipeline.md)")
    if concept.slug == "pka-brand-voice":
        lines.append("- [Creator Lab Content Extension](../projects/creator-lab-content-extension.md)")
    lines.extend(
        [
            "",
            "## Source Notes",
            "",
            "This page was generated from durable PKA sources. Treat it as compiled context, not primary evidence.",
        ]
    )
    return _ascii_text("\n".join(lines).strip()) + "\n"


def _related_pages_for_person(person: PersonSource) -> list[str]:
    keyword_text = " ".join(person.keywords).lower()
    links = ["- [PKA Wiki Index](../index.md)"]
    if any(token in keyword_text for token in ("creator lab", "nab", "mimolive", "boinx")):
        links.append("- [Creator Lab Content Extension](../projects/creator-lab-content-extension.md)")
    if any(token in keyword_text for token in ("cuecam", "helieroast", "testimonial")):
        links.append("- [CueCam One-Command Pipeline](../projects/cuecam-one-command-pipeline.md)")
        links.append("- [CueCam Mobile Workflow](../concepts/cuecam-mobile-workflow.md)")
    if "hyperframes" in keyword_text:
        links.append("- [HyperFrames](../concepts/hyperframes.md)")
    return links


def _format_kb_fact(entry: dict[str, Any]) -> str:
    fact = f"KB #{entry['id']} - {entry['title']}"
    if entry["category"]:
        fact += f" is categorized as `{entry['category']}`"
    if entry["source_file"]:
        fact += f" and comes from `{entry['source_file']}`"
    return fact + "."


def _render_person_page(
    person: PersonSource,
    kb_entries: list[dict[str, Any]],
    journal_entries: list[dict[str, Any]],
) -> str:
    source_paths = _source_paths_from_kb(kb_entries)
    kb_ids = [entry["id"] for entry in kb_entries]
    journal_ids = [entry["id"] for entry in journal_entries]
    confidence = "medium" if kb_entries or journal_entries else "low"
    direct_entries = [entry for entry in kb_entries if int(entry.get("rank", 100)) < 50]
    context_entries = [entry for entry in kb_entries if int(entry.get("rank", 100)) >= 50]

    lines = [
        "---",
        f"title: {json.dumps(person.name)}",
        "type: person",
        f"description: {json.dumps(person.summary)}",
        "status: active",
        f"last_compiled: {_today()}",
        f"timestamp: {_today()}T00:00:00Z",
    ]
    _add_yaml_list(lines, "tags", person.keywords)
    if person.contact_id is not None:
        _add_yaml_list(lines, "source_contact_ids", [person.contact_id])
    else:
        _add_yaml_list(lines, "source_contact_ids", [])
    _add_yaml_list(lines, "source_kb_ids", kb_ids)
    _add_yaml_list(lines, "source_journal_ids", journal_ids)
    _add_yaml_list(lines, "source_paths", source_paths)
    lines.extend(
        [
            f"confidence: {confidence}",
            "compiled_by: Dreamer",
            "---",
            "",
            f"# {person.name}",
            "",
            "## Current Synthesis",
            "",
            person.summary,
            "",
        ]
    )

    if person.contact_summary or person.notes:
        lines.extend(["Contact context:", ""])
        if person.contact_summary:
            lines.append(f"- {person.contact_summary}")
        if person.notes and person.notes != person.contact_summary:
            lines.append(f"- {person.notes}")
        lines.append("")

    lines.extend(["## Direct Evidence", ""])
    evidence: list[str] = []
    if person.contact_id is not None:
        evidence.append(f"Contact row `{person.contact_id}` exists for {person.name}.")
    if person.role:
        evidence.append(f"Role/context in contacts: {person.role}.")
    if person.company:
        evidence.append(f"Company/context in contacts: {person.company}.")
    for entry in direct_entries[:8]:
        evidence.append(_format_kb_fact(entry))
    if evidence:
        lines.extend(f"- {item}" for item in evidence)
    else:
        lines.append("- No direct contact or exact person-match evidence found yet beyond the person specification.")
    lines.append("")

    lines.extend(["## Relationship Context", ""])
    if journal_entries:
        for entry in journal_entries[:8]:
            detail = entry["summary"] or entry["tags"] or entry["created_at"]
            lines.append(f"- Journal #{entry['id']} - {entry['title']}: {detail}")
    else:
        lines.append("- No matching journal signals found yet.")
    lines.append("")

    lines.extend(["## Project Context", ""])
    if context_entries:
        for entry in context_entries[:8]:
            lines.append(f"- {_format_kb_fact(entry)}")
    else:
        lines.append("- No broader project-context evidence was needed for this page.")
    lines.append("")

    lines.extend(["## Follow-Up State", ""])
    lines.append(f"- {person.follow_up}")
    lines.append("")

    reusable_entries = direct_entries[:3] + context_entries[:2]
    lines.extend(["## Reusable Context", ""])
    if reusable_entries:
        for entry in reusable_entries:
            if entry["excerpt"]:
                lines.append(f"- {entry['excerpt']}")
    else:
        lines.append("- No reusable excerpts found yet.")
    lines.append("")

    lines.extend(["## Open Questions", ""])
    lines.append("- Which facts belong in a durable relationship note versus a project-specific page?")
    lines.append("- Should this page be expanded with a hand-authored relationship note after the next owner review?")
    lines.append("")

    lines.extend(["## Related Pages", ""])
    lines.extend(_related_pages_for_person(person))
    lines.extend(
        [
            "",
            "## Source Notes",
            "",
            "This page was generated from durable PKA sources. Treat it as compiled context, not primary evidence.",
        ]
    )
    return _ascii_text("\n".join(lines).strip()) + "\n"


def _upsert_kb_links(conn: sqlite3.Connection, kb_entries: list[dict[str, Any]]) -> int:
    if len(kb_entries) < 2:
        return 0
    anchor = kb_entries[0]["id"]
    created = 0
    for entry in kb_entries[1:]:
        target = entry["id"]
        if anchor == target:
            continue
        exists = conn.execute(
            """
            SELECT 1 FROM kb_links
            WHERE source_id = ? AND target_id = ? AND relationship = 'related'
            LIMIT 1
            """,
            (anchor, target),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO kb_links (source_id, target_id, relationship, created_at)
            VALUES (?, ?, 'related', ?)
            """,
            (anchor, target, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        created += 1
    conn.commit()
    return created


def _prune_generated_kb_links(conn: sqlite3.Connection) -> int:
    cursor = conn.execute(
        """
        DELETE FROM kb_links
        WHERE source_id IN (
            SELECT id FROM knowledge_base
            WHERE category IN ('dreamer', 'session-artifacts')
               OR source_file LIKE 'wiki/%'
               OR source_file LIKE 'owners-inbox/dreamer-%'
        )
           OR target_id IN (
            SELECT id FROM knowledge_base
            WHERE category IN ('dreamer', 'session-artifacts')
               OR source_file LIKE 'wiki/%'
               OR source_file LIKE 'owners-inbox/dreamer-%'
        )
        """
    )
    conn.commit()
    return cursor.rowcount if cursor.rowcount is not None else 0


def _wiki_links(directory: Path, relative_dir: str) -> str:
    links = []
    for path in sorted(directory.glob("*.md")):
        title = path.stem.replace("-", " ").title()
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        if match:
            title = match.group(1).strip()
        links.append(f"- [{title}]({relative_dir}/{path.name})")
    return "\n".join(links)


def _replace_index_section(existing: str, heading: str, body: str) -> str:
    replacement = f"## {heading}\n\n{body or f'No compiled {heading.lower()} pages yet.'}\n"
    pattern = rf"## {re.escape(heading)}\n\n.*?(?=\n## |\Z)"
    if re.search(pattern, existing, flags=re.S):
        return re.sub(pattern, replacement, existing, flags=re.S)
    return existing.rstrip() + "\n\n" + replacement


def _load_video_runs() -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Load every run manifest, returning the readable ones and the failures.

    A manifest that cannot be read must never vanish quietly: a page claiming to
    cover every run has to say which ones it could not.
    """
    runs: list[dict[str, Any]] = []
    excluded: list[tuple[str, str]] = []
    for manifest in sorted(VIDEO_PROJECTS_DIR.glob("*/project.json")):
        rel = f"{manifest.parent.name}/project.json"
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except OSError as exc:
            excluded.append((rel, f"unreadable: {exc.strerror or exc}"))
            continue
        except json.JSONDecodeError as exc:
            excluded.append((rel, f"malformed JSON: line {exc.lineno}, column {exc.colno}"))
            continue
        if not isinstance(payload, dict) or not payload.get("slug"):
            excluded.append((rel, "missing required key: slug"))
            continue
        runs.append(payload)
    return runs, excluded


def compile_pipeline(dry_run: bool = False) -> dict[str, Any]:
    """Compile what the video-editorial pipeline looks like across every run.

    The per-run view belongs to video_project.py, which owns project.md. This
    page owns only what one run cannot show: how often a stage is skipped, which
    observations are still open, and where decisions cluster.
    """
    from video_project import (
        ARTIFACT_FLOW,
        WORKFLOW_STAGE_SEQUENCE,
        expected_inputs,
        primary_governance,
        stage_of,
    )

    runs, excluded = _load_video_runs()
    if not runs and not excluded:
        return {"status": "no-runs", "page": None, "excluded": []}

    slugs = [run["slug"] for run in runs]
    divergence: dict[tuple[str, str], list[str]] = {}
    observations: list[tuple[str, dict[str, Any]]] = []
    decisions: list[tuple[str, dict[str, Any]]] = []
    for run in runs:
        artifacts = run.get("artifacts", {})
        for kind in artifacts:
            for absent in expected_inputs(kind):
                if absent not in artifacts:
                    divergence.setdefault((kind, absent), []).append(run["slug"])
        for item in run.get("observations", []):
            observations.append((run["slug"], item))
        for item in run.get("decisions", []):
            decisions.append((run["slug"], item))

    today = _today()
    lines = [
        "---",
        'title: "Video Editorial Pipeline"',
        "type: operation",
        "status: active",
        'description: "How the interview-to-blog pipeline actually behaves across every '
        'recorded run."',
        f"last_compiled: {today}",
        f"timestamp: {today}T00:00:00Z",
        "tags:",
        "  - video-editorial",
        "  - pipeline",
        "  - operations",
        "  - workflow",
        "source_kb_ids: []",
        "source_journal_ids: []",
        "source_paths:",
    ]
    for slug in slugs:
        lines.append(f"  - owners-inbox/video-projects/{slug}/project.json")
    lines.extend([
        "confidence: medium",
        "compiled_by: Dreamer",
        "---",
        "",
        "# Video Editorial Pipeline",
        "",
        "Compiled from every video project manifest. The per-run view lives in each",
        "project's own page; this page carries only what a single run cannot show.",
        "",
        "## Runs",
        "",
    ])
    ranked = sorted(divergence.items(), key=lambda item: (-len(item[1]), item[0]))
    open_items = [
        (slug, item) for slug, item in observations
        if item.get("status") in {"proposed", "accepted", "implemented"}
    ]

    if not runs:
        # Every manifest failed to load. The page still gets written, because a
        # missing page says nothing while an empty one names what went wrong.
        lines.append(
            "No manifest could be read, so this page describes nothing. Every "
            "manifest is listed under exclusions below."
        )
    else:
        lines.extend([
            "| Run | State | Artifacts | Published | Decisions | Open observations |",
            "|---|---|---|---|---|---|",
        ])
        for run in runs:
            published = ", ".join(sorted(run.get("publications", {}))) or "-"
            open_count = len([
                item for item in run.get("observations", [])
                if item.get("status") in {"proposed", "accepted", "implemented"}
            ])
            lines.append(
                f'| [{run["slug"]}](../../owners-inbox/video-projects/{run["slug"]}/project.md) '
                f'| `{run["state"]}` | {len(run.get("artifacts", {}))} | {published} '
                f'| {len(run.get("decisions", []))} | {open_count} |'
            )

        lines.extend([
            "",
            "## Stage coverage",
            "",
            "Which artifact kinds each run actually registered.",
            "",
            "| Kind | Stage | " + " | ".join(slugs) + " |",
            "|---|---|" + "---|" * len(slugs),
        ])
        for kind in ARTIFACT_FLOW:
            marks = " | ".join("x" if kind in run.get("artifacts", {}) else "-" for run in runs)
            lines.append(f"| `{kind}` | {stage_of(kind)} | {marks} |")

        lines.extend([
            "",
            "## Most frequent divergence",
            "",
            "An attached artifact whose expected input is absent, counted across runs.",
            "This is where the designed pipeline and the practised one disagree most; it",
            "is not a ranking of causes.",
            "",
        ])
        if ranked:
            for (kind, absent), where in ranked[:15]:
                lines.append(
                    f"- `{kind}` without `{absent}` - {len(where)}/{len(runs)} runs "
                    f"({', '.join(where)})"
                )
        else:
            lines.append("- None. Every run matches the designed flow.")

        lines.extend(["", "## Open observations", ""])
        if open_items:
            for slug, item in open_items:
                governing = primary_governance(item["stage"]) or ""
                target = f" - would change `{governing}`" if governing else ""
                lines.append(
                    f'- `{item["id"]}` [{item["status"]}] **{item["stage"]}** ({slug}) '
                    f'{item["summary"]}{target}'
                )
        else:
            lines.append("- None raised yet.")

        lines.extend(["", "## Decisions by stage", ""])
        if decisions:
            for stage in WORKFLOW_STAGE_SEQUENCE:
                at_stage = [(slug, item) for slug, item in decisions if item["stage"] == stage]
                if at_stage:
                    lines.append(f"- **{stage}:** {len(at_stage)}")
                    for slug, item in at_stage[:5]:
                        lines.append(f'  - {slug}: {item["summary"]}')
        else:
            lines.extend([
                "No decisions have been recorded on any run. Until they are, this page can",
                "show where runs diverged from the design but never why, and no",
                "retrospective can either.",
            ])

    if excluded:
        lines.extend([
            "",
            "## Manifests excluded from this page",
            "",
            "These runs could not be read, so nothing above accounts for them.",
            "",
        ])
        for rel, reason in excluded:
            lines.append(f"- `{rel}` - {reason}")

    body = _ascii_text("\n".join(lines).strip()) + "\n"
    if dry_run:
        return {
            "status": "dry-run",
            "page": None,
            "markdown": body,
            "runs": len(runs),
            "excluded": excluded,
        }

    OPERATIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = OPERATIONS_DIR / "video-editorial-pipeline.md"
    path.write_text(body, encoding="utf-8")
    indexed = index_markdown_artifact(
        path,
        title="Video Editorial Pipeline",
        category="wiki",
        tags=["video-editorial", "pipeline", "operations"],
        summary="Cross-run view of the video editorial pipeline.",
    )
    _update_wiki_index()
    return {
        "status": "compiled",
        "page": _rel_path(path),
        "runs": len(runs),
        "excluded": excluded,
        "divergences": len(ranked),
        "open_observations": len(open_items),
        "decisions": len(decisions),
        "knowledge_base_id": indexed["knowledge_base_id"],
    }


def _update_wiki_index() -> None:
    index_path = WIKI_DIR / "index.md"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    if not existing:
        existing = (
            "---\n"
            "title: PKA Wiki Index\n"
            "type: operation\n"
            "status: active\n"
            "description: Entry point directory for the compiled context layer.\n"
            f"last_compiled: {_today()}\n"
            f"timestamp: {_today()}T00:00:00Z\n"
            "tags:\n"
            "  - wiki\n"
            "  - index\n"
            "  - context-layer\n"
            "source_kb_ids: []\n"
            "source_journal_ids: []\n"
            "source_paths: []\n"
            "confidence: medium\n"
            "compiled_by: Dreamer\n"
            "---\n\n"
            "# PKA Wiki Index\n\n"
            "This is the entry point for the compiled context layer.\n\n"
            "## Projects\n\n"
            "No compiled project pages yet.\n\n"
            "## Concepts\n\nNo compiled concept pages yet.\n\n"
            "## People\n\nNo compiled people pages yet.\n\n"
            "## Operations\n\nNo compiled operations pages yet.\n"
        )

    existing = re.sub(
        r"last_compiled:\s*\d{4}-\d{2}-\d{2}",
        f"last_compiled: {_today()}",
        existing,
        count=1,
    )
    iso_timestamp = f"{_today()}T00:00:00Z"
    if "timestamp:" in existing:
        existing = re.sub(
            r"timestamp:\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            f"timestamp: {iso_timestamp}",
            existing,
            count=1,
        )
    else:
        existing = existing.replace(
            f"last_compiled: {_today()}",
            f"last_compiled: {_today()}\ntimestamp: {iso_timestamp}",
            1
        )
    existing = _replace_index_section(existing, "Projects", _wiki_links(PROJECTS_DIR, "projects"))
    existing = _replace_index_section(existing, "Concepts", _wiki_links(CONCEPTS_DIR, "concepts"))
    existing = _replace_index_section(existing, "People", _wiki_links(PEOPLE_DIR, "people"))
    if OPERATIONS_DIR.exists():
        existing = _replace_index_section(
            existing, "Operations", _wiki_links(OPERATIONS_DIR, "operations")
        )
    index_path.write_text(_ascii_text(existing.strip()) + "\n", encoding="utf-8")


def _update_wiki_log(updated_pages: list[dict[str, Any]]) -> None:
    if not updated_pages:
        return
    log_path = WIKI_DIR / "log.md"

    today = _today()
    iso_timestamp = f"{today}T{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z"

    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
    else:
        existing = (
            "---\n"
            "title: PKA Wiki Change Log\n"
            "type: operation\n"
            "status: active\n"
            "description: Chronological history of updates to the PKA wiki context layer.\n"
            f"timestamp: {iso_timestamp}\n"
            "compiled_by: Dreamer\n"
            "---\n\n"
            "# PKA Wiki Change Log\n\n"
            "This log tracks updates to the compiled wiki context layer.\n"
        )

    heading = f"## {today}"
    entry_lines = []
    for page in updated_pages:
        rel_wiki_path = page["wiki_path"]
        if rel_wiki_path.startswith("wiki/"):
            rel_wiki_path = rel_wiki_path[5:]

        ptype = "page"
        if "projects/" in rel_wiki_path:
            ptype = "project"
        elif "concepts/" in rel_wiki_path:
            ptype = "concept"
        elif "people/" in rel_wiki_path:
            ptype = "person"

        entry_lines.append(f"- Updated {ptype} [{page['title']}]({rel_wiki_path})")

    entry_text = "\n".join(entry_lines)

    match = re.search(r"(# PKA Wiki Change Log\n+.*?\n+)(.*)", existing, flags=re.S)
    if match:
        header_part, body_part = match.groups()
        if heading in body_part:
            pattern = rf"({re.escape(heading)}\n+)(.*?)(?=\n## |\Z)"
            def _sub(m):
                orig_head, orig_body = m.groups()
                lines = orig_body.splitlines()
                for line in entry_lines:
                    if line not in lines:
                        lines.insert(0, line)
                return orig_head + "\n".join(lines).strip() + "\n"
            body_part = re.sub(pattern, _sub, body_part, flags=re.S)
        else:
            body_part = f"{heading}\n\n{entry_text}\n\n" + body_part.strip() + "\n"
        new_text = header_part + body_part
    else:
        new_text = existing.rstrip() + f"\n\n{heading}\n\n{entry_text}\n"

    # Update timestamp
    if "timestamp:" in new_text:
        new_text = re.sub(
            r"timestamp:\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
            f"timestamp: {iso_timestamp}",
            new_text,
            count=1,
        )
    else:
        if "---\n" in new_text:
            parts = new_text.split("---\n", 2)
            if len(parts) == 3:
                parts[1] = parts[1].rstrip() + f"\ntimestamp: {iso_timestamp}\n"
                new_text = "---\n".join(parts)

    log_path.write_text(_ascii_text(new_text.strip()) + "\n", encoding="utf-8")


def _index_link_findings() -> list[AuditFinding]:
    index_path = WIKI_DIR / "index.md"
    rel_index = _rel_path(index_path)
    if not index_path.exists():
        return [
            AuditFinding(
                kind="missing-index-page",
                severity="low",
                page=rel_index,
                detail="Wiki index page is missing.",
                recommendation="Rebuild `wiki/index.md` from compiled page directories.",
            )
        ]

    index_text = index_path.read_text(encoding="utf-8")
    findings: list[AuditFinding] = []
    for directory, relative_dir, label in (
        (PROJECTS_DIR, "projects", "project"),
        (CONCEPTS_DIR, "concepts", "concept"),
        (PEOPLE_DIR, "people", "person"),
    ):
        if not directory.exists():
            continue
        for link in _wiki_links(directory, relative_dir).splitlines():
            if link and link not in index_text:
                findings.append(
                    AuditFinding(
                        kind="missing-index-link",
                        severity="low",
                        page=rel_index,
                        detail=f"Index is missing compiled {label} link: {link}",
                        recommendation="Rebuild the wiki index from compiled page directories.",
                    )
                )
    return findings


def _render_report(results: list[dict[str, Any]]) -> str:
    lines = [
        f"# Dreamer Wiki Compile Report - {_today()}",
        "",
        "## Summary",
        "",
        f"- Pages updated: {len(results)}",
        f"- KB links created: {sum(item['kb_links_created'] for item in results)}",
        "",
        "## Pages",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"### {result['title']}",
                "",
                f"- Page: `{result['wiki_path']}`",
                f"- Source KB IDs: {', '.join(str(item) for item in result['source_kb_ids']) or 'None'}",
                f"- Source journal IDs: {', '.join(str(item) for item in result['source_journal_ids']) or 'None'}",
                f"- KB links created: {result['kb_links_created']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Notes",
            "",
            "- The wiki remains a compiled view. Use DB rows and source files as primary evidence.",
            "- Review `wiki/contradictions.md` and `wiki/stale-review.md` during deeper Dreamer passes.",
        ]
    )
    return _ascii_text("\n".join(lines).strip()) + "\n"


def _wiki_pages() -> list[WikiPage]:
    pages: list[WikiPage] = []
    for path in sorted(WIKI_DIR.rglob("*.md")):
        if path.name == "README.md":
            continue
        text = path.read_text(encoding="utf-8")
        frontmatter, body = _parse_frontmatter(text)
        pages.append(WikiPage(path=path, frontmatter=frontmatter, body=body))
    return pages


def _project_status_by_name(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        "SELECT name, status, priority, updated_at FROM projects ORDER BY id"
    ).fetchall()
    return {
        str(name).lower(): {
            "name": name or "",
            "status": status or "",
            "priority": priority or "",
            "updated_at": updated_at or "",
        }
        for name, status, priority, updated_at in rows
    }


def _task_frontmatter_for_title(title: str) -> dict[str, str]:
    task_path = _task_path_for_project(title)
    if not task_path:
        return {}
    text = task_path.read_text(encoding="utf-8")
    frontmatter, _sections = _parse_sectioned_markdown(text)
    return frontmatter


def _audit_wiki_pages(conn: sqlite3.Connection, stale_days: int) -> tuple[list[AuditFinding], list[AuditFinding]]:
    stale: list[AuditFinding] = []
    contradictions: list[AuditFinding] = []
    project_rows = _project_status_by_name(conn)
    stale.extend(_index_link_findings())

    for page in _wiki_pages():
        rel_page = _rel_path(page.path)
        title = str(page.frontmatter.get("title") or page.path.stem)
        page_type = str(page.frontmatter.get("type") or "")

        age_days = _date_age_days(str(page.frontmatter.get("last_compiled") or ""))
        if age_days is None:
            stale.append(
                AuditFinding(
                    kind="missing-last-compiled",
                    severity="medium",
                    page=rel_page,
                    detail="Page has no parseable `last_compiled` value.",
                    recommendation="Re-run the wiki compiler for this page.",
                )
            )
        elif age_days > stale_days:
            stale.append(
                AuditFinding(
                    kind="stale-page",
                    severity="low",
                    page=rel_page,
                    detail=f"Page was last compiled {age_days} days ago.",
                    recommendation="Re-run the wiki compiler and review whether the synthesis still holds.",
                )
            )

        source_paths = [_clean_source_path(item) for item in _as_list(page.frontmatter.get("source_paths"))]
        for source_path in source_paths:
            if not source_path:
                continue
            if not _source_path_exists(source_path):
                stale.append(
                    AuditFinding(
                        kind="missing-source-path",
                        severity="high",
                        page=rel_page,
                        detail=f"Source path does not resolve: `{source_path}`.",
                        recommendation="Repair the source path, re-index the artifact, or remove it from the compiled page.",
                    )
                )

        # Database IDs are peer-local under federation. When portable source
        # paths exist, those paths carry cross-machine provenance and numeric
        # IDs from the compiling peer are diagnostic hints only. Pages without
        # any portable path still receive the stricter local-row validation.
        kb_ids = [str(item).strip() for item in _as_list(page.frontmatter.get("source_kb_ids")) if str(item).strip()]
        if not source_paths:
            for kb_id in kb_ids:
                row = conn.execute(
                    "SELECT id FROM knowledge_base WHERE id = ?",
                    (kb_id,),
                ).fetchone()
                if not row:
                    stale.append(
                        AuditFinding(
                            kind="missing-kb-row",
                            severity="high",
                            page=rel_page,
                            detail=f"Frontmatter references missing local KB row `{kb_id}` and has no portable source path.",
                            recommendation="Re-run compilation with source paths so provenance can travel between peers.",
                        )
                    )

        if page_type == "project":
            project = project_rows.get(title.lower())
            if not project:
                contradictions.append(
                    AuditFinding(
                        kind="missing-project-row",
                        severity="high",
                        page=rel_page,
                        detail=f"Project page `{title}` has no matching row in the `projects` table.",
                        recommendation="Create or repair the project row, or move the page out of `wiki/projects/`.",
                    )
                )
                continue

            wiki_status = str(page.frontmatter.get("status") or "")
            if wiki_status and wiki_status != project["status"]:
                contradictions.append(
                    AuditFinding(
                        kind="project-status-mismatch",
                        severity="high",
                        page=rel_page,
                        detail=f"Wiki status is `{wiki_status}` but DB project status is `{project['status']}`.",
                        recommendation="Re-run project compilation or update the canonical project record.",
                    )
                )

            wiki_priority = str(page.frontmatter.get("priority") or "")
            if wiki_priority and wiki_priority != project["priority"]:
                contradictions.append(
                    AuditFinding(
                        kind="project-priority-mismatch",
                        severity="medium",
                        page=rel_page,
                        detail=f"Wiki priority is `{wiki_priority}` but DB project priority is `{project['priority']}`.",
                        recommendation="Re-run project compilation or update the canonical project record.",
                    )
                )

            task_frontmatter = _task_frontmatter_for_title(title)
            task_status = task_frontmatter.get("status")
            if task_status and project["status"] and task_status != project["status"]:
                contradictions.append(
                    AuditFinding(
                        kind="task-db-status-mismatch",
                        severity="high",
                        page=rel_page,
                        detail=f"Task record status is `{task_status}` but DB project status is `{project['status']}`.",
                        recommendation="Use `tools/task_record.py upsert` or edit the canonical task record source of truth.",
                    )
                )
            task_priority = task_frontmatter.get("priority")
            if task_priority and project["priority"] and task_priority != project["priority"]:
                contradictions.append(
                    AuditFinding(
                        kind="task-db-priority-mismatch",
                        severity="medium",
                        page=rel_page,
                        detail=f"Task record priority is `{task_priority}` but DB project priority is `{project['priority']}`.",
                        recommendation="Align task record and DB project priority.",
                    )
                )

    return stale, contradictions


def _render_findings_page(
    *,
    title: str,
    purpose: str,
    section_title: str,
    empty_text: str,
    findings: list[AuditFinding],
) -> str:
    lines = [
        "---",
        f"title: {title}",
        "type: operation",
        "status: active",
        f"last_compiled: {_today()}",
        "source_kb_ids: []",
        "source_journal_ids: []",
        "source_paths: []",
        "confidence: medium",
        "compiled_by: Dreamer",
        "---",
        "",
        f"# {title}",
        "",
        purpose,
        "",
        f"## {section_title}",
        "",
    ]
    if not findings:
        lines.append(empty_text)
    else:
        for finding in findings:
            lines.extend(
                [
                    f"### {finding.kind} - {finding.severity}",
                    "",
                    f"- Page: `{finding.page}`",
                    f"- Detail: {finding.detail}",
                    f"- Recommendation: {finding.recommendation}",
                    "",
                ]
            )
    return _ascii_text("\n".join(lines).strip()) + "\n"


def _render_audit_report(stale: list[AuditFinding], contradictions: list[AuditFinding], stale_days: int) -> str:
    lines = [
        f"# Dreamer Wiki Audit Report - {_today()}",
        "",
        "## Summary",
        "",
        f"- Stale review threshold: {stale_days} days",
        f"- Stale/source findings: {len(stale)}",
        f"- Contradiction findings: {len(contradictions)}",
        "",
        "## Stale And Source Findings",
        "",
    ]
    if not stale:
        lines.append("- None.")
    else:
        for finding in stale:
            lines.append(f"- `{finding.page}` - {finding.kind}: {finding.detail}")
    lines.extend(["", "## Contradiction Findings", ""])
    if not contradictions:
        lines.append("- None.")
    else:
        for finding in contradictions:
            lines.append(f"- `{finding.page}` - {finding.kind}: {finding.detail}")
    return _ascii_text("\n".join(lines).strip()) + "\n"


def _pages_by_rel_path() -> dict[str, WikiPage]:
    return {_rel_path(page.path): page for page in _wiki_pages()}


def _refresh_compiled_page(page: WikiPage, dry_run: bool) -> RepairAction:
    rel_page = _rel_path(page.path)
    page_type = str(page.frontmatter.get("type") or "")
    title = str(page.frontmatter.get("title") or page.path.stem)

    if page_type == "project":
        compile_projects(title, all_projects=False, kb_limit=12, journal_limit=6, dry_run=dry_run)
        return RepairAction(
            action="refresh-project-page",
            status="planned" if dry_run else "applied",
            page=rel_page,
            detail=f"Refresh project page from canonical project/task sources: {title}",
        )
    if page_type == "concept":
        compile_concepts(title, all_concepts=False, kb_limit=10, journal_limit=5, dry_run=dry_run)
        return RepairAction(
            action="refresh-concept-page",
            status="planned" if dry_run else "applied",
            page=rel_page,
            detail=f"Refresh concept page from configured concept sources: {title}",
        )
    if page_type == "person":
        compile_people(title, all_people=False, kb_limit=10, journal_limit=6, dry_run=dry_run)
        return RepairAction(
            action="refresh-person-page",
            status="planned" if dry_run else "applied",
            page=rel_page,
            detail=f"Refresh person page from contacts, journals, and matching KB sources: {title}",
        )

    return RepairAction(
        action="refresh-page",
        status="refused",
        page=rel_page,
        detail=f"Repair mode only refreshes project, concept, and person pages; found type `{page_type or 'unknown'}`.",
    )


def _repair_audit_findings(
    stale: list[AuditFinding],
    contradictions: list[AuditFinding],
    dry_run: bool,
) -> tuple[list[RepairAction], list[RepairAction]]:
    actions: list[RepairAction] = []
    refused: list[RepairAction] = []
    pages = _pages_by_rel_path()
    refreshed_pages: set[str] = set()
    index_rebuild_needed = False

    for finding in stale:
        if finding.kind in {"missing-index-page", "missing-index-link"}:
            index_rebuild_needed = True
            continue

        if finding.kind in {"stale-page", "missing-last-compiled"}:
            page = pages.get(finding.page)
            if not page:
                refused.append(
                    RepairAction(
                        action="refresh-page",
                        status="refused",
                        page=finding.page,
                        detail="Cannot refresh because the page no longer exists.",
                    )
                )
                continue
            if finding.page in refreshed_pages:
                continue
            action = _refresh_compiled_page(page, dry_run)
            if action.status == "refused":
                refused.append(action)
            else:
                actions.append(action)
                refreshed_pages.add(finding.page)
            continue

        refused.append(
            RepairAction(
                action="repair-finding",
                status="refused",
                page=finding.page,
                detail=f"Repair mode does not modify source paths or KB provenance for `{finding.kind}` findings.",
            )
        )

    if index_rebuild_needed:
        if not dry_run:
            _update_wiki_index()
        actions.append(
            RepairAction(
                action="rebuild-index",
                status="planned" if dry_run else "applied",
                page=_rel_path(WIKI_DIR / "index.md"),
                detail="Rebuild wiki index links from compiled project, concept, and people pages.",
            )
        )

    for finding in contradictions:
        refused.append(
            RepairAction(
                action="repair-contradiction",
                status="refused",
                page=finding.page,
                detail=f"Contradiction `{finding.kind}` requires source-of-truth review; safe repair mode will not change it.",
            )
        )

    return actions, refused


def compile_projects(project: str | None, all_projects: bool, kb_limit: int, journal_limit: int, dry_run: bool) -> dict[str, Any]:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with _connect() as conn:
        pruned_links = 0 if dry_run else _prune_generated_kb_links(conn)
        for row in _project_rows(conn, project, all_projects):
            source = _load_project_source(conn, row)
            kb_entries = _kb_matches(conn, source, kb_limit)
            journal_entries = _journal_matches(conn, source, journal_limit)
            markdown = _render_project_page(source, kb_entries, journal_entries)
            page_path = PROJECTS_DIR / f"{_slugify(source.name, 'project')}.md"
            kb_links_created = 0 if dry_run else _upsert_kb_links(conn, kb_entries)
            result = {
                "title": source.name,
                "wiki_path": _rel_path(page_path),
                "source_kb_ids": [entry["id"] for entry in kb_entries],
                "source_journal_ids": [entry["id"] for entry in journal_entries],
                "kb_links_created": kb_links_created,
            }
            if dry_run:
                result["markdown"] = markdown
            else:
                page_path.write_text(markdown, encoding="utf-8")
                try:
                    index_markdown_artifact(
                        page_path,
                        title=source.name,
                        category="wiki",
                        tags=["wiki", "project", _slugify(source.name, 'project')],
                        summary=_clean_section_text(source.task_sections.get('Summary', '')) or source.description,
                    )
                except SatelliteWriteError:
                    pass
            results.append(result)

    if not dry_run:
        _update_wiki_index()
        _update_wiki_log(results)
        try:
            update_wiki_graph(dry_run=False)
        except SatelliteWriteError:
            pass
        report_path = PKA_ROOT / "owners-inbox" / f"dreamer-{_today()}-wiki-projects-compile.md"
        report = _render_report(results)
        report_path.write_text(report, encoding="utf-8")
        report_kb_id = None
        try:
            index_result = index_markdown_artifact(
                report_path,
                title=f"Dreamer Wiki Compile Report - {_today()}",
                category="dreamer",
                tags=["dreamer", "wiki", "compiled-context", "knowledge-base"],
                summary="Dreamer wiki compilation report for generated PKA context pages.",
            )
            report_kb_id = index_result["knowledge_base_id"]
        except SatelliteWriteError:
            pass  # satellite: file queued; indexing deferred to primary
        return {
            "status": "saved",
            "pages_updated": len(results),
            "generated_kb_links_pruned": pruned_links,
            "results": results,
            "report_path": _rel_path(report_path),
            "report_kb_id": report_kb_id,
        }

    return {
        "status": "dry-run",
        "pages_updated": len(results),
        "results": results,
    }


def _concept_sources(concept: str | None, all_concepts: bool) -> list[ConceptSource]:
    if all_concepts:
        selected = CONCEPT_SPECS.items()
    elif concept:
        slug = _slugify(concept, "concept")
        if slug not in CONCEPT_SPECS:
            raise SystemExit(
                f"Concept not found: {concept}. Available: {', '.join(sorted(CONCEPT_SPECS))}"
            )
        selected = [(slug, CONCEPT_SPECS[slug])]
    else:
        slug = "hybrid-context-layer"
        selected = [(slug, CONCEPT_SPECS[slug])]

    return [
        ConceptSource(
            slug=slug,
            title=str(spec["title"]),
            summary=str(spec["summary"]),
            keywords=[str(item) for item in spec["keywords"]],
        )
        for slug, spec in selected
    ]


def _contact_row_for_name(conn: sqlite3.Connection, name: str) -> tuple | None:
    return conn.execute(
        """
        SELECT id, name, role, company, notes, summary
        FROM contacts
        WHERE lower(name) = lower(?)
        ORDER BY id
        LIMIT 1
        """,
        (name,),
    ).fetchone()


def _person_sources(conn: sqlite3.Connection, person: str | None, all_people: bool) -> list[PersonSource]:
    if all_people:
        selected = PERSON_SPECS.items()
    elif person:
        slug = _slugify(person, "person")
        if slug not in PERSON_SPECS:
            row = _contact_row_for_name(conn, person)
            if not row:
                raise SystemExit(
                    f"Person not found: {person}. Available: {', '.join(sorted(PERSON_SPECS))}"
                )
            contact_id, contact_name, role, company, notes, contact_summary = row
            keywords = [str(contact_name)]
            if role:
                keywords.append(str(role))
            if company:
                keywords.append(str(company))
            return [
                PersonSource(
                    slug=_slugify(str(contact_name), "person"),
                    name=str(contact_name),
                    summary=str(contact_summary or notes or f"{contact_name} is a PKA contact."),
                    keywords=keywords,
                    contact_id=int(contact_id),
                    role=str(role or ""),
                    company=str(company or ""),
                notes=str(notes or ""),
                contact_summary=str(contact_summary or ""),
                follow_up="No explicit follow-up state compiled yet.",
            )
        ]
        selected = [(slug, PERSON_SPECS[slug])]
    else:
        selected = PERSON_SPECS.items()

    people: list[PersonSource] = []
    for slug, spec in selected:
        title = str(spec["title"])
        row = _contact_row_for_name(conn, title)
        contact_id = None
        role = ""
        company = ""
        notes = ""
        contact_summary = ""
        if row:
            contact_id_raw, _contact_name, role, company, notes, contact_summary = row
            contact_id = int(contact_id_raw)
            role = str(role or "")
            company = str(company or "")
            notes = str(notes or "")
            contact_summary = str(contact_summary or "")
        keywords = [str(item) for item in spec["keywords"]]
        if role:
            keywords.append(role)
        if company:
            keywords.append(company)
        people.append(
            PersonSource(
                slug=slug,
                name=title,
                summary=str(spec["summary"]),
                keywords=keywords,
                contact_id=contact_id,
                role=role,
                company=company,
                notes=notes,
                contact_summary=contact_summary,
                follow_up=str(spec.get("follow_up") or "No explicit follow-up state compiled yet."),
            )
        )
    return people


def compile_concepts(concept: str | None, all_concepts: bool, kb_limit: int, journal_limit: int, dry_run: bool) -> dict[str, Any]:
    CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with _connect() as conn:
        pruned_links = 0 if dry_run else _prune_generated_kb_links(conn)
        for source in _concept_sources(concept, all_concepts):
            kb_entries = _kb_matches_for_keywords(conn, source.keywords, kb_limit)
            journal_entries = _journal_matches_for_keywords(conn, source.keywords, journal_limit)
            markdown = _render_concept_page(source, kb_entries, journal_entries)
            page_path = CONCEPTS_DIR / f"{source.slug}.md"
            kb_links_created = 0 if dry_run else _upsert_kb_links(conn, kb_entries)
            result = {
                "title": source.title,
                "wiki_path": _rel_path(page_path),
                "source_kb_ids": [entry["id"] for entry in kb_entries],
                "source_journal_ids": [entry["id"] for entry in journal_entries],
                "kb_links_created": kb_links_created,
            }
            if dry_run:
                result["markdown"] = markdown
            else:
                page_path.write_text(markdown, encoding="utf-8")
                try:
                    index_markdown_artifact(
                        page_path,
                        title=source.title,
                        category="wiki",
                        tags=["wiki", "concept", source.slug],
                        summary=source.summary,
                    )
                except SatelliteWriteError:
                    pass
            results.append(result)

    if not dry_run:
        _update_wiki_index()
        _update_wiki_log(results)
        try:
            update_wiki_graph(dry_run=False)
        except SatelliteWriteError:
            pass
        report_path = PKA_ROOT / "owners-inbox" / f"dreamer-{_today()}-wiki-concepts-compile.md"
        report = _render_report(results)
        report_path.write_text(report, encoding="utf-8")
        report_kb_id = None
        try:
            index_result = index_markdown_artifact(
                report_path,
                title=f"Dreamer Wiki Compile Report - {_today()}",
                category="dreamer",
                tags=["dreamer", "wiki", "compiled-context", "concepts"],
                summary="Dreamer wiki compilation report for generated PKA concept pages.",
            )
            report_kb_id = index_result["knowledge_base_id"]
        except SatelliteWriteError:
            pass  # satellite: file queued; indexing deferred to primary
        return {
            "status": "saved",
            "pages_updated": len(results),
            "generated_kb_links_pruned": pruned_links,
            "results": results,
            "report_path": _rel_path(report_path),
            "report_kb_id": report_kb_id,
        }

    return {
        "status": "dry-run",
        "pages_updated": len(results),
        "results": results,
    }


def compile_people(person: str | None, all_people: bool, kb_limit: int, journal_limit: int, dry_run: bool) -> dict[str, Any]:
    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with _connect() as conn:
        pruned_links = 0 if dry_run else _prune_generated_kb_links(conn)
        for source in _person_sources(conn, person, all_people):
            kb_entries = _kb_matches_for_person(conn, source, kb_limit)
            journal_entries = _journal_matches_for_person(conn, source, journal_limit)
            markdown = _render_person_page(source, kb_entries, journal_entries)
            page_path = PEOPLE_DIR / f"{source.slug}.md"
            kb_links_created = 0 if dry_run else _upsert_kb_links(conn, kb_entries)
            result = {
                "title": source.name,
                "wiki_path": _rel_path(page_path),
                "source_kb_ids": [entry["id"] for entry in kb_entries],
                "source_journal_ids": [entry["id"] for entry in journal_entries],
                "kb_links_created": kb_links_created,
            }
            if source.contact_id is not None:
                result["source_contact_id"] = source.contact_id
            if dry_run:
                result["markdown"] = markdown
            else:
                page_path.write_text(markdown, encoding="utf-8")
                try:
                    index_markdown_artifact(
                        page_path,
                        title=source.name,
                        category="wiki",
                        tags=["wiki", "person", source.slug],
                        summary=source.summary,
                    )
                except SatelliteWriteError:
                    pass
            results.append(result)

    if not dry_run:
        _update_wiki_index()
        _update_wiki_log(results)
        try:
            update_wiki_graph(dry_run=False)
        except SatelliteWriteError:
            pass
        report_path = PKA_ROOT / "owners-inbox" / f"dreamer-{_today()}-wiki-people-compile.md"
        report = _render_report(results)
        report_path.write_text(report, encoding="utf-8")
        report_kb_id = None
        try:
            index_result = index_markdown_artifact(
                report_path,
                title=f"Dreamer Wiki Compile Report - {_today()}",
                category="dreamer",
                tags=["dreamer", "wiki", "compiled-context", "people"],
                summary="Dreamer wiki compilation report for generated PKA people pages.",
            )
            report_kb_id = index_result["knowledge_base_id"]
        except SatelliteWriteError:
            pass  # satellite: file queued; indexing deferred to primary
        return {
            "status": "saved",
            "pages_updated": len(results),
            "generated_kb_links_pruned": pruned_links,
            "results": results,
            "report_path": _rel_path(report_path),
            "report_kb_id": report_kb_id,
        }

    return {
        "status": "dry-run",
        "pages_updated": len(results),
        "results": results,
    }


def audit_wiki(stale_days: int, dry_run: bool, repair: bool = False) -> dict[str, Any]:
    with _connect() as conn:
        stale, contradictions = _audit_wiki_pages(conn, stale_days)

    repairs: list[RepairAction] = []
    refused_repairs: list[RepairAction] = []
    initial_stale_count = len(stale)
    initial_contradiction_count = len(contradictions)

    if repair:
        repairs, refused_repairs = _repair_audit_findings(stale, contradictions, dry_run)
        if not repairs and not refused_repairs:
            return {
                "status": "no-op",
                "stale_findings": len(stale),
                "contradiction_findings": len(contradictions),
                "stale_pages": [finding.__dict__ for finding in stale],
                "contradictions": [finding.__dict__ for finding in contradictions],
                "repairs_applied": [],
                "repairs_refused": [],
            }
        if not dry_run and repairs:
            with _connect() as conn:
                stale, contradictions = _audit_wiki_pages(conn, stale_days)

    stale_markdown = _render_findings_page(
        title="Wiki Stale Review",
        purpose="Dreamer records pages and sources that need freshness or provenance review here.",
        section_title="Pages To Review",
        empty_text="No stale or missing-source findings recorded.",
        findings=stale,
    )
    contradictions_markdown = _render_findings_page(
        title="Wiki Contradictions",
        purpose="Dreamer records unresolved contradictions found during wiki audits here.",
        section_title="Open Contradictions",
        empty_text="No contradictions recorded.",
        findings=contradictions,
    )
    report_markdown = _render_audit_report(stale, contradictions, stale_days)

    if repair:
        status = "repair-dry-run" if dry_run else "repaired"
        if not repairs and refused_repairs:
            status = "repair-refused"
    else:
        status = "dry-run" if dry_run else "saved"

    result = {
        "status": status,
        "stale_findings": len(stale),
        "contradiction_findings": len(contradictions),
        "stale_pages": [finding.__dict__ for finding in stale],
        "contradictions": [finding.__dict__ for finding in contradictions],
    }
    if repair:
        result["initial_stale_findings"] = initial_stale_count
        result["initial_contradiction_findings"] = initial_contradiction_count
        result["repairs_applied"] = [action.__dict__ for action in repairs]
        result["repairs_refused"] = [action.__dict__ for action in refused_repairs]
    if dry_run:
        result["stale_markdown"] = stale_markdown
        result["contradictions_markdown"] = contradictions_markdown
        result["report_markdown"] = report_markdown
        return result

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    stale_path = WIKI_DIR / "stale-review.md"
    contradictions_path = WIKI_DIR / "contradictions.md"
    report_path = PKA_ROOT / "owners-inbox" / f"dreamer-{_today()}-wiki-audit.md"
    stale_path.write_text(stale_markdown, encoding="utf-8")
    contradictions_path.write_text(contradictions_markdown, encoding="utf-8")
    report_path.write_text(report_markdown, encoding="utf-8")
    if repair:
        repairs.append(
            RepairAction(
                action="rewrite-audit-pages",
                status="applied",
                page=f"{_rel_path(stale_path)}, {_rel_path(contradictions_path)}",
                detail="Rewrite stale-review and contradictions pages after repair evaluation.",
            )
        )
        result["repairs_applied"] = [action.__dict__ for action in repairs]
    result["report_path"] = _rel_path(report_path)
    result["report_kb_id"] = None
    try:
        index_result = index_markdown_artifact(
            report_path,
            title=f"Dreamer Wiki Audit Report - {_today()}",
            category="dreamer",
            tags=["dreamer", "wiki", "audit", "stale-review", "contradictions"],
            summary="Dreamer wiki audit report for stale context, missing source paths, and contradictions.",
        )
        result["report_kb_id"] = index_result["knowledge_base_id"]
    except SatelliteWriteError:
        pass  # satellite: file queued; indexing deferred to primary
    return result


def _resolve_relative_wiki_path(source_file: str, link_target: str) -> str | None:
    """
    Resolves relative link_target (e.g. '../concepts/hybrid.md') relative to source_file (e.g. 'wiki/projects/pka.md').
    Returns the normalized relative path from PKA_ROOT (e.g. 'wiki/concepts/hybrid.md').
    """
    if link_target.startswith(("http://", "https://", "mailto:", "file://")):
        return None
    link_target = link_target.split("#")[0].split("?")[0]
    if not link_target.endswith(".md"):
        return None
        
    source_file = source_file.replace("\\", "/")
    link_target = link_target.replace("\\", "/")
    
    source_parts = source_file.split("/")
    target_parts = link_target.split("/")
    
    stack = source_parts[:-1]
    for part in target_parts:
        if part == "" or part == ".":
            continue
        elif part == "..":
            if stack:
                stack.pop()
        else:
            stack.append(part)
            
    return "/".join(stack)


def update_wiki_graph(dry_run: bool = False) -> int:
    """
    Scans the compiled wiki directory, extracts relative links,
    and populates the kb_links table in SQLite.
    """
    if not WIKI_DIR.exists():
        return 0
        
    created_count = 0
    with _connect() as conn:
        if not dry_run:
            _prune_generated_kb_links(conn)
            
        cursor = conn.execute(
            "SELECT id, source_file FROM knowledge_base WHERE source_file LIKE 'wiki/%'"
        )
        wiki_file_ids = {row[1]: row[0] for row in cursor.fetchall()}
        
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
        
        for rel_file_path, source_id in list(wiki_file_ids.items()):
            full_path = PKA_ROOT / rel_file_path
            if not full_path.exists():
                continue
            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                continue
                
            for match in link_pattern.finditer(content):
                target_link = match.group(2)
                resolved_target = _resolve_relative_wiki_path(rel_file_path, target_link)
                if not resolved_target:
                    continue
                    
                target_id = wiki_file_ids.get(resolved_target)
                if not target_id:
                    row = conn.execute(
                        "SELECT id FROM knowledge_base WHERE source_file = ?",
                        (resolved_target,)
                    ).fetchone()
                    if row:
                        target_id = row[0]
                        wiki_file_ids[resolved_target] = target_id
                        
                if target_id and source_id != target_id:
                    exists = conn.execute(
                        """
                        SELECT 1 FROM kb_links
                        WHERE source_id = ? AND target_id = ? AND relationship = 'references'
                        LIMIT 1
                        """,
                        (source_id, target_id)
                    ).fetchone()
                    
                    if not exists:
                        if not dry_run:
                            conn.execute(
                                """
                                INSERT INTO kb_links (source_id, target_id, relationship, created_at)
                                VALUES (?, ?, 'references', ?)
                                """,
                                (source_id, target_id, datetime.now(timezone.utc).isoformat(timespec="seconds"))
                            )
                        created_count += 1
                        
        if not dry_run:
            conn.commit()
            
    return created_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile PKA source material into wiki pages")
    sub = parser.add_subparsers(dest="command", required=True)

    p_projects = sub.add_parser("projects", help="Compile project wiki pages")
    p_projects.add_argument("--project", help="Project name to compile")
    p_projects.add_argument("--all", action="store_true", help="Compile all projects")
    p_projects.add_argument("--kb-limit", type=int, default=12, help="Maximum KB entries per page")
    p_projects.add_argument("--journal-limit", type=int, default=6, help="Maximum journal entries per page")
    p_projects.add_argument("--dry-run", action="store_true", help="Return generated markdown without writing files")

    p_concepts = sub.add_parser("concepts", help="Compile concept wiki pages")
    p_concepts.add_argument("--concept", help="Concept slug or name to compile")
    p_concepts.add_argument("--all", action="store_true", help="Compile all known concepts")
    p_concepts.add_argument("--kb-limit", type=int, default=10, help="Maximum KB entries per page")
    p_concepts.add_argument("--journal-limit", type=int, default=5, help="Maximum journal entries per page")
    p_concepts.add_argument("--dry-run", action="store_true", help="Return generated markdown without writing files")

    p_people = sub.add_parser("people", help="Compile people wiki pages")
    p_people.add_argument("--person", help="Person name or slug to compile")
    p_people.add_argument("--all", action="store_true", help="Compile all known people")
    p_people.add_argument("--kb-limit", type=int, default=10, help="Maximum KB entries per page")
    p_people.add_argument("--journal-limit", type=int, default=6, help="Maximum journal entries per page")
    p_people.add_argument("--dry-run", action="store_true", help="Return generated markdown without writing files")

    p_pipeline = sub.add_parser("pipeline", help="Compile the cross-run video pipeline page")
    p_pipeline.add_argument("--dry-run", action="store_true", help="Return generated markdown without writing files")

    p_audit = sub.add_parser("audit", help="Audit wiki pages for stale sources and contradictions")
    p_audit.add_argument("--stale-days", type=int, default=14, help="Flag pages compiled more than this many days ago")
    p_audit.add_argument("--repair", action="store_true", help="Apply safe repairs for stale generated pages and index links")
    p_audit.add_argument("--dry-run", action="store_true", help="Return generated markdown without writing files")

    args = parser.parse_args()

    if args.command == "projects":
        result = compile_projects(args.project, args.all, args.kb_limit, args.journal_limit, args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "concepts":
        result = compile_concepts(args.concept, args.all, args.kb_limit, args.journal_limit, args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "people":
        result = compile_people(args.person, args.all, args.kb_limit, args.journal_limit, args.dry_run)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "pipeline":
        result = compile_pipeline(args.dry_run)
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "audit":
        result = audit_wiki(args.stale_days, args.dry_run, args.repair)
        print(json.dumps(result, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
