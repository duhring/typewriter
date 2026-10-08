#!/usr/bin/env python3
"""
Session clean-up and closeout CLI for Typewriter / PKA.

Usage:
    bin/pka session-cleanup [--slug SLUG] [--summary "SUMMARY"] [--log] [--dry-run]

Cleans ephemeral scratch files from tmp/, verifies that session deliverables
in owners-inbox/ have copies in the user's Downloads folder, and optionally records
a clean closeout log.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, date
from pathlib import Path

# Ensure tools/ is on sys.path
_tools_dir = str(Path(__file__).resolve().parent)
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

PKA_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PKA_ROOT / "tmp"
OWNERS_INBOX = PKA_ROOT / "owners-inbox"
DOWNLOADS_DIR = Path.home() / "Downloads"


def clean_scratch(tmp_dir: Path | None = None, dry_run: bool = False) -> list[str]:
    """Remove ephemeral files and directories inside tmp/."""
    target_dir = tmp_dir or TMP_DIR
    cleaned = []
    if not target_dir.exists():
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
        return cleaned

    for item in target_dir.iterdir():
        if item.name.startswith(".") or item.name == ".gitkeep":
            continue
        cleaned.append(item.name)
        if not dry_run:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
            else:
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    pass
    return cleaned


def mirror_to_downloads(
    slug: str | None = None,
    downloads_dir: Path | None = None,
    owners_inbox: Path | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Ensure key artifacts from owners-inbox have copies in ~/Downloads/."""
    target_downloads = downloads_dir or DOWNLOADS_DIR
    target_inbox = owners_inbox or OWNERS_INBOX
    if not dry_run:
        target_downloads.mkdir(parents=True, exist_ok=True)

    mirrored = []
    search_paths: list[Path] = []

    if slug:
        dev_dir = target_inbox / "development" / slug
        if dev_dir.exists():
            search_paths.extend(dev_dir.glob("*"))
        pres_dir = target_inbox / "presentations"
        if pres_dir.exists():
            search_paths.extend(pres_dir.glob(f"*{slug}*"))
        blog_dir = target_inbox / "blog"
        if blog_dir.exists():
            search_paths.extend(blog_dir.glob(f"*{slug}*"))
    else:
        # Check files modified today across development, presentations, briefs, and blog
        today = date.today()
        for sub in ["development", "presentations", "briefs", "blog"]:
            sub_dir = target_inbox / sub
            if not sub_dir.exists():
                continue
            for item in sub_dir.rglob("*"):
                if item.is_file() or (item.is_dir() and item.suffix == ".cuecam"):
                    try:
                        mtime = datetime.fromtimestamp(item.stat().st_mtime).date()
                        if mtime == today:
                            search_paths.append(item)
                    except OSError:
                        pass

    for source in search_paths:
        if source.name.startswith(".") or source.name == ".gitkeep":
            continue
        # Mirror cuecam bundles, images, markdown briefs/drafts/storyboards, and json registers
        if source.suffix in [".cuecam", ".jpg", ".jpeg", ".png", ".md", ".json"]:
            dest = target_downloads / source.name
            if not dry_run:
                try:
                    if source.is_dir() and source.suffix == ".cuecam":
                        if dest.exists():
                            shutil.rmtree(dest, ignore_errors=True)
                        shutil.copytree(source, dest)
                    elif source.is_file():
                        shutil.copy2(source, dest)
                except Exception:
                    continue
            mirrored.append(str(dest))

    return list(dict.fromkeys(mirrored))


def cleanup_session(
    *,
    slug: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    dry_run: bool = False,
    log: bool = False,
    tmp_dir: Path | None = None,
    downloads_dir: Path | None = None,
    owners_inbox: Path | None = None,
) -> dict:
    """Run full session clean-up."""
    cleaned_scratch = clean_scratch(tmp_dir=tmp_dir, dry_run=dry_run)
    mirrored_files = mirror_to_downloads(
        slug=slug, downloads_dir=downloads_dir, owners_inbox=owners_inbox, dry_run=dry_run
    )

    log_path = None
    if (log or title or summary) and not dry_run:
        try:
            from session_log import save_codex_session
            res = save_codex_session(
                title=title or "Session Closeout",
                summary=summary or "Session ended; workspace cleaned and deliverables verified.",
                task_kind="cleanup",
                deliverables=mirrored_files,
                learnings=[],
                open_loops=[],
                tags=["session-close", "cleanup"],
                files_changed=None,
                lines_added=None,
                lines_removed=None,
            )
            log_path = res.get("path")
        except Exception:
            pass

    return {
        "status": "clean",
        "scratch_cleaned": cleaned_scratch,
        "mirrored_to_downloads": mirrored_files,
        "session_log": log_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Session clean-up and closeout CLI for Typewriter / PKA"
    )
    parser.add_argument("--slug", help="Project slug to mirror artifacts for")
    parser.add_argument("--title", help="Optional title for the session closeout log")
    parser.add_argument("--summary", help="Optional summary of session achievements")
    parser.add_argument("--log", action="store_true", help="Save a durable session log")
    parser.add_argument(
        "--dry-run", action="store_true", help="Check what would be cleaned without modifying disk"
    )
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    args = parser.parse_args()

    result = cleanup_session(
        slug=args.slug,
        title=args.title,
        summary=args.summary,
        dry_run=args.dry_run,
        log=args.log or bool(args.title or args.summary),
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("Typewriter Session Clean-up")
    print("---------------------------")
    if result["scratch_cleaned"]:
        print(f"✓ Ephemeral scratch files removed ({len(result['scratch_cleaned'])} items):")
        for item in result["scratch_cleaned"]:
            print(f"    - {item}")
    else:
        print("✓ Scratch directory (tmp/) is clean.")

    if result["mirrored_to_downloads"]:
        print(f"✓ Verified / copied to Downloads ({len(result['mirrored_to_downloads'])} items):")
        for item in result["mirrored_to_downloads"]:
            print(f"    - {item}")
    else:
        print("✓ Downloads folder checked.")

    if result["session_log"]:
        print(f"✓ Session closeout logged: {result['session_log']}")

    print("\nSession cleanly concluded. Your personal data remains on your machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
