#!/usr/bin/env python3
"""
Bot liveness check — alerts #larry when the Discord bot process is down.

Runs every 15 minutes from cron. Sends via discord_send's direct REST path,
which works without the bot process running. Throttled by a state file in
data/healthchecks/ so a down bot alerts once immediately, then every 2 hours
while it stays down, plus a recovery note when it comes back.

Usage:
    python3 tools/bot_liveness.py            # check + alert
    python3 tools/bot_liveness.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # noqa: E402

PKA_ROOT = Path(__file__).resolve().parent.parent
# Not in data/healthchecks/ — pka_health.py treats that dir as check specs.
STATE_PATH = PKA_ROOT / "data" / "bot-liveness-state.json"
REALERT_SECONDS = 2 * 3600


def bot_running() -> bool:
    # The bot runs as "Python -u bot.py" (cwd discord-bridge), so match the
    # literal script name, escaped so the dot can't match e.g. bot_py.
    out = subprocess.run(["pgrep", "-f", r"bot\.py"], capture_output=True, text=True)
    return out.returncode == 0 and out.stdout.strip() != ""


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Alert when the Discord bot is down")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = time.time()
    state = load_state()
    up = bot_running()

    if up:
        if state.get("down_since"):
            mins = int((now - state["down_since"]) / 60)
            notify.notify(f"bot.py is back up after ~{mins} min down.",
                          level="ok", source="bot-liveness", dry_run=args.dry_run)
        save_state({"down_since": None, "last_alert": None,
                    "last_check": datetime.now().isoformat(timespec="seconds")})
        print("bot.py running")
        return 0

    down_since = state.get("down_since") or now
    last_alert = state.get("last_alert")
    if last_alert is None or now - last_alert >= REALERT_SECONDS:
        since = datetime.fromtimestamp(down_since).strftime("%H:%M")
        notify.notify(
            f"bot.py is NOT running (down since ~{since}). "
            f"Restart: discord-bridge/start-bot.sh",
            level="fail", source="bot-liveness", dry_run=args.dry_run)
        last_alert = now
    save_state({"down_since": down_since, "last_alert": last_alert,
                "last_check": datetime.now().isoformat(timespec="seconds")})
    print("bot.py DOWN", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
