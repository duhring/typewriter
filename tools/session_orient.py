#!/usr/bin/env python3
"""PKA Session Orientation CLI

Automates the 5-step Session Start Checklist defined in CLAUDE.md and docs/larry/operations.md:
1. Checks active task records in owners-inbox/tasks/
2. Checks unprocessed incoming notes in team-inbox/discord/
3. Verifies PKA system health (pka_health.py)
4. Displays open loops & active priorities

Usage:
    discord-bridge/venv/bin/python3 tools/session_orient.py [--json]
"""

import sys
import json
import sqlite3
import subprocess
import argparse
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
TASKS_DIR = PKA_ROOT / "owners-inbox" / "tasks"
DISCORD_INBOX = PKA_ROOT / "team-inbox" / "discord"


def get_active_tasks():
    tasks = []
    if not TASKS_DIR.exists():
        return tasks

    for task_file in TASKS_DIR.glob("*.md"):
        try:
            content = task_file.read_text(encoding="utf-8")
            title = task_file.stem
            status = "active"
            focus = ""
            
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                elif "status:" in line.lower():
                    status = line.split(":", 1)[1].strip().lower()
                elif "**Now:**" in line or "**Focus:**" in line or "Focus:" in line:
                    focus = line.split(":", 1)[1].strip()

            if status not in ("completed", "archived", "resolved"):
                tasks.append({
                    "file": task_file.name,
                    "title": title,
                    "status": status,
                    "focus": focus
                })
        except Exception:
            continue
    return tasks


def get_unprocessed_discord():
    if not DISCORD_INBOX.exists():
        return []
    
    unprocessed = []
    for item in DISCORD_INBOX.iterdir():
        if item.is_file() and not item.name.startswith("."):
            unprocessed.append(item.name)
    return unprocessed


def get_health_status():
    try:
        venv_py = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"
        python_exe = str(venv_py) if venv_py.exists() else sys.executable
        
        proc = subprocess.run(
            [python_exe, str(PKA_ROOT / "tools" / "pka_health.py"), "--json"],
            capture_output=True, text=True, cwd=str(PKA_ROOT)
        )
        if proc.stdout:
            data = json.loads(proc.stdout)
            return data.get("status", "unknown"), data.get("summary", "")
    except Exception:
        pass
    return "unknown", "Health check failed to run"


def main():
    parser = argparse.ArgumentParser(description="PKA Session Startup Orientation")
    parser.add_argument("--json", action="store_true", help="Output session state as JSON")
    args = parser.parse_args()

    active_tasks = get_active_tasks()
    unprocessed_discord = get_unprocessed_discord()
    health_status, health_summary = get_health_status()

    session_state = {
        "health_status": health_status,
        "health_summary": health_summary,
        "active_tasks": active_tasks,
        "unprocessed_discord_count": len(unprocessed_discord),
        "unprocessed_discord_samples": unprocessed_discord[:5]
    }

    if args.json:
        print(json.dumps(session_state, indent=2))
        return

    print("==================================================")
    print("           PKA SESSION ORIENTATION                ")
    print("==================================================")
    print(f"System Health : [{health_status.upper()}] - {health_summary}")
    print(f"Discord Inbox : {len(unprocessed_discord)} unprocessed item(s)")
    if unprocessed_discord:
        for f in unprocessed_discord[:5]:
            print(f"  - {f}")
        if len(unprocessed_discord) > 5:
            print(f"  ... (+{len(unprocessed_discord) - 5} more)")

    print(f"\nActive Task Records ({len(active_tasks)}):")
    if active_tasks:
        for t in active_tasks:
            focus_str = f" -> {t['focus']}" if t['focus'] else ""
            print(f"  - [{t['status'].upper()}] {t['title']} ({t['file']}){focus_str}")
    else:
        print("  No active tasks found.")

    print("==================================================")


if __name__ == "__main__":
    main()
