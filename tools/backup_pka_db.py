#!/usr/bin/env python3
"""
Nightly backup of data/pka.db to local disk, off the network volume.

SQLite on a network mount is the single biggest data-loss risk in PKA:
one bad unmount can corrupt the only copy. This uses the SQLite online
backup API (safe while the bot has the DB open), writes a dated copy to
~/PKA-backups/, verifies it with an integrity check, and prunes old copies.

Usage:
    python3 tools/backup_pka_db.py            # backup + prune (keep 14)
    python3 tools/backup_pka_db.py --keep 30
Exit code is non-zero on any failure so notify.py --run can alert.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
# Override with PKA_BACKUP_DIR if the home directory lives on a network volume.
BACKUP_DIR = Path(os.environ.get("PKA_BACKUP_DIR", "~/PKA-backups")).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup pka.db off the network volume")
    parser.add_argument("--keep", type=int, default=14, help="Dated backups to retain")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found (volume unmounted?)", file=sys.stderr)
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"pka-{datetime.now():%Y-%m-%d}.db"
    tmp = dest.with_suffix(".db.partial")

    try:
        src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp)
        with dst:
            src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        src.close()
        dst.close()
        if result != "ok":
            print(f"ERROR: integrity_check on backup returned: {result}", file=sys.stderr)
            tmp.unlink(missing_ok=True)
            return 1
        tmp.replace(dest)
    except Exception as exc:
        print(f"ERROR: backup failed: {exc}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return 1

    backups = sorted(BACKUP_DIR.glob("pka-*.db"))
    for old in backups[:-args.keep]:
        old.unlink()

    size_mb = dest.stat().st_size / 1e6
    print(f"backup ok: {dest} ({size_mb:.1f} MB), {min(len(backups), args.keep)} retained")
    return 0


if __name__ == "__main__":
    sys.exit(main())
