#!/usr/bin/env python3
"""
Maintain canonical task/project records for active PKA work.

Each task record lives in owners-inbox/tasks/ as a stable markdown file and is
kept in sync with the existing `projects` table so active work has one obvious
home instead of being scattered across notes and deliverables.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from pka_index import index_markdown_artifact


PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
TASKS_DIR = PKA_ROOT / "owners-inbox" / "tasks"
NORMALIZED_STATUSES = {"active", "blocked", "on-hold", "complete", "archived"}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    # Satellite machines get read access only (docs/devendorf-contract.md)
    from machine_role import guard_connection
    return guard_connection(conn, "task records")


def _slugify(text: str, fallback: str = "task") -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:80] or fallback


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


def _bullet_block(items: list[str]) -> list[str]:
    if not items:
        return ["- None."]
    return [f"- {item}" for item in items]


def _project_row(conn: sqlite3.Connection, name: str):
    return conn.execute(
        """
        SELECT id, name, description, status, priority, start_date, due_date, created_at, updated_at
        FROM projects
        WHERE lower(name) = lower(?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (name,),
    ).fetchone()


def _upsert_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    description: str,
    status: str,
    priority: str,
    due_date: str | None,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    row = _project_row(conn, name)
    if row:
        conn.execute(
            """
            UPDATE projects
            SET description = ?, status = ?, priority = ?, due_date = ?, updated_at = ?
            WHERE id = ?
            """,
            (description, status, priority, due_date, now, row[0]),
        )
        return int(row[0])

    cursor = conn.execute(
        """
        INSERT INTO projects (name, description, status, priority, start_date, due_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, description, status, priority, now[:10], due_date, now, now),
    )
    return int(cursor.lastrowid)


def _task_path(name: str) -> Path:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    return TASKS_DIR / f"{_slugify(name)}.md"


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
        else:
            if current is not None:
                buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return frontmatter, sections


def _parse_bullets(section_text: str) -> list[str]:
    items: list[str] = []
    for line in (section_text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
    return [item for item in items if item and item.lower() != "none."]


def _merge_bullets(existing: list[str], new: list[str]) -> list[str]:
    return _dedupe(existing + new)


def _atomic_write(path: Path, text: str) -> None:
    """Replace a markdown record in one filesystem operation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _replace_section(text: str, heading: str, body: str, *, before: str = "Canonical Record") -> str:
    """Replace one level-two section while preserving every unrelated section."""
    lines = text.rstrip().splitlines()
    marker = f"## {heading}"
    start = next((i for i, line in enumerate(lines) if line.strip() == marker), None)
    replacement = [marker, ""] + body.strip().splitlines()

    if start is not None:
        end = len(lines)
        for i in range(start + 1, len(lines)):
            if lines[i].startswith("## "):
                end = i
                break
        lines[start:end] = replacement + [""]
    else:
        insert_at = next(
            (i for i, line in enumerate(lines) if line.strip() == f"## {before}"),
            None,
        )
        if insert_at is None and before != "Canonical Record":
            insert_at = next(
                (i for i, line in enumerate(lines) if line.strip() == "## Canonical Record"),
                None,
            )
        if insert_at is None:
            insert_at = len(lines)
        prefix = [] if insert_at == 0 or lines[insert_at - 1] == "" else [""]
        lines[insert_at:insert_at] = prefix + replacement + [""]
    return "\n".join(lines).strip() + "\n"


def _remove_section(text: str, heading: str) -> str:
    """Remove one level-two section without disturbing neighboring content."""
    lines = text.rstrip().splitlines()
    marker = f"## {heading}"
    start = next((i for i, line in enumerate(lines) if line.strip() == marker), None)
    if start is None:
        return text
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    del lines[start:end]
    while start < len(lines) and start > 0 and lines[start] == "" and lines[start - 1] == "":
        del lines[start]
    return "\n".join(lines).strip() + "\n"


def _set_frontmatter(text: str, updates: dict[str, str]) -> str:
    """Set scalar frontmatter fields without rebuilding the document."""
    if not text.startswith("---\n"):
        return text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return text
    front_lines = parts[0].splitlines()
    remaining = dict(updates)
    for i, line in enumerate(front_lines):
        if ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key in remaining:
            front_lines[i] = f"{key}: {remaining.pop(key)}"
    for key, value in remaining.items():
        front_lines.append(f"{key}: {value}")
    return "\n".join(front_lines) + "\n---\n" + parts[1]


def _remove_frontmatter_field(text: str, key: str) -> str:
    """Remove a machine-local frontmatter field from a shared task record."""
    if not text.startswith("---\n"):
        return text
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return text
    front_lines = [
        line for line in parts[0].splitlines()
        if not (":" in line and line.split(":", 1)[0].strip() == key)
    ]
    return "\n".join(front_lines) + "\n---\n" + parts[1]


def _parse_current_state(section_text: str) -> dict[str, str]:
    state: dict[str, str] = {}
    for item in _parse_bullets(section_text):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        state[key.strip().lower().replace(" ", "_")] = value.strip()
    return state


def _index_task(path: Path, name: str, status: str, priority: str, summary: str, tags: list[str]) -> dict:
    return index_markdown_artifact(
        path,
        title=f"{name} — Task Record",
        category="task-record",
        tags=["task-record", status, priority] + tags,
        summary=summary.strip(),
    )


def _task_markdown(
    *,
    name: str,
    status: str,
    priority: str,
    due_date: str | None,
    summary: str,
    current_focus: list[str],
    related_artifacts: list[str],
    key_decisions: list[str],
    open_loops: list[str],
    tags: list[str],
    created_at: str,
) -> str:
    now = datetime.now().isoformat(timespec="seconds")
    frontmatter = [
        "---",
        f"project_key: {_slugify(name)}",
        f"status: {status}",
        f"priority: {priority}",
        f"created_at: {created_at}",
        f"updated_at: {now}",
        f"title: {json.dumps(name)}",
    ]
    if due_date:
        frontmatter.append(f"due_date: {due_date}")
    if tags:
        frontmatter.append(f"tags: {json.dumps(tags)}")
    frontmatter.extend(["source: task-record", "---", ""])

    lines = frontmatter + [
        f"# {name}",
        "",
        "## Summary",
        "",
        summary.strip() or "None.",
        "",
        "## Current Focus",
        "",
    ]
    lines.extend(_bullet_block(current_focus))
    lines.extend(["", "## Related Artifacts", ""])
    lines.extend(_bullet_block(related_artifacts))
    lines.extend(["", "## Key Decisions", ""])
    lines.extend(_bullet_block(key_decisions))
    lines.extend(["", "## Open Loops", ""])
    lines.extend(_bullet_block(open_loops))
    lines.extend(["", "## Canonical Record", "", f"- This file is the single source of truth for the `{name}` task/project record."])
    return "\n".join(lines).strip() + "\n"


def upsert_task_record(
    *,
    name: str,
    summary: str,
    status: str,
    priority: str,
    due_date: str | None,
    focus: list[str],
    artifact: list[str],
    decision: list[str],
    open_loop: list[str],
    tag: list[str],
) -> dict:
    status = status.strip().lower()
    priority = priority.strip().lower()
    path = _task_path(name)
    created_at = datetime.now().isoformat(timespec="seconds")
    existing_focus: list[str] = []
    existing_artifacts: list[str] = []
    existing_decisions: list[str] = []
    existing_loops: list[str] = []
    existing_summary = ""
    existing_tags: list[str] = []
    existing_text = ""
    checkpointed = False

    if path.exists():
        existing_text = path.read_text(encoding="utf-8")
        frontmatter, sections = _parse_sectioned_markdown(existing_text)
        checkpointed = bool(sections.get("Current State", "").strip())
        if focus and checkpointed:
            raise ValueError(
                "checkpointed task records cannot append --focus; use the checkpoint command to replace current state"
            )
        if checkpointed and status not in NORMALIZED_STATUSES:
            raise ValueError(
                f"checkpointed task status must be one of: {', '.join(sorted(NORMALIZED_STATUSES))}"
            )
        created_at = frontmatter.get("created_at", created_at)
        existing_summary = sections.get("Summary", "").strip()
        existing_focus = _parse_bullets(sections.get("Current Focus", ""))
        existing_artifacts = _parse_bullets(sections.get("Related Artifacts", ""))
        existing_decisions = _parse_bullets(sections.get("Key Decisions", ""))
        existing_loops = _parse_bullets(sections.get("Open Loops", ""))
        tags_raw = frontmatter.get("tags", "")
        if tags_raw:
            try:
                existing_tags = json.loads(tags_raw)
            except json.JSONDecodeError:
                existing_tags = [item.strip() for item in tags_raw.split(",") if item.strip()]

    merged_summary = summary.strip() or existing_summary or "None."
    merged_focus = _merge_bullets(existing_focus, focus)
    merged_artifacts = _merge_bullets(existing_artifacts, artifact)
    merged_decisions = _merge_bullets(existing_decisions, decision)
    merged_loops = _merge_bullets(existing_loops, open_loop)
    merged_tags = _dedupe(existing_tags + [item.strip().lower() for item in tag if item.strip()])

    with _connect() as conn:
        project_id = _upsert_project(
            conn,
            name=name,
            description=merged_summary,
            status=status,
            priority=priority,
            due_date=due_date,
        )

    if checkpointed:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        markdown = _replace_section(existing_text, "Summary", merged_summary, before="Current State")
        markdown = _replace_section(
            markdown, "Related Artifacts", "\n".join(_bullet_block(merged_artifacts))
        )
        markdown = _replace_section(
            markdown, "Key Decisions", "\n".join(_bullet_block(merged_decisions))
        )
        markdown = _replace_section(
            markdown, "Open Loops", "\n".join(_bullet_block(merged_loops))
        )
        markdown = _set_frontmatter(
            markdown,
            {
                "project_key": _slugify(name),
                "status": status,
                "priority": priority,
                "updated_at": now,
                "tags": json.dumps(merged_tags),
            },
        )
        markdown = _remove_frontmatter_field(markdown, "project_id")
        _atomic_write(path, markdown)
        index_result = _index_task(path, name, status, priority, merged_summary, merged_tags)
        return {
            "status": "updated",
            "project_id": project_id,
            "path": str(path),
            "file_id": index_result["file_id"],
            "knowledge_base_id": index_result["knowledge_base_id"],
            "name": name,
        }

    markdown = _task_markdown(
        name=name,
        status=status,
        priority=priority,
        due_date=due_date,
        summary=merged_summary,
        current_focus=merged_focus,
        related_artifacts=merged_artifacts,
        key_decisions=merged_decisions,
        open_loops=merged_loops,
        tags=merged_tags,
        created_at=created_at,
    )
    _atomic_write(path, markdown)

    index_result = _index_task(path, name, status, priority, merged_summary, merged_tags)
    return {
        "status": "updated" if path.exists() else "created",
        "project_id": project_id,
        "path": str(path),
        "file_id": index_result["file_id"],
        "knowledge_base_id": index_result["knowledge_base_id"],
        "name": name,
    }


def checkpoint_task_record(
    *,
    name: str,
    phase: str,
    status: str,
    owner: str,
    objective: str,
    now: str,
    next_action: str,
    blockers: list[str],
    acceptance: list[str],
) -> dict:
    """Replace authoritative current state and archive the previous checkpoint."""
    path = _task_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Task record not found: {path}")
    status = status.strip().lower()
    if status not in NORMALIZED_STATUSES:
        raise ValueError(
            f"status must be one of: {', '.join(sorted(NORMALIZED_STATUSES))}"
        )
    required = {
        "phase": phase,
        "owner": owner,
        "objective": objective,
        "now": now,
        "next": next_action,
    }
    missing = [key for key, value in required.items() if not value.strip()]
    if missing:
        raise ValueError(f"checkpoint fields may not be empty: {', '.join(missing)}")

    text = path.read_text(encoding="utf-8")
    frontmatter, sections = _parse_sectioned_markdown(text)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    previous = sections.get("Current State", "").strip()
    if not previous:
        legacy_focus = sections.get("Current Focus", "").strip()
        if legacy_focus and legacy_focus.lower() != "- none.":
            previous = "**Legacy Current Focus**\n\n" + legacy_focus
    history = sections.get("Checkpoint History", "").strip()
    if previous and "Not checkpointed." not in previous:
        previous_state = _parse_current_state(previous)
        label = previous_state.get("last_checkpoint") or frontmatter.get("updated_at") or "pre-checkpoint"
        entry = f"### {label}\n\n{previous}"
        history = f"{history}\n\n{entry}".strip() if history else entry

    blocker_items = _dedupe(blockers)
    acceptance_items = _dedupe(acceptance)
    current_lines = [
        f"- Phase: {phase.strip()}",
        f"- State: {status}",
        f"- Owner: {owner.strip()}",
        f"- Objective: {objective.strip()}",
        f"- Now: {now.strip()}",
        f"- Next: {next_action.strip()}",
        f"- Blocked by: {'; '.join(blocker_items) if blocker_items else 'None.'}",
        f"- Last checkpoint: {timestamp}",
    ]
    focus_lines = [
        f"- Objective: {objective.strip()}",
        f"- Now: {now.strip()}",
        f"- Next: {next_action.strip()}",
    ]
    acceptance_body = "\n".join(_bullet_block(acceptance_items))
    blockers_body = "\n".join(_bullet_block(blocker_items))

    text = _replace_section(text, "Current State", "\n".join(current_lines), before="Current Focus")
    text = _replace_section(text, "Current Focus", "\n".join(focus_lines), before="Related Artifacts")
    # Reinsert these together so their order stays stable even on older records.
    text = _remove_section(text, "Acceptance")
    text = _remove_section(text, "Blockers")
    text = _replace_section(text, "Blockers", blockers_body, before="Related Artifacts")
    text = _replace_section(text, "Acceptance", acceptance_body, before="Blockers")
    if history:
        text = _replace_section(text, "Checkpoint History", history)
    text = _set_frontmatter(
        text,
        {"project_key": _slugify(name), "status": status, "updated_at": timestamp},
    )
    text = _remove_frontmatter_field(text, "project_id")
    _atomic_write(path, text)

    parsed_frontmatter, parsed_sections = _parse_sectioned_markdown(text)
    summary = parsed_sections.get("Summary", "").strip() or "None."
    priority = parsed_frontmatter.get("priority", "medium").strip().lower()
    try:
        tags = json.loads(parsed_frontmatter.get("tags", "[]"))
    except json.JSONDecodeError:
        tags = []
    with _connect() as conn:
        _upsert_project(
            conn,
            name=name,
            description=summary,
            status=status,
            priority=priority,
            due_date=parsed_frontmatter.get("due_date"),
        )
    index_result = _index_task(path, name, status, priority, summary, tags)
    return {
        "status": "checkpointed",
        "path": str(path),
        "checkpointed_at": timestamp,
        "knowledge_base_id": index_result["knowledge_base_id"],
        "file_id": index_result["file_id"],
        "name": name,
    }


def resolve_open_loop(*, name: str, loop: str, resolution: str) -> dict:
    """Move one uniquely matched active open loop into dated resolved history."""
    path = _task_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Task record not found: {path}")
    text = path.read_text(encoding="utf-8")
    frontmatter, sections = _parse_sectioned_markdown(text)
    loops = _parse_bullets(sections.get("Open Loops", ""))
    active = [item for item in loops if not item.strip().startswith("~~")]
    needle = " ".join(loop.lower().split())
    exact = [item for item in active if " ".join(item.lower().split()) == needle]
    matches = exact or [item for item in active if needle in " ".join(item.lower().split())]
    if not matches:
        raise ValueError(f"no active open loop matches: {loop}")
    if len(matches) > 1:
        raise ValueError(f"open-loop match is ambiguous ({len(matches)} matches); use more text")
    matched = matches[0]
    remaining = [item for item in loops if item != matched]
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    resolved = _parse_bullets(sections.get("Resolved", ""))
    resolved.append(f"{matched} — resolved {timestamp}: {resolution.strip()}")

    text = _replace_section(text, "Open Loops", "\n".join(_bullet_block(remaining)))
    text = _replace_section(text, "Resolved", "\n".join(_bullet_block(resolved)))
    text = _set_frontmatter(text, {"updated_at": timestamp})
    _atomic_write(path, text)

    parsed_frontmatter, parsed_sections = _parse_sectioned_markdown(text)
    status = parsed_frontmatter.get("status", "active").strip().lower()
    priority = parsed_frontmatter.get("priority", "medium").strip().lower()
    summary = parsed_sections.get("Summary", "").strip() or "None."
    try:
        tags = json.loads(parsed_frontmatter.get("tags", "[]"))
    except json.JSONDecodeError:
        tags = []
    index_result = _index_task(path, name, status, priority, summary, tags)
    return {
        "status": "resolved",
        "path": str(path),
        "resolved_loop": matched,
        "resolved_at": timestamp,
        "knowledge_base_id": index_result["knowledge_base_id"],
        "file_id": index_result["file_id"],
        "name": name,
    }


def record_attempt(*, name: str, approach: str, outcome: str, reason: str, result: str) -> dict:
    """Append a dated attempted approach and why it did or did not work."""
    path = _task_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Task record not found: {path}")
    text = path.read_text(encoding="utf-8")
    frontmatter, sections = _parse_sectioned_markdown(text)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    entry = "\n".join([
        f"### {timestamp} — {result}",
        "",
        f"- Approach: {approach.strip()}",
        f"- Outcome: {outcome.strip()}",
        f"- Reason: {reason.strip()}",
    ])
    attempts = sections.get("Attempts", "").strip()
    attempts = f"{attempts}\n\n{entry}".strip() if attempts else entry
    text = _replace_section(text, "Attempts", attempts)
    text = _set_frontmatter(text, {"updated_at": timestamp})
    _atomic_write(path, text)

    parsed_frontmatter, parsed_sections = _parse_sectioned_markdown(text)
    status = parsed_frontmatter.get("status", "active").strip().lower()
    priority = parsed_frontmatter.get("priority", "medium").strip().lower()
    summary = parsed_sections.get("Summary", "").strip() or "None."
    try:
        tags = json.loads(parsed_frontmatter.get("tags", "[]"))
    except json.JSONDecodeError:
        tags = []
    index_result = _index_task(path, name, status, priority, summary, tags)
    return {
        "status": "recorded",
        "path": str(path),
        "attempted_at": timestamp,
        "result": result,
        "knowledge_base_id": index_result["knowledge_base_id"],
        "file_id": index_result["file_id"],
        "name": name,
    }


def audit_task_records(*, stale_days: int = 30) -> dict:
    """Read-only schema and freshness audit over canonical markdown records."""
    now = datetime.now().astimezone()
    records = []
    issue_counts: dict[str, int] = {}
    for path in sorted(TASKS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter, sections = _parse_sectioned_markdown(text)
        current = _parse_current_state(sections.get("Current State", ""))
        issues: list[str] = []
        raw_status = frontmatter.get("status", "").strip()
        normalized_status = raw_status.lower()
        if not raw_status:
            issues.append("missing_status")
        elif normalized_status not in NORMALIZED_STATUSES:
            issues.append("non_normalized_status")
        needs_current_state = normalized_status not in {"complete", "archived"}
        if needs_current_state:
            if not sections.get("Current State", "").strip():
                issues.append("missing_current_state")
            for key in ("objective", "now", "next", "owner", "last_checkpoint"):
                if key not in current:
                    issues.append(f"missing_{key}")
            if not sections.get("Acceptance", "").strip():
                issues.append("missing_acceptance")
        focus_count = len(_parse_bullets(sections.get("Current Focus", "")))
        open_loops = [
            item for item in _parse_bullets(sections.get("Open Loops", ""))
            if not item.strip().startswith("~~")
        ]
        if focus_count > 5:
            issues.append("focus_over_cap")
        checkpoint_age_days = None
        raw_checkpoint = current.get("last_checkpoint") or frontmatter.get("updated_at")
        if raw_checkpoint:
            try:
                checkpoint = datetime.fromisoformat(raw_checkpoint.replace("Z", "+00:00"))
                if checkpoint.tzinfo is None:
                    checkpoint = checkpoint.astimezone()
                checkpoint_age_days = (now - checkpoint).days
                if normalized_status in {"active", "blocked"} and checkpoint_age_days > stale_days:
                    issues.append("stale_checkpoint")
            except ValueError:
                issues.append("invalid_checkpoint_timestamp")
        for issue in issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
        try:
            display_path = str(path.relative_to(PKA_ROOT))
        except ValueError:
            display_path = str(path)
        records.append({
            "path": display_path,
            "title": frontmatter.get("title", path.stem).strip('"'),
            "status": raw_status or None,
            "checkpoint_age_days": checkpoint_age_days,
            "focus_count": focus_count,
            "active_open_loops": len(open_loops),
            "issues": issues,
        })
    return {
        "status": "attention_needed" if issue_counts else "ok",
        "record_count": len(records),
        "issue_counts": issue_counts,
        "records": records,
    }


def sync_local_task_records() -> dict:
    """Rebuild this peer's local project/index rows from shared task markdown.

    Database IDs are intentionally local under federation. Task title and
    `project_key` are the portable identity; a numeric project ID must never be
    trusted after a git sync from another peer.
    """
    records: list[dict] = []
    skipped: list[dict] = []
    with _connect() as conn:
        for path in sorted(TASKS_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            frontmatter, sections = _parse_sectioned_markdown(text)
            raw_title = frontmatter.get("title", "")
            raw_status = frontmatter.get("status", "").strip().lower()
            if not raw_title or raw_status not in NORMALIZED_STATUSES:
                skipped.append({
                    "path": str(path),
                    "reason": "missing title or normalized status",
                })
                continue
            try:
                name = json.loads(raw_title) if raw_title else path.stem.replace("-", " ").title()
            except json.JSONDecodeError:
                name = raw_title.strip('"') or path.stem.replace("-", " ").title()
            status = raw_status
            priority = frontmatter.get("priority", "medium").strip().lower()
            summary = sections.get("Summary", "").strip() or "None."
            project_id = _upsert_project(
                conn,
                name=name,
                description=summary,
                status=status,
                priority=priority,
                due_date=frontmatter.get("due_date"),
            )
            try:
                tags = json.loads(frontmatter.get("tags", "[]"))
            except json.JSONDecodeError:
                tags = []
            records.append({
                "path": path,
                "name": name,
                "status": status,
                "priority": priority,
                "summary": summary,
                "tags": tags,
                "local_project_id": project_id,
            })
    for record in records:
        indexed = _index_task(
            record["path"], record["name"], record["status"],
            record["priority"], record["summary"], record["tags"],
        )
        record["local_file_id"] = indexed["file_id"]
        record["local_knowledge_base_id"] = indexed["knowledge_base_id"]
        record["path"] = str(record["path"])
    return {
        "status": "synced",
        "record_count": len(records),
        "skipped_count": len(skipped),
        "records": records,
        "skipped": skipped,
    }


def show_task(name: str) -> dict:
    path = _task_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Task record not found: {path}")
    return {
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Maintain canonical task records for PKA")
    sub = parser.add_subparsers(dest="command", required=True)

    p_upsert = sub.add_parser("upsert", help="Create or update a task record")
    p_upsert.add_argument("--name", required=True, help="Task or project name")
    p_upsert.add_argument("--summary", default="", help="Short summary of the task")
    p_upsert.add_argument("--status", default="active", help="Task status")
    p_upsert.add_argument("--priority", default="medium", help="Task priority")
    p_upsert.add_argument("--due-date", help="Optional due date YYYY-MM-DD")
    p_upsert.add_argument("--focus", action="append", default=[], help="Current focus bullet")
    p_upsert.add_argument("--artifact", action="append", default=[], help="Related artifact path or note")
    p_upsert.add_argument("--decision", action="append", default=[], help="Key decision")
    p_upsert.add_argument("--open-loop", action="append", default=[], help="Open loop")
    p_upsert.add_argument("--tag", action="append", default=[], help="Extra tag")

    p_show = sub.add_parser("show", help="Show an existing task record")
    p_show.add_argument("--name", required=True, help="Task or project name")

    p_checkpoint = sub.add_parser("checkpoint", help="Replace authoritative current state")
    p_checkpoint.add_argument("--name", required=True, help="Existing task or project name")
    p_checkpoint.add_argument("--phase", required=True, help="Current work phase")
    p_checkpoint.add_argument(
        "--status", default="active", choices=sorted(NORMALIZED_STATUSES), help="Normalized task state"
    )
    p_checkpoint.add_argument("--owner", required=True, help="Current accountable owner")
    p_checkpoint.add_argument("--objective", required=True, help="One governing objective")
    p_checkpoint.add_argument("--now", required=True, help="One action happening now")
    p_checkpoint.add_argument("--next", dest="next_action", required=True, help="One action that follows")
    p_checkpoint.add_argument("--blocker", action="append", default=[], help="Active blocker")
    p_checkpoint.add_argument(
        "--acceptance", action="append", default=[], required=True, help="Acceptance criterion"
    )

    p_resolve = sub.add_parser("resolve-loop", help="Move one open loop into resolved history")
    p_resolve.add_argument("--name", required=True, help="Existing task or project name")
    p_resolve.add_argument("--loop", required=True, help="Exact or uniquely identifying loop text")
    p_resolve.add_argument("--resolution", required=True, help="How or why the loop was resolved")

    p_attempt = sub.add_parser("record-attempt", help="Record an approach, outcome, and reason")
    p_attempt.add_argument("--name", required=True, help="Existing task or project name")
    p_attempt.add_argument("--approach", required=True, help="What was tried")
    p_attempt.add_argument("--outcome", required=True, help="What happened")
    p_attempt.add_argument("--reason", required=True, help="Why it worked, failed, or remains inconclusive")
    p_attempt.add_argument(
        "--result", required=True, choices=["failed", "succeeded", "inconclusive"],
        help="Coarse attempt result",
    )

    p_audit = sub.add_parser("audit", help="Read-only task-record schema and freshness audit")
    p_audit.add_argument("--stale-days", type=int, default=30, help="Age threshold for active checkpoints")

    sub.add_parser(
        "sync-local",
        help="Reconcile this peer's local project/index rows from shared task markdown",
    )

    args = parser.parse_args()

    if args.command == "upsert":
        existed = _task_path(args.name).exists()
        result = upsert_task_record(
            name=args.name,
            summary=args.summary,
            status=args.status,
            priority=args.priority,
            due_date=args.due_date,
            focus=args.focus,
            artifact=args.artifact,
            decision=args.decision,
            open_loop=args.open_loop,
            tag=args.tag,
        )
        result["status"] = "updated" if existed else "created"
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "show":
        print(json.dumps(show_task(args.name), indent=2))
        return 0

    if args.command == "checkpoint":
        print(
            json.dumps(
                checkpoint_task_record(
                    name=args.name,
                    phase=args.phase,
                    status=args.status,
                    owner=args.owner,
                    objective=args.objective,
                    now=args.now,
                    next_action=args.next_action,
                    blockers=args.blocker,
                    acceptance=args.acceptance,
                ),
                indent=2,
            )
        )
        return 0

    if args.command == "resolve-loop":
        print(
            json.dumps(
                resolve_open_loop(
                    name=args.name,
                    loop=args.loop,
                    resolution=args.resolution,
                ),
                indent=2,
            )
        )
        return 0

    if args.command == "record-attempt":
        print(
            json.dumps(
                record_attempt(
                    name=args.name,
                    approach=args.approach,
                    outcome=args.outcome,
                    reason=args.reason,
                    result=args.result,
                ),
                indent=2,
            )
        )
        return 0

    if args.command == "audit":
        print(json.dumps(audit_task_records(stale_days=args.stale_days), indent=2))
        return 0

    if args.command == "sync-local":
        print(json.dumps(sync_local_task_records(), indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
