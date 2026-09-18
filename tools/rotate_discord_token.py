#!/usr/bin/env python3
"""
Read a Discord bot token from the clipboard, validate it, and replace
the DISCORD_BOT_TOKEN= line in discord-bridge/.env.

Usage:
  1. Discord Developer Portal -> Reset Token, copy the new token.
  2. Without copying anything else:
     discord-bridge/venv/bin/python3 tools/rotate_discord_token.py
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys


def main() -> int:
    raw = subprocess.check_output(["pbpaste"]).decode()
    tok = "".join(raw.split())  # strip ALL whitespace including embedded newlines
    if not re.match(r"^[A-Za-z0-9._-]{50,100}$", tok):
        print(f"clipboard does not look like a Discord token (len={len(tok)})", file=sys.stderr)
        return 1

    env_path = pathlib.Path(__file__).resolve().parent.parent / "discord-bridge" / ".env"
    lines = env_path.read_text().splitlines()
    out: list[str] = []
    replaced = False
    for ln in lines:
        if ln.startswith("DISCORD_BOT_TOKEN="):
            out.append(f"DISCORD_BOT_TOKEN={tok}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"DISCORD_BOT_TOKEN={tok}")

    env_path.write_text("\n".join(out) + "\n")
    print(f"DISCORD_BOT_TOKEN updated, length={len(tok)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
