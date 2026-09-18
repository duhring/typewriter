#!/usr/bin/env python3
"""
PKA health doctor.

Machine-checkable "definition of done" for the PKA operating system. Surfaces
operational entropy before it compounds: version control, KB provenance,
indexing coverage, wiki staleness, category drift, and Discord inbox state.

Run: discord-bridge/venv/bin/python3 tools/pka_health.py
     discord-bridge/venv/bin/python3 tools/pka_health.py --json

Exit code is 0 when every check passes, 1 otherwise (cron-friendly).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
HEALTHCHECKS_DIR = PKA_ROOT / "data" / "healthchecks"


def _resolve_venv_py() -> Path:
    """Find a usable python3, tolerating git-worktree layouts.

    Worktrees don't carry the discord-bridge venv. Try the local path first,
    then walk up looking for an ancestor `discord-bridge/venv/bin/python3`
    (covers `.claude/worktrees/<name>` siblings of the main checkout), then
    fall back to the interpreter running this script.
    """
    local = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"
    if local.exists():
        return local
    for ancestor in PKA_ROOT.parents:
        candidate = ancestor / "discord-bridge" / "venv" / "bin" / "python3"
        if candidate.exists():
            return candidate
    return Path(sys.executable)


VENV_PY = _resolve_venv_py()

CATEGORIES_FILE = PKA_ROOT / "data" / "categories.json"

REQUIRED_DB_TABLES = {
    "contacts",
    "files",
    "journal_entries",
    "knowledge_base",
    "meetings",
    "projects",
}


def _load_categories() -> set[str]:
    """Load the canonical category set from data/categories.json.

    Falls back to an empty set (surfacing a warning) rather than crashing,
    so a missing or malformed file doesn't take down the whole health run.
    """
    try:
        raw = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
        return {k for k in raw if not k.startswith("_")}
    except FileNotFoundError:
        return set()
    except Exception:
        return set()


def _result(name: str, status: str, detail: str, **extra) -> dict:
    return {"check": name, "status": status, "detail": detail, **extra}


def check_git() -> dict:
    if not (PKA_ROOT / ".git").exists():
        return _result("git", "fail", "PKA root is not a git repository.")
    try:
        tracked = subprocess.run(
            ["git", "-C", str(PKA_ROOT), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except subprocess.CalledProcessError as exc:
        return _result("git", "fail", f"git ls-files failed: {exc}")

    leak_re = re.compile(r"(^|/)\.env($|\.)|(^|/)venv/|\.db$")
    leaked = [f for f in tracked if leak_re.search(f) and not f.endswith(".env.example")]
    if leaked:
        return _result("git", "fail",
                        f"{len(leaked)} secret/venv/db file(s) tracked in git.",
                        offenders=leaked[:10])
    return _result("git", "ok",
                   f"git repo present, {len(tracked)} files tracked, no secrets/venv/db leaked.")


def check_db_freshness() -> dict:
    """Confirm the local database exists. Under federation each machine owns
    its own data/pka.db; there is no remote mirror to age-check."""
    if not DB_PATH.exists():
        return _result("db_freshness", "fail",
                       "local database is missing. Run: python3 tools/pka_db.py init")
    return _result("db_freshness", "ok", "local database present.")


def check_db_schema(conn: sqlite3.Connection) -> dict:
    """Fail safely when a database exists but is not a usable PKA mirror."""
    tables = {
        row[0]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    }
    missing = sorted(REQUIRED_DB_TABLES - tables)
    if not missing:
        return _result(
            "db_schema", "ok",
            f"all {len(REQUIRED_DB_TABLES)} required tables are present.",
        )

    # Under federation this machine owns its DB; a missing schema is a real failure.
    return _result(
        "db_schema", "fail",
        f"database is incomplete; missing {len(missing)} required "
        f"table(s). Run: python3 tools/pka_db.py init",
        offenders=missing,
    )


def check_kb_provenance(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "select id, source_file from knowledge_base "
        "where source_file is not null and source_file != '' "
        "and source_file not like 'http%'"
    ).fetchall()
    broken = []
    for kid, sf in rows:
        on_disk = (PKA_ROOT / sf).exists() if not sf.startswith("/") else Path(sf).exists()
        in_files = conn.execute(
            "select 1 from files where filepath = ?", (sf,)
        ).fetchone() is not None
        if not (on_disk or in_files):
            broken.append({"kb_id": kid, "source_file": sf})
    if broken:
        return _result("kb_provenance", "fail",
                       f"{len(broken)} KB row(s) point to a source_file that is "
                       f"neither on disk nor registered in files.",
                       offenders=broken[:10])
    return _result("kb_provenance", "ok",
                   f"all {len(rows)} KB source_file references resolve.")


def check_indexing_coverage(conn: sqlite3.Connection) -> dict:
    """Definition-of-done: durable owners-inbox markdown should have a KB row."""
    indexed = {r[0] for r in conn.execute(
        "select source_file from knowledge_base where source_file is not null"
    )}
    inbox = PKA_ROOT / "owners-inbox"
    unindexed = []
    for md in inbox.rglob("*.md"):
        rel = str(md.relative_to(PKA_ROOT))
        # task records are canonical state, not durable deliverables to index
        if "/tasks/" in rel:
            continue
        if rel not in indexed:
            unindexed.append(rel)
    if unindexed:
        # Under federation every machine indexes its own owners-inbox markdown.
        return _result("indexing_coverage", "warn",
                       f"{len(unindexed)} owners-inbox markdown file(s) lack a "
                       f"knowledge_base row (definition of done).",
                       offenders=sorted(unindexed)[:10])
    return _result("indexing_coverage", "ok",
                   "all durable owners-inbox markdown is indexed.")


def check_wiki_staleness() -> dict:
    try:
        out = subprocess.run(
            [str(VENV_PY), str(PKA_ROOT / "tools" / "wiki_compile.py"), "audit"],
            capture_output=True, text=True, cwd=str(PKA_ROOT), check=True,
        ).stdout
        payload = json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return _result("wiki_staleness", "fail", f"wiki audit failed: {exc}")

    findings = payload.get("findings") or payload.get("stale_pages") or []
    contradictions = payload.get("contradictions") or []
    if findings or contradictions:
        return _result("wiki_staleness", "warn",
                       f"{len(findings)} stale finding(s), "
                       f"{len(contradictions)} contradiction(s) in wiki.",
                       stale=len(findings), contradictions=len(contradictions))
    return _result("wiki_staleness", "ok", "wiki audit clean.")


def check_categories(conn: sqlite3.Connection) -> dict:
    canonical = _load_categories()
    if not canonical:
        return _result("categories", "warn",
                       f"data/categories.json missing or unreadable — "
                       f"cannot validate categories.")
    bad = {}
    for table in ("knowledge_base", "files"):
        for cat, count in conn.execute(
            f"select category, count(*) from {table} "
            f"where category is not null group by category"
        ):
            if cat not in canonical:
                bad.setdefault(cat, {})[table] = count
    if bad:
        return _result("categories", "warn",
                       f"{len(bad)} unregistered category value(s) in use — "
                       f"add to data/categories.json to silence.",
                       offenders=bad)
    return _result("categories", "ok",
                   f"all categories conform to the registered set "
                   f"({len(canonical)} in data/categories.json).")


def check_discord_inbox() -> dict:
    discord = PKA_ROOT / "team-inbox" / "discord"
    if not discord.exists():
        return _result("discord_inbox", "warn", "team-inbox/discord missing.")
    top = [p for p in discord.iterdir() if p.is_file() and not p.name.startswith(".")]
    processed_dir = discord / "processed"
    processed = (
        [p for p in processed_dir.iterdir() if p.is_file()]
        if processed_dir.exists() else []
    )
    # High top-level backlog relative to processed/ signals state drift.
    if len(top) > 200 and len(top) > len(processed):
        return _result("discord_inbox", "warn",
                       f"{len(top)} files in team-inbox/discord/ vs "
                       f"{len(processed)} in processed/ — handled files are not "
                       f"being moved.",
                       top_level=len(top), processed=len(processed))
    return _result("discord_inbox", "ok",
                   f"{len(top)} top-level / {len(processed)} processed.",
                   top_level=len(top), processed=len(processed))


def _run_external_check(spec_path: Path) -> dict:
    """Run one external check defined by a JSON spec in data/healthchecks/."""
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _result(spec_path.stem, "warn", f"unreadable check spec: {exc}")

    name = spec.get("name", spec_path.stem)
    description = spec.get("description", "")
    cmd = spec.get("command")
    timeout = int(spec.get("timeout_seconds", 30))
    on_nonzero = spec.get("on_nonzero", "warn")

    if not cmd:
        return _result(name, "warn", "check spec missing 'command' field")

    # Resolve the executable: if relative and contains a slash, anchor to PKA_ROOT.
    resolved_cmd = list(cmd)
    first = Path(cmd[0])
    if not first.is_absolute() and "/" in cmd[0]:
        resolved_cmd[0] = str(PKA_ROOT / cmd[0])

    try:
        proc = subprocess.run(
            resolved_cmd,
            capture_output=True, text=True,
            timeout=timeout,
            cwd=str(PKA_ROOT),
        )
    except subprocess.TimeoutExpired:
        return _result(name, on_nonzero, f"timed out after {timeout}s")
    except Exception as exc:
        return _result(name, "warn", f"failed to launch: {exc}")

    detail = (proc.stdout or proc.stderr or "").strip() or description
    if proc.returncode == 0:
        return _result(name, "ok", detail or "ok")
    return _result(name, on_nonzero, detail)


def check_external() -> list[dict]:
    """Discover and run all checks registered in data/healthchecks/*.json."""
    if not HEALTHCHECKS_DIR.exists():
        return []
    from machine_role import machine_id, role

    current_role = role()
    current_machine_id = machine_id()
    selected = []
    for path in sorted(HEALTHCHECKS_DIR.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            selected.append(path)  # let _run_external_check report the error
            continue
        roles = spec.get("roles")
        machine_ids = spec.get("machine_ids")
        excluded_machine_ids = spec.get("exclude_machine_ids") or []
        if (
            (not roles or current_role in roles)
            and (not machine_ids or current_machine_id in machine_ids)
            and current_machine_id not in excluded_machine_ids
        ):
            selected.append(path)
    return [_run_external_check(path) for path in selected]


def run_all() -> dict:
    if not DB_PATH.exists():
        checks = [
            check_git(),
            check_db_freshness(),
            _result("kb_provenance", "warn",
                    "data/pka.db missing — run: python3 tools/pka_db.py init"),
            _result("wiki_staleness", "warn",
                    "skipped — wiki audit reads pka.db (see kb_provenance)."),
            check_discord_inbox(),
        ]
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            schema = check_db_schema(conn)
            checks = [check_git(), check_db_freshness(), schema]
            if schema["status"] == "ok":
                checks.extend([
                    check_kb_provenance(conn),
                    check_indexing_coverage(conn),
                    check_wiki_staleness(),
                    check_categories(conn),
                ])
            checks.append(check_discord_inbox())
        finally:
            conn.close()
    checks.extend(check_external())
    statuses = {c["status"] for c in checks}
    overall = "fail" if "fail" in statuses else (
        "attention_needed" if "warn" in statuses else "ok"
    )
    return {"status": overall, "checks": checks}


SYMBOL = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def main() -> int:
    parser = argparse.ArgumentParser(description="PKA health doctor")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON.")
    parser.add_argument(
        "--no-track", action="store_true", help="Do not update repeated-finding recurrence state."
    )
    parser.add_argument(
        "--escalation-threshold", type=int, default=3,
        help="Consecutive daily findings before owner escalation (default: 3).",
    )
    args = parser.parse_args()

    payload = run_all()
    if not args.no_track:
        from health_escalation import update_recurrence

        recurrence = update_recurrence(payload, threshold=max(1, args.escalation_threshold))
        payload["recurrence_state_updated_at"] = recurrence.get("updated_at")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"PKA health: {payload['status']}\n")
        for c in payload["checks"]:
            print(f"  [{SYMBOL[c['status']]}] {c['check']}: {c['detail']}")
            for off in (c.get("offenders") or [])[:5] if isinstance(c.get("offenders"), list) else []:
                print(f"          - {off}")
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
