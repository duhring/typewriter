#!/usr/bin/env python3
"""
Find recurring editorial signals in recent PKA artifacts.

This is a bridge between ordinary publishing cycles and the compiled wiki layer:
it scans recent durable artifacts and journal entries, groups repeated people,
project, and concept signals, and writes a reviewable report for Dreamer.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pka_index import index_markdown_artifact
from wiki_compile import CONCEPT_SPECS, PERSON_SPECS, PKA_ROOT, _parse_sectioned_markdown, _slugify, _split_tags


DB_PATH = PKA_ROOT / "data" / "pka.db"
OWNERS_INBOX = PKA_ROOT / "owners-inbox"
REPORT_DIR = OWNERS_INBOX / "editorial-recurrences"
WIKI_DIR = PKA_ROOT / "wiki"

EXCLUDED_KB_CATEGORIES = {
    "dreamer",
    "session-artifacts",
    "sop-promotion",
}

EXCLUDED_SOURCE_PREFIXES = (
    "wiki/",
    "owners-inbox/dreamer-",
    "owners-inbox/editorial-recurrences/",
)

HIGH_SIGNAL_CATEGORIES = {
    "blog",
    "youtube-content",
    "youtube-package",
    "youtube-packages",
    "transcript",
    "transcripts",
    "transcript-extract",
    "meeting-extract",
    "research",
    "task-record",
    "presentation",
    "cuecam-hyperframes",
    "brand-system",
    "session-log",
}


@dataclass
class Evidence:
    source_kind: str
    source_id: int
    title: str
    category: str
    tags: str
    source_path: str
    created_at: str
    excerpt: str


@dataclass
class RecurrenceTarget:
    target_type: str
    slug: str
    title: str
    keywords: list[str]
    wiki_path: str
    evidence: dict[str, Evidence] = field(default_factory=dict)

    @property
    def score(self) -> int:
        base = len(self.evidence)
        category_bonus = sum(
            1 for item in self.evidence.values() if item.category in HIGH_SIGNAL_CATEGORIES
        )
        source_bonus = len({item.source_path for item in self.evidence.values() if item.source_path})
        return base * 2 + category_bonus + min(source_bonus, 5)

    @property
    def action(self) -> str:
        path = PKA_ROOT / self.wiki_path
        if path.exists():
            return "refresh-existing-page"
        if len(self.evidence) >= 2:
            return "consider-new-page"
        return "watch"


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return _utc_now().date().isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _within_days(value: str | None, days: int) -> bool:
    moment = _parse_datetime(value)
    if moment is None:
        return False
    return moment >= _utc_now() - timedelta(days=days)


def _excerpt(text: str, limit: int = 260) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _like(value: str) -> str:
    return f"%{value}%"


def _is_generated_or_operational(category: str, source_path: str) -> bool:
    if (category or "").strip().lower() in EXCLUDED_KB_CATEGORIES:
        return True
    return any((source_path or "").startswith(prefix) for prefix in EXCLUDED_SOURCE_PREFIXES)


def _normalize_blob(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def _keyword_hits(blob: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        key = keyword.strip()
        if not key:
            continue
        if key.lower() in blob:
            hits.append(key)
    return hits


def _load_kb_evidence(conn: sqlite3.Connection, days: int) -> list[Evidence]:
    rows = conn.execute(
        """
        SELECT id, title, category, tags, source_file, created_at, updated_at, content
        FROM knowledge_base
        ORDER BY id DESC
        """
    ).fetchall()
    evidence: list[Evidence] = []
    for kb_id, title, category, tags, source_file, created_at, updated_at, content in rows:
        category = category or ""
        source_file = source_file or ""
        if _is_generated_or_operational(category, source_file):
            continue
        if not (_within_days(created_at, days) or _within_days(updated_at, days)):
            continue
        evidence.append(
            Evidence(
                source_kind="knowledge_base",
                source_id=int(kb_id),
                title=title or "Untitled",
                category=category,
                tags=tags or "",
                source_path=source_file,
                created_at=created_at or updated_at or "",
                excerpt=_excerpt(content or ""),
            )
        )
    return evidence


def _load_journal_evidence(conn: sqlite3.Connection, days: int) -> list[Evidence]:
    rows = conn.execute(
        """
        SELECT id, title, summary, content, tags, created_at
        FROM journal_entries
        ORDER BY id DESC
        """
    ).fetchall()
    evidence: list[Evidence] = []
    for entry_id, title, summary, content, tags, created_at in rows:
        if not _within_days(created_at, days):
            continue
        evidence.append(
            Evidence(
                source_kind="journal_entry",
                source_id=int(entry_id),
                title=title or "Untitled",
                category="journal",
                tags=tags or "",
                source_path="",
                created_at=created_at or "",
                excerpt=_excerpt(summary or content or ""),
            )
        )
    return evidence


def _project_targets(conn: sqlite3.Connection) -> list[RecurrenceTarget]:
    rows = conn.execute(
        "SELECT name, description, status FROM projects ORDER BY id"
    ).fetchall()
    targets: list[RecurrenceTarget] = []
    for name, description, status in rows:
        slug = _slugify(str(name), "project")
        keywords = [str(name), slug]
        task_path = PKA_ROOT / "owners-inbox" / "tasks" / f"{slug}.md"
        if task_path.exists():
            frontmatter, _sections = _parse_sectioned_markdown(task_path.read_text(encoding="utf-8"))
            keywords.extend(_split_tags(frontmatter.get("tags")))
        targets.append(
            RecurrenceTarget(
                target_type="project",
                slug=slug,
                title=str(name),
                keywords=_dedupe(keywords),
                wiki_path=f"wiki/projects/{slug}.md",
            )
        )
    return targets


def _concept_targets() -> list[RecurrenceTarget]:
    targets: list[RecurrenceTarget] = []
    for slug, spec in CONCEPT_SPECS.items():
        targets.append(
            RecurrenceTarget(
                target_type="concept",
                slug=slug,
                title=str(spec["title"]),
                keywords=_dedupe([str(spec["title"]), slug] + [str(item) for item in spec["keywords"]]),
                wiki_path=f"wiki/concepts/{slug}.md",
            )
        )
    return targets


def _person_targets(conn: sqlite3.Connection) -> list[RecurrenceTarget]:
    targets: dict[str, RecurrenceTarget] = {}
    for slug, spec in PERSON_SPECS.items():
        title = str(spec["title"])
        keywords = [title, slug]
        targets[slug] = RecurrenceTarget(
            target_type="person",
            slug=slug,
            title=title,
            keywords=_dedupe(keywords),
            wiki_path=f"wiki/people/{slug}.md",
        )

    rows = conn.execute("SELECT name, company, role, notes, summary FROM contacts ORDER BY id").fetchall()
    for name, company, role, notes, summary in rows:
        slug = _slugify(str(name), "person")
        if slug in targets:
            continue
        keywords = [str(name), slug]
        targets[slug] = RecurrenceTarget(
            target_type="person",
            slug=slug,
            title=str(name),
            keywords=_dedupe(keywords),
            wiki_path=f"wiki/people/{slug}.md",
        )
    return list(targets.values())


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = " ".join(str(item or "").split()).strip()
        if not cleaned or len(cleaned) < 3:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _score_targets(targets: list[RecurrenceTarget], evidence_items: list[Evidence]) -> list[RecurrenceTarget]:
    for item in evidence_items:
        blob = _normalize_blob(item.title, item.category, item.tags, item.source_path, item.excerpt)
        for target in targets:
            hits = _keyword_hits(blob, target.keywords)
            if not hits:
                continue
            key = f"{item.source_kind}:{item.source_id}"
            target.evidence[key] = item
    return sorted(
        [target for target in targets if target.evidence],
        key=lambda target: (target.score, len(target.evidence), target.title.lower()),
        reverse=True,
    )


def _render_report(targets: list[RecurrenceTarget], days: int, limit: int) -> str:
    selected = targets[:limit]
    strong = [target for target in selected if len(target.evidence) >= 2 or target.score >= 5]
    watch = [target for target in selected if target not in strong]

    lines = [
        "---",
        f"title: Editorial Recurrence Report - {_today()}",
        "type: dreamer-report",
        f"generated_at: {_utc_now().isoformat(timespec='seconds')}",
        f"window_days: {days}",
        "category: editorial-recurrence",
        "---",
        "",
        f"# Editorial Recurrence Report - {_today()}",
        "",
        "This report scans recent durable PKA artifacts and journal entries for repeated editorial signals.",
        "Use it to decide which project, concept, and person wiki pages deserve the next editorial pass.",
        "",
        "## Summary",
        "",
        f"- Review window: {days} days",
        f"- Recurrence targets with evidence: {len(targets)}",
        f"- Strong recurrence candidates: {len(strong)}",
        "",
        "## Strong Recurrences",
        "",
    ]

    if not strong:
        lines.append("- No strong recurrence candidates found yet.")
    else:
        for target in strong:
            lines.extend(_render_target_block(target))

    lines.extend(["", "## Watch List", ""])
    if not watch:
        lines.append("- No watch-list items in the selected set.")
    else:
        for target in watch[: max(0, limit - len(strong))]:
            lines.append(
                f"- {target.title} ({target.target_type}) - {len(target.evidence)} signal(s), action: `{target.action}`"
            )

    lines.extend(
        [
            "",
            "## Recommended Editorial Loop",
            "",
            "- Refresh existing wiki pages for strong recurrences that changed this week.",
            "- Promote repeated watch-list items only after they recur across multiple editorial cycles.",
            "- Keep source artifacts as the source of truth; use wiki pages as the synthesized view.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _render_target_block(target: RecurrenceTarget) -> list[str]:
    lines = [
        f"### {target.title}",
        "",
        f"- Type: `{target.target_type}`",
        f"- Score: {target.score}",
        f"- Signals: {len(target.evidence)}",
        f"- Suggested action: `{target.action}`",
        f"- Wiki path: `{target.wiki_path}`",
        "",
        "Evidence:",
    ]
    for item in sorted(target.evidence.values(), key=lambda entry: entry.created_at, reverse=True)[:8]:
        label = f"{item.source_kind} #{item.source_id} - {item.title}"
        if item.category:
            label += f" (`{item.category}`)"
        if item.source_path:
            label += f" - `{item.source_path}`"
        lines.append(f"- {label}")
    lines.append("")
    return lines


def generate_report(days: int, limit: int, dry_run: bool) -> dict[str, Any]:
    with _connect() as conn:
        evidence_items = _load_kb_evidence(conn, days) + _load_journal_evidence(conn, days)
        targets = _project_targets(conn) + _concept_targets() + _person_targets(conn)
        ranked = _score_targets(targets, evidence_items)

    markdown = _render_report(ranked, days, limit)
    result = {
        "status": "dry-run" if dry_run else "saved",
        "window_days": days,
        "evidence_items": len(evidence_items),
        "targets_with_evidence": len(ranked),
        "strong_recurrences": len([target for target in ranked[:limit] if len(target.evidence) >= 2 or target.score >= 5]),
        "top_targets": [
            {
                "title": target.title,
                "type": target.target_type,
                "score": target.score,
                "signals": len(target.evidence),
                "action": target.action,
                "wiki_path": target.wiki_path,
            }
            for target in ranked[:limit]
        ],
    }
    if dry_run:
        result["markdown"] = markdown
        return result

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{_today()}-editorial-recurrence.md"
    report_path.write_text(markdown, encoding="utf-8")
    index_result = index_markdown_artifact(
        report_path,
        title=f"Editorial Recurrence Report - {_today()}",
        category="editorial-recurrence",
        tags=["dreamer", "editorial", "recurrence", "wiki", "compiled-context"],
        summary="Weekly editorial recurrence report for wiki compile candidates and repeated signals.",
    )
    result["report_path"] = str(report_path.relative_to(PKA_ROOT))
    result["report_kb_id"] = index_result["knowledge_base_id"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an editorial recurrence report")
    parser.add_argument("--days", type=int, default=7, help="Recent artifact window")
    parser.add_argument("--limit", type=int, default=12, help="Maximum targets to include")
    parser.add_argument("--dry-run", action="store_true", help="Print report JSON without writing files")
    args = parser.parse_args()

    result = generate_report(args.days, args.limit, args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
