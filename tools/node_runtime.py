#!/usr/bin/env python3
"""
Node.js for HyperFrames, fetched only when it is first needed.

    bin/pka node_runtime status      which node would be used
    bin/pka node_runtime install     fetch the pinned Node into vendor/node now
    bin/pka npx hyperframes ...      run npx (bin/pka node and bin/pka npm work too)

Only HyperFrames needs Node (>= 22); none of the three core paths do. A node
already on PATH is used when it is new enough. Otherwise the official Node
v24.22.0 LTS build for this machine is downloaded from nodejs.org, checked
against a sha256 pinned here (copied from that release's SHASUMS256.txt), and
unpacked into vendor/node/. Nothing outside this folder changes.

From Python: `import node_runtime; node_runtime.npx()` returns the npx to run
and puts the matching node first on PATH for child processes.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "node"
MIN_MAJOR = 22
NODE_VERSION = "v24.22.0"
PINNED = {  # node-<version>-<platform>.tar.gz -> sha256
    "darwin-arm64": "6a9724d9b50a5f22b4816cded441b5a8d9ddae546191a5e5b7efa5a5e2ee26d5",
    "darwin-x64": "495e3f273c645f3c4677d438304cdfc701729ea5dec5728f5c8e76a2dece4b91",
    "linux-x64": "28ea0a438de9a13bd7891fd758eee6c7819ee8af5442dfd8d45bd65e56d4228c",
}


class NodeError(Exception):
    pass


def platform_key() -> str:
    system = {"darwin": "darwin", "linux": "linux"}.get(sys.platform)
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        try:  # a Rosetta process still runs on Apple Silicon
            if subprocess.check_output(["sysctl", "-n", "hw.optional.arm64"], text=True,
                                       stderr=subprocess.DEVNULL).strip() == "1":
                machine = "arm64"
        except (OSError, subprocess.CalledProcessError):
            pass
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(machine)
    key = f"{system}-{arch}"
    if key not in PINNED:
        raise NodeError(f"no pinned Node build for {sys.platform} {machine}; install Node {MIN_MAJOR}+ yourself")
    return key


def node_major(node: str) -> int | None:
    try:
        out = subprocess.check_output([node, "--version"], text=True, stderr=subprocess.DEVNULL).strip()
        return int(out.lstrip("v").split(".")[0])
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def vendored_bin() -> Path:
    return VENDOR / "bin"


def system_bin() -> Path | None:
    """A node on PATH (outside vendor/node) that is new enough."""
    path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if Path(p) != vendored_bin()]
    node = shutil.which("node", path=os.pathsep.join(path))
    if node and (node_major(node) or 0) >= MIN_MAJOR and shutil.which("npx", path=str(Path(node).parent)):
        return Path(node).parent
    return None


def install(quiet: bool = False) -> Path:
    key = platform_key()
    name = f"node-{NODE_VERSION}-{key}"
    url = f"https://nodejs.org/dist/{NODE_VERSION}/{name}.tar.gz"
    if not quiet:
        print(f"Downloading Node {NODE_VERSION} for HyperFrames (one time) ...", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / f"{name}.tar.gz"
        req = urllib.request.Request(url, headers={"User-Agent": "typewriter-node-runtime"})
        with urllib.request.urlopen(req, timeout=120) as r, open(tar_path, "wb") as f:
            shutil.copyfileobj(r, f)
        digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
        if digest != PINNED[key]:
            raise NodeError(f"Node download failed its pinned checksum ({url})")
        with tarfile.open(tar_path, "r:gz") as tar:
            for m in tar.getmembers():
                if m.name.startswith("/") or ".." in Path(m.name).parts:
                    raise NodeError(f"unsafe path in Node archive: {m.name}")
            if hasattr(tarfile, "data_filter"):
                tar.extractall(tmp, filter="tar")
            else:
                tar.extractall(tmp)
        staged = Path(tmp) / name
        if VENDOR.exists():
            shutil.rmtree(VENDOR)
        VENDOR.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(VENDOR))
    if node_major(str(vendored_bin() / "node")) is None:
        raise NodeError("the downloaded Node does not run on this machine")
    if not quiet:
        print(f"Node {NODE_VERSION} installed in vendor/node", file=sys.stderr)
    return vendored_bin()


def bin_dir(fetch: bool = True) -> Path:
    """Directory holding node/npx/npm to use, fetching Node if needed."""
    found = system_bin()
    if found:
        return found
    if (vendored_bin() / "node").exists() and node_major(str(vendored_bin() / "node")):
        return vendored_bin()
    if not fetch:
        raise NodeError("no Node found")
    return install()


def activate(fetch: bool = True) -> Path:
    d = bin_dir(fetch)
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    return d


def npx() -> str:
    return str(activate() / "npx")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv.pop(0) if argv else "status"
    try:
        if cmd == "status":
            sysd = system_bin()
            if sysd:
                print(f"node: {sysd / 'node'} (v{node_major(str(sysd / 'node'))}, on PATH)")
            elif (vendored_bin() / "node").exists():
                print(f"node: {vendored_bin() / 'node'} ({NODE_VERSION}, vendor/node)")
            else:
                print(f"node: none yet; Node {NODE_VERSION} will be fetched on first HyperFrames use")
            return 0
        if cmd == "install":
            install()
            return 0
        if cmd == "exec":
            if not argv or argv[0] not in ("node", "npx", "npm"):
                raise NodeError("usage: node_runtime exec node|npx|npm [args...]")
            d = activate()
            os.execv(str(d / argv[0]), [str(d / argv[0]), *argv[1:]])
        raise NodeError(f"unknown command {cmd!r} (status, install, exec)")
    except (NodeError, OSError) as e:
        print(f"node_runtime: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
