#!/usr/bin/env python3
"""
Daily machine heartbeat — liveness signal for the PKA federation.

Posts one line to Discord with machine_id, role, db_write, and the sha256 of
docs/federation.md, and records the same payload locally in
data/heartbeat-<machine_id>.json. Under federation each machine is an
independent peer; the heartbeat confirms a machine is alive and which
operating agreement it is running. (The retired Devendorf contract used the
hash for cross-machine drift detection; federation keeps it as a simple
version stamp of the local operating agreement.)

Channel: discord_channels.machine_status from config/machine.json if set,
otherwise the default #larry channel.

Usage:
    python3 tools/machine_heartbeat.py            # post + record
    python3 tools/machine_heartbeat.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import discord_send  # noqa: E402
from machine_role import load_machine, db_write_allowed  # noqa: E402

PKA_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = PKA_ROOT / "docs" / "federation.md"


def contract_hash() -> str:
    try:
        return hashlib.sha256(CONTRACT.read_bytes()).hexdigest()[:12]
    except FileNotFoundError:
        return "MISSING"


def main() -> int:
    parser = argparse.ArgumentParser(description="Post daily machine heartbeat")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    m = load_machine()
    machine_id = m.get("machine_id", "unknown")
    payload = {
        "machine_id": machine_id,
        "role": m.get("role", "primary"),
        "db_write": db_write_allowed(),
        "contract_sha256_12": contract_hash(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    state = PKA_ROOT / "data" / f"heartbeat-{machine_id}.json"
    state.write_text(json.dumps(payload, indent=2) + "\n")

    channel = (m.get("discord_channels") or {}).get("machine_status")
    if channel:
        discord_send.CHANNEL_ID = channel
    line = (f"💓 **{machine_id}** role={payload['role']} "
            f"db_write={str(payload['db_write']).lower()} "
            f"contract={payload['contract_sha256_12']}")
    try:
        discord_send.send_message(line, dry_run=args.dry_run)
    except SystemExit as exc:
        print(f"heartbeat: send failed ({exc.code}); recorded locally", file=sys.stderr)
        return 1
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
