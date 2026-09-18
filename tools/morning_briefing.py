#!/usr/bin/env python3
"""
Generate and send the daily morning briefing to Discord.

Pulls today's calendar events, formats a briefing aligned with
John's AI Native brand (YouTube + Substack), and sends to #larry.

Usage:
    python3 tools/morning_briefing.py
"""

import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# --- Paths -------------------------------------------------------------------
PKA_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"
GCAL = PKA_ROOT / "tools" / "gcal.py"
DISCORD_SEND = PKA_ROOT / "tools" / "discord_send.py"

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _extract_calendar_error(stderr: str) -> str:
    """Return the most useful calendar error line from stderr."""
    ignored_fragments = (
        "FutureWarning",
        "NotOpenSSLWarning",
        "warnings.warn",
        "Traceback (most recent call last):",
    )
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("File "):
            continue
        if any(fragment in line for fragment in ignored_fragments):
            continue
        return line
    return "Calendar data is unavailable."


def get_calendar_events():
    """Fetch today's calendar events via gcal.py."""
    today = date.today().isoformat()
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(GCAL), "list", "--from", today, "--to", today],
            capture_output=True, text=True, timeout=30, cwd=str(PKA_ROOT),
        )
        if result.returncode != 0:
            return [], _extract_calendar_error(result.stderr)
        data = json.loads(result.stdout)
        # gcal.py returns {"events": [...]} or a list directly
        if isinstance(data, list):
            return data, None
        return data.get("events", []), None
    except json.JSONDecodeError:
        return [], "Calendar data returned invalid JSON."
    except Exception as exc:
        return [], f"Calendar data is unavailable: {exc}"


def format_time(iso_time: str) -> str:
    """Convert ISO time string to readable 12-hour format."""
    try:
        # Handle both "2026-03-28T09:00:00-07:00" and "2026-03-28T09:00:00"
        t = iso_time[11:16]  # "HH:MM"
        h, m = int(t[:2]), int(t[3:])
        period = "am" if h < 12 else "pm"
        if h == 0:
            h = 12
        elif h > 12:
            h -= 12
        return f"{h}:{m:02d}{period}" if m else f"{h}{period}"
    except Exception:
        return iso_time


def get_daypart_greeting(now=None) -> str:
    """Return an appropriate greeting for the local time of day."""
    current = now or datetime.now().astimezone()
    hour = current.hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def build_briefing(events: list[dict], calendar_error: str = None) -> str:
    today = date.today()
    dow = DAYS[today.weekday()]
    month = MONTHS[today.month - 1]
    date_str = f"{dow}, {month} {today.day}, {today.year}"
    greeting = get_daypart_greeting()

    lines = [
        f"**{greeting}, John!** Here's your briefing for {date_str}.",
        "",
    ]

    # Calendar section
    lines.append("**Today's Calendar**")
    if calendar_error:
        lines.append(f"• Calendar unavailable — {calendar_error}")
    elif events:
        for event in events:
            title = event.get("summary", event.get("title", "Untitled"))
            start = event.get("start", {})
            # start can be {"dateTime": "..."} or {"date": "..."}
            if isinstance(start, dict):
                dt = start.get("dateTime") or start.get("date", "")
            else:
                dt = str(start)

            if "T" in dt:
                time_str = format_time(dt)
                lines.append(f"• {time_str} — {title}")
            else:
                lines.append(f"• (all day) — {title}")
    else:
        lines.append("• No events scheduled — open runway today.")

    lines.append("")

    # Focus section — aligned with AI Native brand
    lines.append("**Today's Focus Areas**")
    lines.append("• YouTube: Any content ideas, recordings, or edits to move forward?")
    lines.append("• Substack: AI Native is waiting for its first post — got a draft or angle?")
    lines.append("• AI/Tech: What's worth exploring or sharing today?")
    lines.append("")
    lines.append("What are we working on today? 🚀")

    return "\n".join(lines)


def main():
    events, calendar_error = get_calendar_events()
    briefing = build_briefing(events, calendar_error=calendar_error)

    # Send to Discord
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(DISCORD_SEND), briefing],
            capture_output=True, text=True, timeout=15, cwd=str(PKA_ROOT),
        )
        if result.returncode != 0:
            print(f"Discord send failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        print("Morning briefing sent to Discord.")
    except Exception as e:
        print(f"Error sending briefing: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
