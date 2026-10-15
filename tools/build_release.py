#!/usr/bin/env python3
"""
Build a Typewriter release tarball from a git ref.

    bin/pka build_release --version v1.0.0 [--ref HEAD] [--out dist]

Writes, in --out:
    typewriter-<version>.tar.gz          git archive of <ref>, top folder typewriter-<version>/,
                                         plus VERSION and MANIFEST.sha256 inside it
    typewriter-<version>.tar.gz.sha256   "<sha256>  typewriter-<version>.tar.gz"

VERSION holds the version, the full commit and the build date. MANIFEST.sha256
lists every shipped file as "<sha256>  <path>", sorted. An installation made
from the tarball keeps both files, so `bin/pka update` can later tell system
files the owner changed from files left as shipped.

The release workflow (.github/workflows/release.yml) runs this on each v* tag.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(version: str, ref: str, out: Path, repo: Path = ROOT) -> Path:
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", f"{ref}^{{commit}}"], text=True).strip()
    archive = subprocess.check_output(["git", "-C", str(repo), "archive", "--format=tar", commit])
    top = f"typewriter-{version}"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{top}.tar.gz"

    manifest: list[tuple[str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as src, \
         tarfile.open(target, mode="w:gz", format=tarfile.PAX_FORMAT) as dst:
        for member in src.getmembers():
            if member.name in ("pax_global_header",):
                continue
            data = src.extractfile(member).read() if member.isfile() else None
            if member.isfile():
                manifest.append((member.name, sha256_bytes(data)))
            member.name = f"{top}/{member.name}"
            if member.issym() or member.islnk():
                dst.addfile(member)
            else:
                dst.addfile(member, io.BytesIO(data) if data is not None else None)

        built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        extras = {
            "VERSION": f"version={version}\ncommit={commit}\nbuilt={built}\n".encode(),
            "MANIFEST.sha256": "".join(f"{h}  {p}\n" for p, h in sorted(manifest)).encode(),
        }
        for name, data in extras.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            dst.addfile(info, io.BytesIO(data))

    digest = sha256_bytes(target.read_bytes())
    (out / f"{target.name}.sha256").write_text(f"{digest}  {target.name}\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Typewriter release tarball.")
    parser.add_argument("--version", required=True, help="release name, e.g. v1.0.0")
    parser.add_argument("--ref", default="HEAD", help="git ref to package (default HEAD)")
    parser.add_argument("--out", default="dist", help="output folder (default dist)")
    args = parser.parse_args(argv)
    if "/" in args.version or not args.version.strip():
        parser.error("--version must be a plain name like v1.0.0")
    target = build(args.version, args.ref, Path(args.out))
    print(target)
    print(f"{target}.sha256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
