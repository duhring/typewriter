#!/usr/bin/env python3
"""
Auto-index sweep — closes the definition-of-done gap pka_health.py detects.

Finds owners-inbox markdown files with no knowledge_base row (same rule as
check_indexing_coverage in pka_health.py) and indexes each one via
pka_index.py index-markdown, inferring the category from its directory.

Usage:
    python3 tools/index_sweep.py             # index everything unindexed
    python3 tools/index_sweep.py --dry-run   # list what would be indexed
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"
VENV_PY = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"

# owners-inbox subdirectory -> canonical category (see data/categories.json)
DIR_CATEGORY = {
    "youtube": "youtube",
    "transcripts": "transcript",
    "transcript-extracts": "transcript-extract",
    "sop-promotions": "sop-promotion",
    "editorial-recurrences": "editorial-recurrence",
    "briefs": "brief",
    "research": "research",
    "blog": "blog",
    "session-logs": "session-log",
}
DEFAULT_CATEGORY = "knowledge-base"
CATEGORIES_FILE = PKA_ROOT / "data" / "categories.json"
DEVELOPMENT_FILENAME_CATEGORY = {
    "interview.md": "development-interview",
    "outline.md": "development-outline",
    "brief.md": "development-brief",
    "balance-check.md": "development-balance-check",
    "sourced-figures.md": "development-sourced-figures",
    "image-prompts.md": "development-assets",
    "challenge.md": "development-challenge",
}


def find_unindexed() -> list[str]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    indexed = {r[0] for r in conn.execute(
        "select source_file from knowledge_base where source_file is not null")}
    conn.close()
    unindexed = []
    for md in (PKA_ROOT / "owners-inbox").rglob("*.md"):
        rel = str(md.relative_to(PKA_ROOT))
        if "/tasks/" in rel:  # task records are canonical state, not deliverables
            continue
        if rel not in indexed:
            unindexed.append(rel)
    return sorted(unindexed)


def infer_category(rel: str) -> str:
    path = PKA_ROOT / rel
    try:
        text = path.read_text(encoding="utf-8")
        if text.startswith("---\n"):
            front = text.split("\n---\n", 1)[0]
            for line in front.splitlines()[1:]:
                if line.lower().startswith("category:"):
                    declared = line.split(":", 1)[1].strip().strip('"\'')
                    categories = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
                    if declared in categories and not declared.startswith("_"):
                        return declared
                    break
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    parts = Path(rel).parts  # ('owners-inbox', <subdir>, ...)
    if len(parts) >= 3:
        if parts[1] == "development":
            category = DEVELOPMENT_FILENAME_CATEGORY.get(Path(rel).name)
            if category:
                return category
        return DIR_CATEGORY.get(parts[1], DEFAULT_CATEGORY)
    if Path(rel).name.startswith("dreamer-"):
        return "dreamer"
    return DEFAULT_CATEGORY


def main() -> int:
    parser = argparse.ArgumentParser(description="Index unindexed owners-inbox markdown")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    targets = find_unindexed()
    if not targets:
        print("index sweep: nothing to index")
        return 0

    failures = 0
    for rel in targets:
        category = infer_category(rel)
        if args.dry_run:
            print(f"would index: {rel}  (category: {category})")
            continue
        cmd = [str(VENV_PY), str(PKA_ROOT / "tools" / "pka_index.py"),
               "index-markdown", "--file", rel, "--category", category,
               "--tags", "auto-index-sweep"]
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PKA_ROOT))
        if out.returncode == 0:
            print(f"indexed: {rel}  (category: {category})")
        else:
            failures += 1
            print(f"FAILED: {rel}\n{out.stderr.strip()}", file=sys.stderr)

    if failures:
        print(f"index sweep: {failures} failure(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
