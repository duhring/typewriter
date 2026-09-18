#!/usr/bin/env python3
"""
Daily PKA health digest — the scheduled consumer for the health doctor.

On a clean day posts a single ✅ heartbeat line so you know cron ran and
the system is healthy. On a degraded day posts the full problem list.
Either way, one message at 7:05am; silence means the cron itself is down.

Run:  discord-bridge/venv/bin/python3 tools/health_digest.py
      tools/health_digest.py --quiet      # suppress ok heartbeat (old behaviour)
      tools/health_digest.py --dry-run    # print, don't send
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402
import pka_health  # noqa: E402

SYMBOL = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
SOURCE = "pka-health"

# attention_needed (a warn somewhere) maps to warn; a hard fail maps to fail.
LEVEL_FOR_STATUS = {"ok": "ok", "attention_needed": "warn", "fail": "fail"}


def main() -> int:
    parser = argparse.ArgumentParser(description="PKA daily health digest")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the ok heartbeat — only post on degraded health")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the digest without sending")
    args = parser.parse_args()

    try:
        payload = pka_health.run_all()
    except Exception as exc:
        # The watchdog crashing is itself a finding worth surfacing.
        notify.notify(f"health doctor crashed: {exc}", level="fail",
                      source=SOURCE, dry_run=args.dry_run)
        return 1

    status = payload["status"]
    checks = payload["checks"]

    if status == "ok":
        if args.quiet:
            print("PKA health: ok — heartbeat suppressed.")
            return 0
        # Brief liveness heartbeat — one line proves cron ran and system is clean.
        notify.notify(f"all {len(checks)} checks ok · Larry ready",
                      level="ok", source=SOURCE, dry_run=args.dry_run)
        return 0

    problems = [c for c in checks if c["status"] != "ok"]
    shown = problems or checks
    lines = [f"{SYMBOL.get(c['status'], '•')} {c['check']}: {c['detail']}" for c in shown]
    body = "PKA health: **{}**\n{}".format(status, "\n".join(lines))

    notify.notify(body, level=LEVEL_FOR_STATUS.get(status, "warn"),
                  source=SOURCE, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
