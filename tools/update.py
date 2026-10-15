#!/usr/bin/env python3
"""
Update a Typewriter installation made from a release tarball (no git needed).

    bin/pka update status            what is installed, and which shipped files you changed
    bin/pka update check             is a newer release available?
    bin/pka update fetch [--from F]  download the latest release (or tarball F), verify it,
                                     unpack it into data/tmp/update/, and write a plan
    bin/pka update apply --yes       back up, then apply the plan

Git clones are not updated by this tool: follow docs/updating-typewriter.md
(a reviewed fast-forward or staged integration). This tool is the mechanical
part of that procedure for installations that have MANIFEST.sha256 instead of
git history.

How files are compared. MANIFEST.sha256 records each system file as shipped.
For every file the tool knows three versions: as shipped (base), as it is now
(local), and in the new release (upstream).

    update        you did not change it, the release did      -> replaced
    add           new in the release, nothing at that path    -> added
    keep-local    you changed it, the release did not         -> left alone
    conflict      you changed it and so did the release       -> left alone; the release's
                                                                 copy is saved in the backup
    removed       the release dropped it                      -> left in place, listed
    same          nothing to do

Never touched, whatever the plan says: data/, owners-inbox/, team-inbox/,
vendor/, discord-bridge/venv*, discord-bridge/.env, config/machine.local.json.
New files are still added there when nothing exists at that path (for example
a new .gitkeep).

apply first makes a backup outside the installation (default
~/Typewriter-backups/<timestamp>/): every file it will replace, the old
VERSION and MANIFEST.sha256, and an online backup of data/pka.db through
backup_pka_db. It then writes the files, a new VERSION and MANIFEST.sha256,
runs the unit tests, and saves a receipt in owners-inbox/setup-notes/.

Integrity: the tarball is checked against the release's .sha256 file. That
catches a damaged download, not a replaced release.

Environment: PKA_UPDATE_REPO (default duhring/typewriter), PKA_BACKUP_ROOT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("PKA_UPDATE_ROOT", Path(__file__).resolve().parents[1]))
REPO = os.environ.get("PKA_UPDATE_REPO", "duhring/typewriter")
STAGE = ROOT / "data" / "tmp" / "update"
PROTECTED = ("data/", "owners-inbox/", "team-inbox/", "vendor/", "discord-bridge/venv",
             "discord-bridge/.env", "config/machine.local.json")
META = ("VERSION", "MANIFEST.sha256")


class UpdateError(Exception):
    pass


# --- small helpers -------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                digest, name = line.split("  ", 1)
                out[name] = digest
    return out


def read_version(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def is_git_clone(root: Path) -> bool:
    return (root / ".git").exists()


def protected(rel: str) -> bool:
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in PROTECTED)


def local_hash(rel: str) -> str | None:
    p = ROOT / rel
    return sha256_file(p) if p.is_file() and not p.is_symlink() else None


def http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "typewriter-update", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


# --- release lookup and staging ------------------------------------------------

def latest_release() -> dict:
    data = json.loads(http_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    tag = data["tag_name"]
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    name = f"typewriter-{tag}.tar.gz"
    if name not in assets or f"{name}.sha256" not in assets:
        raise UpdateError(f"release {tag} has no {name} and {name}.sha256 assets")
    return {"tag": tag, "tarball": assets[name], "sha256": assets[f"{name}.sha256"], "name": name}


def safe_extract(tar_path: Path, dest: Path) -> Path:
    """Unpack, refusing absolute paths, '..', and links. Returns the top folder."""
    with tarfile.open(tar_path, "r:gz") as tar:
        members = tar.getmembers()
        tops = {m.name.split("/", 1)[0] for m in members}
        if len(tops) != 1:
            raise UpdateError("tarball must contain exactly one top folder")
        for m in members:
            parts = Path(m.name).parts
            if m.name.startswith("/") or ".." in parts:
                raise UpdateError(f"unsafe path in tarball: {m.name}")
            if m.issym() or m.islnk() or not (m.isfile() or m.isdir()):
                raise UpdateError(f"links and special files are not allowed: {m.name}")
        tar.extractall(dest, members=members, filter="data") if hasattr(tarfile, "data_filter") \
            else tar.extractall(dest, members=members)
    return dest / tops.pop()


def make_plan(upstream_root: Path) -> dict:
    base = read_manifest(ROOT / "MANIFEST.sha256")
    upstream = read_manifest(upstream_root / "MANIFEST.sha256")
    if not upstream:
        raise UpdateError("the release has no MANIFEST.sha256")
    entries = []
    for rel in sorted(set(base) | set(upstream)):
        b, u, loc = base.get(rel), upstream.get(rel), local_hash(rel)
        if u is None:
            action = "removed" if loc == b else "keep-local"
        elif b is None:
            action = "add" if loc is None else ("same" if loc == u else "conflict")
        elif loc == u:
            action = "same"
        elif loc == b:
            action = "update"
        elif u == b:
            action = "keep-local"
        else:
            action = "conflict"
        if protected(rel) and action not in ("add", "same"):
            action = "keep-local" if action != "removed" else "removed"
        entries.append({"path": rel, "action": action, "base": b, "upstream": u, "local": loc})
    return {"upstream_root": str(upstream_root), "version": read_version(upstream_root / "VERSION"),
            "installed": read_version(ROOT / "VERSION"), "entries": entries}


def summarize(plan: dict) -> str:
    counts: dict[str, list[str]] = {}
    for e in plan["entries"]:
        counts.setdefault(e["action"], []).append(e["path"])
    lines = [f"installed: {plan['installed'].get('version', 'unknown')}  ->  release: {plan['version'].get('version', '?')}"]
    for action in ("update", "add", "conflict", "keep-local", "removed"):
        paths = counts.get(action, [])
        if paths:
            lines.append(f"{action}: {len(paths)}")
            lines += [f"  {p}" for p in paths[:40]] + ([f"  ... and {len(paths) - 40} more"] if len(paths) > 40 else [])
    lines.append(f"same: {len(counts.get('same', []))}")
    return "\n".join(lines)


# --- commands --------------------------------------------------------------------

def require_tarball_install() -> None:
    if is_git_clone(ROOT):
        raise UpdateError("this installation is a git clone; update it with git as described in "
                          "docs/updating-typewriter.md (this tool is for tarball installs)")
    if not (ROOT / "MANIFEST.sha256").exists():
        raise UpdateError("no MANIFEST.sha256 here, so shipped and changed files cannot be told apart; "
                          "follow the staged integration in docs/updating-typewriter.md")


def cmd_status(_args) -> int:
    if is_git_clone(ROOT):
        print("install: git clone (update with git; see docs/updating-typewriter.md)")
        return 0
    v = read_version(ROOT / "VERSION")
    base = read_manifest(ROOT / "MANIFEST.sha256")
    print(f"install: tarball  version: {v.get('version', 'unknown')}  commit: {v.get('commit', '?')[:12]}")
    changed = [p for p, h in base.items() if local_hash(p) not in (h,) and not protected(p)]
    print(f"shipped system files you changed or removed: {len(changed)}")
    for p in changed[:40]:
        print(f"  {p}")
    return 0


def cmd_check(_args) -> int:
    installed = read_version(ROOT / "VERSION").get("version", "unknown")
    rel = latest_release()
    if rel["tag"] == installed:
        print(f"up to date ({installed})")
    else:
        print(f"update available: {installed} -> {rel['tag']}  (bin/pka update fetch)")
    return 0


def cmd_fetch(args) -> int:
    require_tarball_install()
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    if args.source:
        tar_path = Path(args.source).resolve()
        side = Path(f"{tar_path}.sha256")
        expected = side.read_text().split()[0] if side.exists() else None
    else:
        rel = latest_release()
        tar_path = STAGE / rel["name"]
        tar_path.write_bytes(http_get(rel["tarball"]))
        expected = http_get(rel["sha256"]).decode().split()[0]
    if expected is None:
        raise UpdateError(f"no {tar_path.name}.sha256 next to the tarball")
    if sha256_file(tar_path) != expected:
        raise UpdateError("the tarball does not match its .sha256 file")
    upstream_root = safe_extract(tar_path, STAGE / "release")
    plan = make_plan(upstream_root)
    (STAGE / "plan.json").write_text(json.dumps(plan, indent=1))
    print(summarize(plan))
    print(f"\nNothing changed yet. Review, then: bin/pka update apply --yes")
    return 0


def cmd_apply(args) -> int:
    require_tarball_install()
    plan_path = STAGE / "plan.json"
    if not plan_path.exists():
        raise UpdateError("no fetched update; run bin/pka update fetch first")
    plan = json.loads(plan_path.read_text())
    upstream_root = Path(plan["upstream_root"])
    # Re-check: local files must not have changed since the plan was made.
    for e in plan["entries"]:
        if e["action"] in ("update", "add") and local_hash(e["path"]) != e["local"]:
            raise UpdateError(f"{e['path']} changed since the plan was made; run fetch again")
    if not args.yes:
        print(summarize(plan))
        print("\nRe-run with --yes to back up and apply.")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(os.environ.get("PKA_BACKUP_ROOT", Path.home() / "Typewriter-backups")) / f"update-{stamp}"
    if backup.resolve().is_relative_to(ROOT.resolve()):
        raise UpdateError("the backup folder must be outside the installation")
    (backup / "files").mkdir(parents=True)
    for name in META:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, backup / name)
    to_write = [e for e in plan["entries"] if e["action"] in ("update", "add")]
    for e in to_write:
        if (ROOT / e["path"]).exists():
            dest = backup / "files" / e["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / e["path"], dest)
    conflicts = [e for e in plan["entries"] if e["action"] == "conflict"]
    for e in conflicts:
        dest = backup / "release-copies-of-conflicts" / e["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(upstream_root / e["path"], dest)
    db_note = "no data/pka.db yet"
    if (ROOT / "data" / "pka.db").exists():
        env = dict(os.environ, PKA_BACKUP_DIR=str(backup / "database"))
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "backup_pka_db.py")], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError(f"database backup failed, nothing applied:\n{r.stderr or r.stdout}")
        db_note = f"backed up to {backup / 'database'}"

    for e in to_write:
        dest = ROOT / e["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(upstream_root / e["path"], dest)

    # New baseline: the release's hashes, except conflicts keep the old base so
    # they stay flagged until someone resolves them.
    new_manifest = read_manifest(upstream_root / "MANIFEST.sha256")
    for e in conflicts:
        if e["base"]:
            new_manifest[e["path"]] = e["base"]
        else:
            new_manifest.pop(e["path"], None)
    (ROOT / "MANIFEST.sha256").write_text("".join(f"{h}  {p}\n" for p, h in sorted(new_manifest.items())))
    shutil.copy2(upstream_root / "VERSION", ROOT / "VERSION")

    if args.skip_tests:
        tests, test_line = None, "skipped (--skip-tests)"
    else:
        tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"], cwd=ROOT,
                               capture_output=True, text=True)
        test_line = (tests.stderr.strip().splitlines() or ["?"])[-1]
    deps_changed = any(e["path"] in ("requirements.txt", "bootstrap.sh") for e in to_write)

    old_v, new_v = plan["installed"].get("version", "unknown"), plan["version"].get("version", "?")
    receipt_dir = ROOT / "owners-inbox" / "setup-notes"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt = receipt_dir / f"{datetime.now():%Y-%m-%d}-typewriter-update-{new_v}.md"
    by = lambda a: [e["path"] for e in plan["entries"] if e["action"] == a]
    receipt.write_text("\n".join([
        f"# Typewriter update {old_v} -> {new_v}", "",
        f"- Date: {datetime.now():%Y-%m-%d %H:%M}",
        f"- Previous: {old_v} ({plan['installed'].get('commit', '?')})",
        f"- Target: {new_v} ({plan['version'].get('commit', '?')})",
        f"- Backup: {backup}", f"- Database: {db_note}",
        f"- Unit tests after applying: {test_line}",
        f"- Dependencies changed: {'yes, run ./bootstrap.sh' if deps_changed else 'no'}", "",
        "## Replaced", *([f"- {p}" for p in by("update")] or ["- none"]), "",
        "## Added", *([f"- {p}" for p in by("add")] or ["- none"]), "",
        "## Conflicts left for review (release copies in the backup)", *([f"- {p}" for p in by("conflict")] or ["- none"]), "",
        "## Kept your changes", *([f"- {p}" for p in by("keep-local")] or ["- none"]), "",
        "## Removed upstream, left in place", *([f"- {p}" for p in by("removed")] or ["- none"]), "",
    ]))
    subprocess.run([sys.executable, str(ROOT / "tools" / "pka_index.py"), "index-markdown", "--file", str(receipt),
                    "--category", "reference"], cwd=ROOT, capture_output=True)
    shutil.rmtree(STAGE, ignore_errors=True)

    print(f"updated {old_v} -> {new_v}: {len(by('update'))} replaced, {len(by('add'))} added, "
          f"{len(conflicts)} conflicts left for review")
    print(f"backup: {backup}")
    print(f"tests: {test_line}")
    print(f"receipt: {receipt.relative_to(ROOT)}")
    if deps_changed:
        print("requirements or bootstrap changed: run ./bootstrap.sh")
    return 0 if tests is None or tests.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update a tarball installation of Typewriter.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    f = sub.add_parser("fetch"); f.add_argument("--from", dest="source"); f.set_defaults(fn=cmd_fetch)
    a = sub.add_parser("apply"); a.add_argument("--yes", action="store_true")
    a.add_argument("--skip-tests", action="store_true", help="do not run the unit tests afterwards")
    a.set_defaults(fn=cmd_apply)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except UpdateError as e:
        print(f"update: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"update: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
