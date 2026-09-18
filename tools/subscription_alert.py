#!/usr/bin/env python3
"""
One-shot subscription renewal alert. Fires via cron, sends a Discord
notification, then removes itself from crontab.

Usage: subscription_alert.py <alert-id> <year> <message>
  alert-id  — unique tag used to find and remove the cron entry
  year      — target year (script exits silently if current year < target year)
  message   — the text to send to Discord
"""

import sys
import subprocess
from datetime import datetime
from pathlib import Path

PKA_ROOT = Path(__file__).parent.parent
VENV_PYTHON = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"
DISCORD_SEND = PKA_ROOT / "tools" / "discord_send.py"

def send_discord(message: str) -> None:
    result = subprocess.run(
        [str(VENV_PYTHON), str(DISCORD_SEND), message],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"[subscription_alert] discord_send error: {result.stderr}", file=sys.stderr)
    else:
        print(f"[subscription_alert] Sent: {message[:80]}")


def remove_cron_entry(alert_id: str) -> None:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return
    lines = [l for l in result.stdout.splitlines() if f"# alert:{alert_id}" not in l]
    new_crontab = "\n".join(lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True)
    print(f"[subscription_alert] Removed cron entry for alert:{alert_id}")


def main():
    if len(sys.argv) < 4:
        print("Usage: subscription_alert.py <alert-id> <year> <message>", file=sys.stderr)
        sys.exit(1)

    alert_id = sys.argv[1]
    target_year = int(sys.argv[2])
    message = " ".join(sys.argv[3:])

    current_year = datetime.now().year
    if current_year < target_year:
        # Not yet — exit silently (shouldn't normally happen with correct cron month/day)
        print(f"[subscription_alert] Year {current_year} < target {target_year}, skipping.")
        sys.exit(0)

    send_discord(message)
    remove_cron_entry(alert_id)


if __name__ == "__main__":
    main()
