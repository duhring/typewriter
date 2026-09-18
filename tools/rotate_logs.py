#!/usr/bin/env python3
"""
Log rotation for PKA data/*.log and data/*.err files.

Trims each matching file to the last MAX_LINES lines so post-mortems
survive and logs don't grow unbounded. Run weekly via cron (Monday 6:50am,
before the morning brief) — see crontab for the installed entry.

Usage:
    python3 tools/rotate_logs.py
    python3 tools/rotate_logs.py --max-lines 2000
    python3 tools/rotate_logs.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PKA_ROOT / "data"
PATTERNS = ["*.log", "*.err"]
DEFAULT_MAX_LINES = 2000


def rotate(path: Path, max_lines: int, dry_run: bool) -> tuple[int, int]:
    """Trim path to last max_lines lines. Returns (before, after) line counts."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        print(f"  skip {path.name}: {exc}", file=sys.stderr)
        return 0, 0

    before = len(lines)
    if before <= max_lines:
        return before, before  # nothing to do

    trimmed = lines[-max_lines:]
    if not dry_run:
        path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    return before, len(trimmed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rotate PKA log files")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES,
                        help=f"Lines to keep per file (default {DEFAULT_MAX_LINES})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be trimmed without changing files")
    args = parser.parse_args()

    targets = []
    for pattern in PATTERNS:
        targets.extend(sorted(DATA_DIR.glob(pattern)))

    if not targets:
        print("No log files found.")
        return 0

    trimmed_count = 0
    for path in targets:
        before, after = rotate(path, args.max_lines, args.dry_run)
        if before != after:
            removed = before - after
            flag = " [dry-run]" if args.dry_run else ""
            print(f"  {path.name}: {before} → {after} lines (removed {removed}){flag}")
            trimmed_count += 1
        # else: file already within limit, stay quiet

    if trimmed_count == 0:
        print(f"All {len(targets)} log file(s) within {args.max_lines}-line limit — nothing to rotate.")
    else:
        label = "Would trim" if args.dry_run else "Trimmed"
        print(f"{label} {trimmed_count}/{len(targets)} log file(s) to {args.max_lines} lines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
