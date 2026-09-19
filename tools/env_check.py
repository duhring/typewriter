#!/usr/bin/env python3
"""
Check that the interpreter running this script can build and run PKA.

bootstrap.sh runs this twice: once with the candidate system Python before
creating the virtual environment, and once with an existing environment's
Python before reusing it. It never installs anything. Standard library only,
so it runs on any Python 3.

What it reports:
  path, version, architecture of the running interpreter
  host architecture (uname -m)
  the developer toolchain (clang from xcode-select) and the architectures it
  was built for
  for --venv DIR: whether the environment's base interpreter still exists

What fails the check:
  version_too_old   below --min-version (default 3.11)
  rosetta           interpreter architecture differs from the host's. An
                    Intel Python on Apple Silicon runs under Rosetta and
                    cannot load a native-only toolchain, so packages with
                    C extensions (cryptography, among others) fail to build.
                    --allow-rosetta downgrades this to a warning.
  toolchain_arch    the developer tools do not contain the interpreter's
                    architecture at all: a native build is impossible.
  venv_base_missing the environment's base interpreter (pyvenv.cfg home) is
                    gone; the environment must be removed and recreated.
  venv_arch         the environment's interpreter architecture differs from
                    the host's (a leftover from a Rosetta install).

Exit 0 when the check passes, 1 when it does not. Text report by default;
--json for a machine-readable one.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

DEFAULT_MIN_VERSION = (3, 11)

# Mach-O constants. The fat header is always big-endian; a thin header's magic
# tells us its byte order.
_FAT_MAGIC = 0xCAFEBABE
_MH_MAGIC_64 = 0xFEEDFACF
_MH_MAGIC = 0xFEEDFACE
_CPU_NAMES = {
    0x01000007: "x86_64",
    0x00000007: "i386",
    0x0100000C: "arm64",
    0x0000000C: "arm",
}


def macho_archs(path: Path | str) -> list[str]:
    """Architectures a Mach-O file was built for, or [] if unreadable."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return []
    if len(head) < 8:
        return []
    (magic_be,) = struct.unpack(">I", head[:4])
    if magic_be == _FAT_MAGIC:
        (count,) = struct.unpack(">I", head[4:8])
        archs = []
        for index in range(count):
            offset = 8 + index * 20
            if offset + 4 > len(head):
                break
            (cputype,) = struct.unpack(">I", head[offset:offset + 4])
            archs.append(_CPU_NAMES.get(cputype, hex(cputype)))
        return archs
    for order in ("<", ">"):
        (magic,) = struct.unpack(order + "I", head[:4])
        if magic in (_MH_MAGIC_64, _MH_MAGIC):
            (cputype,) = struct.unpack(order + "I", head[4:8])
            return [_CPU_NAMES.get(cputype, hex(cputype))]
    return []


def host_arch() -> str:
    try:
        return subprocess.check_output(["uname", "-m"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return platform.machine()


def toolchain_clang() -> Path | None:
    try:
        root = subprocess.check_output(
            ["xcode-select", "-p"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        root = ""
    candidates = []
    if root:
        candidates += [Path(root) / "usr" / "bin" / "clang", Path(root) / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "bin" / "clang"]
    found = shutil.which("clang")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def read_pyvenv_cfg(venv: Path) -> dict[str, str]:
    cfg = venv / "pyvenv.cfg"
    result: dict[str, str] = {}
    if not cfg.is_file():
        return result
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def parse_version(text: str) -> tuple[int, int]:
    parts = text.split(".")
    return int(parts[0]), int(parts[1])


def check(
    *,
    interpreter: str = sys.executable,
    version: tuple[int, int, int] = sys.version_info[:3],
    machine: str = platform.machine(),
    host: str | None = None,
    min_version: tuple[int, int] = DEFAULT_MIN_VERSION,
    clang: Path | None | str = "auto",
    clang_archs: list[str] | None = None,
    venv: Path | None = None,
    allow_rosetta: bool = False,
) -> dict:
    """Pure decision. Every environmental fact can be injected for tests."""
    host = host or host_arch()
    if clang == "auto":
        clang = toolchain_clang()
    if clang_archs is None:
        clang_archs = macho_archs(clang) if clang else []

    problems: list[dict] = []
    warnings: list[dict] = []

    if version[:2] < min_version:
        problems.append({
            "code": "version_too_old",
            "message": f"Python {'.'.join(map(str, version))} is below the required {min_version[0]}.{min_version[1]}",
            "fix": "Install a newer Python (brew install python@3.13) or point PKA_PYTHON at one.",
        })

    if machine != host:
        entry = {
            "code": "rosetta",
            "message": f"interpreter is {machine} but this Mac is {host}: it runs under Rosetta",
            "fix": (
                f"Use a native {host} Python: /opt/homebrew/bin/python3 on Apple Silicon "
                f"(brew install python@3.13), or set PKA_PYTHON=/path/to/native/python3 and re-run."
            ),
        }
        (warnings if allow_rosetta else problems).append(entry)

    if clang_archs and machine not in clang_archs:
        problems.append({
            "code": "toolchain_arch",
            "message": (
                f"developer tools at {clang} are built for {', '.join(clang_archs)} only; "
                f"a {machine} interpreter cannot use them to build native packages such as cryptography"
            ),
            "fix": f"Use a {'/'.join(clang_archs)} Python, or install developer tools that include {machine}.",
        })

    venv_report: dict | None = None
    if venv is not None:
        venv = Path(venv)
        cfg = read_pyvenv_cfg(venv)
        home = cfg.get("home", "")
        base_exists = bool(home) and any((Path(home) / name).exists() for name in ("python3", "python", f"python{version[0]}.{version[1]}"))
        venv_report = {"path": str(venv), "home": home, "version": cfg.get("version", ""), "base_exists": base_exists}
        if not cfg:
            problems.append({
                "code": "venv_broken",
                "message": f"{venv} has no pyvenv.cfg; it is not a virtual environment",
                "fix": f"Remove it (rm -rf {venv}) and re-run bootstrap.",
            })
        elif not base_exists:
            problems.append({
                "code": "venv_base_missing",
                "message": f"the environment's base interpreter is gone (pyvenv.cfg home = {home or '?'})",
                "fix": f"Remove it (rm -rf {venv}) and re-run bootstrap.",
            })
        if machine != host:
            # Reported once above as rosetta; name the remedy for an environment.
            problems.append({
                "code": "venv_arch",
                "message": f"the environment's interpreter is {machine} on a {host} Mac",
                "fix": f"Remove it (rm -rf {venv}) and re-run bootstrap with a native Python.",
            })

    return {
        "ok": not problems,
        "python": interpreter,
        "version": ".".join(map(str, version)),
        "machine": machine,
        "host": host,
        "rosetta": machine != host,
        "toolchain": {"clang": str(clang) if clang else None, "archs": clang_archs},
        "venv": venv_report,
        "problems": problems,
        "warnings": warnings,
    }


def render(report: dict) -> str:
    lines = [
        f"python: {report['python']}",
        f"version: {report['version']}  arch: {report['machine']}  host: {report['host']}",
    ]
    tc = report["toolchain"]
    if tc["clang"]:
        lines.append(f"toolchain: {tc['clang']} ({', '.join(tc['archs']) or 'unknown arch'})")
    else:
        lines.append("toolchain: no clang found (xcode-select --install)")
    if report["venv"]:
        v = report["venv"]
        lines.append(f"venv: {v['path']} (base {v['home'] or '?'}, {'present' if v['base_exists'] else 'MISSING'})")
    for entry in report["warnings"]:
        lines.append(f"warning [{entry['code']}]: {entry['message']}")
    for entry in report["problems"]:
        lines.append(f"PROBLEM [{entry['code']}]: {entry['message']}")
        lines.append(f"  fix: {entry['fix']}")
    lines.append("result: ok" if report["ok"] else "result: incompatible")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the running Python against this Mac's toolchain.")
    parser.add_argument("--host-arch", help="Override the host architecture (default: uname -m)")
    parser.add_argument("--venv", help="Also validate this virtual environment (run with its python)")
    parser.add_argument("--min-version", default="3.11")
    parser.add_argument("--allow-rosetta", action="store_true", help="Downgrade a Rosetta interpreter to a warning")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = check(
        host=args.host_arch,
        min_version=parse_version(args.min_version),
        venv=Path(args.venv) if args.venv else None,
        allow_rosetta=args.allow_rosetta,
    )
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
