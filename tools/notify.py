#!/usr/bin/env python3
"""
Outbound notifier for PKA — lets Larry speak first.

Two modes:

1. Send a notification line to the #larry Discord channel:
       python3 tools/notify.py --level warn --source morning-brief "calendar auth expired"
       echo "details" | python3 tools/notify.py --level fail --source dreamer

2. Run a command and push a ⚠️ only if it fails (the wrapper for scheduled
   jobs, so background work stops failing silently into a log nobody reads):
       python3 tools/notify.py --run --source dreamer-nightly -- claude --print "..."

Notifications never raise into the caller: a send failure is logged to stderr,
never allowed to mask or compound the job it is reporting on. Reuses the
existing discord_send REST path, so it works from cron/launchd without the bot
process running.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Reuse the existing direct-REST send path (loads .env on import).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import discord_send  # noqa: E402

PREFIX = {"ok": "✅", "info": "ℹ️", "warn": "⚠️", "fail": "❌"}


def _format(message: str, level: str, source: str | None) -> str:
    emoji = PREFIX.get(level, "ℹ️")
    if source:
        return f"{emoji} **{source}** — {message}"
    return f"{emoji} {message}"


def notify(message: str, level: str = "info", source: str | None = None,
           dry_run: bool = False) -> bool:
    """Post a formatted notification. Returns True on success, never raises."""
    text = _format(message, level, source)
    try:
        discord_send.send_message(text, dry_run=dry_run)
        return True
    except SystemExit as exc:  # discord_send exits non-zero on config/API failure
        print(f"notify: discord_send exited with {exc.code}", file=sys.stderr)
    except Exception as exc:
        print(f"notify: failed to send: {exc}", file=sys.stderr)
    return False


def run_and_notify(cmd: list[str], source: str | None = None,
                   notify_success: bool = False, dry_run: bool = False) -> int:
    """Run cmd; push a failure notification on non-zero exit. Returns child's exit code."""
    label = source or (cmd[0] if cmd else "job")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except Exception as exc:
        notify(f"failed to launch: {exc}", level="fail", source=label, dry_run=dry_run)
        return 127

    rc = proc.returncode
    if rc != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1200:]
        body = f"exited {rc}"
        if tail:
            body += f"\n```\n{tail}\n```"
        notify(body, level="fail", source=label, dry_run=dry_run)
    elif notify_success:
        notify("completed", level="ok", source=label, dry_run=dry_run)

    # Pass the child's output through so the surrounding log still captures it.
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return rc


def main() -> int:
    # Split off the wrapped command at the first bare "--".
    raw = sys.argv[1:]
    cmd: list[str] | None = None
    if "--" in raw:
        idx = raw.index("--")
        cmd = raw[idx + 1:]
        raw = raw[:idx]

    parser = argparse.ArgumentParser(description="PKA outbound notifier")
    parser.add_argument("message", nargs="?", help="Notification text (or pipe via stdin)")
    parser.add_argument("--level", choices=["ok", "info", "warn", "fail"], default="info")
    parser.add_argument("--source", help="Short label for the job/workflow")
    parser.add_argument("--run", action="store_true",
                        help="Run the command after `--`, notifying only on failure")
    parser.add_argument("--notify-success", action="store_true",
                        help="In --run mode, also notify on success")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be sent without sending")
    args = parser.parse_args(raw)

    if args.run:
        if not cmd:
            parser.error("--run requires a command after `--`")
        return run_and_notify(cmd, source=args.source,
                              notify_success=args.notify_success, dry_run=args.dry_run)

    if args.message:
        message = args.message
    elif not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    else:
        parser.error("no message given (pass text, pipe stdin, or use --run)")

    if not message:
        parser.error("empty message")

    ok = notify(message, level=args.level, source=args.source, dry_run=args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
