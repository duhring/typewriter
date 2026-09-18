#!/usr/bin/env python3
"""
Machine identity helper for the PKA federation.

Under federation (docs/federation.md, adopted 2026-07-26) each machine is a
standalone peer with its own `data/pka.db`. There is no primary/satellite
distinction and no single-writer rule: every machine may write its own local
database. Files (tools, docs, video projects, markdown) sync between peers via
git; databases are local-only and never synced.

This module previously enforced the retired Devendorf single-writer contract
via `require_writer()` and `guard_connection()`. Those guards are now no-ops
so DB-writing tools (`pka_index.py`, `journal.py`, `records.py`,
`task_record.py`) work on any peer without code changes at their call sites.
The functions are kept for backward compatibility; they no longer raise or
install authorizers.

Reads config/machine.local.json (gitignored, per-machine) first, then falls
back to config/machine.json (git-tracked). Missing config is treated as a
standalone writer so a fresh clone keeps working.

Usage:
    python3 tools/machine_role.py           # print identity as JSON
    from machine_role import machine_id, role, db_write_allowed
"""

from __future__ import annotations

import json
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
MACHINE_JSON = PKA_ROOT / "config" / "machine.json"
# Per-machine override, gitignored. Under federation this carries each
# machine's own identity; it must never sync between machines.
MACHINE_LOCAL_JSON = PKA_ROOT / "config" / "machine.local.json"


class SatelliteWriteError(RuntimeError):
    """Retained for import compatibility. Never raised under federation."""
    pass


def load_machine() -> dict:
    for path in (MACHINE_LOCAL_JSON, MACHINE_JSON):
        try:
            return json.loads(path.read_text())
        except Exception:
            continue
    return {}


def machine_id() -> str:
    return load_machine().get("machine_id", "unknown")


def role() -> str:
    # Under federation every machine is a writer. Legacy callers may still
    # ask for a "role"; report the configured one, defaulting to "standalone".
    return load_machine().get("role", "standalone")


def db_write_allowed() -> bool:
    # Federation: every machine writes its own local DB. Always true.
    m = load_machine()
    if "db_write" in m:
        return bool(m["db_write"])
    return True


def require_writer(operation: str = "this operation") -> None:
    """No-op under federation. Kept so existing call sites keep working.

    Previously raised SatelliteWriteError on non-primary machines to enforce
    the single-writer rule. Each federated peer now owns its own database, so
    writes are always permitted.
    """
    return None


def guard_connection(conn, operation: str = "this tool"):
    """No-op under federation. Returns the connection unmodified.

    Previously installed a SQLite authorizer that denied writes on satellites.
    Each federated peer now writes its own database, so no authorizer is
    installed.
    """
    return conn


if __name__ == "__main__":
    m = load_machine()
    print(json.dumps({
        "machine_id": m.get("machine_id"),
        "role": role(),
        "db_write": db_write_allowed(),
    }, indent=2))
