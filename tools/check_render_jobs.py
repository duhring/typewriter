#!/usr/bin/env python3
"""
Health check: render jobs stuck in 'running'.

Exits 0 (ok) when no jobs have been stuck in 'running' for more than
STALE_AFTER. Exits 1 (warn) with a one-line description when stale jobs
are found — pka_health surfaces this as a ⚠️ in the daily digest.

Drop a new spec in data/healthchecks/ to register additional render-style
state checks. This file is the reference implementation.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JOBS_FILE = Path(__file__).resolve().parent.parent / "data" / "render_jobs.json"
STALE_AFTER = timedelta(hours=1)


def main() -> int:
    if not JOBS_FILE.exists():
        print("no render jobs on record")
        return 0

    try:
        jobs = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"could not read render_jobs.json: {exc}")
        return 1

    now = datetime.now(timezone.utc)
    stuck = []
    done = failed = 0

    for job in jobs.values():
        status = job.get("status", "")
        if status == "done":
            done += 1
        elif status == "failed":
            failed += 1
        elif status == "running":
            started_raw = job.get("started_at", "")
            try:
                started = datetime.fromisoformat(started_raw)
                age = now - started
                if age > STALE_AFTER:
                    mins = int(age.total_seconds() / 60)
                    stuck.append(
                        f"{job.get('slug', '?')} (job {job.get('job_id', '?')}, {mins}m old)"
                    )
            except Exception:
                stuck.append(f"{job.get('slug', '?')} (unknown start time)")

    if stuck:
        print(
            f"{len(stuck)} render job(s) stuck in 'running' >1h: {', '.join(stuck)}"
        )
        return 1

    print(f"render jobs ok ({done} done, {failed} failed, 0 stale)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
