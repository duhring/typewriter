#!/usr/bin/env python3
"""
Create durable session logs for Codex work.

These logs live in owners-inbox/session-logs/ and are indexed so they can be
recalled later by Larry and the local memory layer.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from pka_index import index_markdown_artifact


PKA_ROOT = Path(__file__).resolve().parent.parent
SESSION_LOG_DIR = PKA_ROOT / "owners-inbox" / "session-logs"


def _slugify(text: str, fallback: str = "session") -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:60] or fallback


def _bullet_lines(items: list[str]) -> list[str]:
    if not items:
        return ["- None."]
    return [f"- {item}" for item in items]


def save_codex_session(
    *,
    title: str,
    summary: str,
    task_kind: str,
    deliverables: list[str],
    learnings: list[str],
    open_loops: list[str],
    tags: list[str],
    files_changed: int | None,
    lines_added: int | None,
    lines_removed: int | None,
) -> dict:
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}-codex-{_slugify(title)}.md"
    path = SESSION_LOG_DIR / filename
    lines_touched = None
    if lines_added is not None or lines_removed is not None:
        lines_touched = (lines_added or 0) + (lines_removed or 0)

    lines = [
        "---",
        f"created_at: {now.isoformat(timespec='seconds')}",
        f"task_kind: {task_kind}",
        "source: codex-session",
        f"title: {json.dumps(title)}",
    ]
    if files_changed is not None:
        lines.append(f"files_changed: {files_changed}")
    if lines_added is not None:
        lines.append(f"lines_added: {lines_added}")
    if lines_removed is not None:
        lines.append(f"lines_removed: {lines_removed}")
    if lines_touched is not None:
        lines.append(f"lines_touched: {lines_touched}")
    lines.extend([
        "---",
        "",
        f"# {title} — Codex Session Log",
        "",
        "## Summary",
        "",
        summary.strip(),
        "",
    ])
    if any(value is not None for value in (files_changed, lines_added, lines_removed)):
        lines.extend([
            "## Change Metrics",
            "",
        ])
        if files_changed is not None:
            lines.append(f"- Files changed: {files_changed}")
        if lines_added is not None:
            lines.append(f"- Lines added: {lines_added}")
        if lines_removed is not None:
            lines.append(f"- Lines removed: {lines_removed}")
        if lines_touched is not None:
            lines.append(f"- Lines touched: {lines_touched}")
        lines.extend([
            "",
        ])
    lines.extend([
        "## Deliverables",
        "",
    ])
    lines.extend(_bullet_lines(deliverables))
    lines.extend(["", "## Durable Learnings", ""])
    lines.extend(_bullet_lines(learnings))
    lines.extend(["", "## Open Loops", ""])
    lines.extend(_bullet_lines(open_loops))

    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    tag_list = ["codex-session", "session-log", task_kind] + [tag for tag in tags if tag]
    index_result = index_markdown_artifact(
        path,
        title=f"{title} — Codex Session Log",
        category="session-log",
        tags=tag_list,
        summary=summary.strip(),
    )
    return {
        "status": "created",
        "path": str(path),
        "knowledge_base_id": index_result["knowledge_base_id"],
        "file_id": index_result["file_id"],
        "title": title,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Save durable Codex session logs")
    sub = parser.add_subparsers(dest="command", required=True)

    p_codex = sub.add_parser("codex", help="Save a Codex session log")
    p_codex.add_argument("--title", required=True, help="Session title")
    p_codex.add_argument("--summary", required=True, help="Short session summary")
    p_codex.add_argument("--task-kind", default="general", help="Coarse task family")
    p_codex.add_argument("--deliverable", action="append", default=[], help="Deliverable path or outcome")
    p_codex.add_argument("--learning", action="append", default=[], help="Durable learning from the session")
    p_codex.add_argument("--open-loop", action="append", default=[], help="Outstanding next step or unresolved item")
    p_codex.add_argument("--tag", action="append", default=[], help="Extra tag")
    p_codex.add_argument("--files-changed", type=int, help="Number of files changed in the session")
    p_codex.add_argument("--lines-added", type=int, help="Lines added in the session")
    p_codex.add_argument("--lines-removed", type=int, help="Lines removed in the session")

    args = parser.parse_args()

    if args.command == "codex":
        print(
            json.dumps(
                save_codex_session(
                    title=args.title,
                    summary=args.summary,
                    task_kind=args.task_kind,
                    deliverables=args.deliverable,
                    learnings=args.learning,
                    open_loops=args.open_loop,
                    tags=args.tag,
                    files_changed=args.files_changed,
                    lines_added=args.lines_added,
                    lines_removed=args.lines_removed,
                ),
                indent=2,
            )
        )
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
