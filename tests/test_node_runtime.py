"""tools/node_runtime.py: pick a new-enough node on PATH, else the vendored one,
and refuse a download that does not match its pinned checksum."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import node_runtime  # noqa: E402


def fake_node_dir(root: Path, version: str) -> Path:
    d = root / version
    d.mkdir()
    for name in ("node", "npx"):
        p = d / name
        p.write_text(f"#!/bin/sh\necho {version}\n")
        p.chmod(0o755)
    return d


class NodeRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_enough_node_on_path_is_used(self):
        d = fake_node_dir(self.root, "v22.1.0")
        with mock.patch.dict(os.environ, {"PATH": str(d)}):
            self.assertEqual(node_runtime.system_bin(), d)
            with mock.patch.object(node_runtime, "install") as install:
                self.assertEqual(node_runtime.bin_dir(), d)
                install.assert_not_called()

    def test_old_node_is_ignored_and_pinned_node_is_fetched(self):
        d = fake_node_dir(self.root, "v18.20.0")
        with mock.patch.dict(os.environ, {"PATH": str(d)}), \
             mock.patch.object(node_runtime, "VENDOR", self.root / "vendor-node"), \
             mock.patch.object(node_runtime, "install", return_value=self.root / "fetched") as install:
            self.assertIsNone(node_runtime.system_bin())
            self.assertEqual(node_runtime.bin_dir(), self.root / "fetched")
            install.assert_called_once()

    def test_no_fetch_raises_when_nothing_found(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.root)}), \
             mock.patch.object(node_runtime, "VENDOR", self.root / "vendor-node"):
            with self.assertRaises(node_runtime.NodeError):
                node_runtime.bin_dir(fetch=False)

    def test_checksum_mismatch_is_refused_and_nothing_installed(self):
        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        vendor = self.root / "vendor-node"
        with mock.patch.object(node_runtime, "VENDOR", vendor), \
             mock.patch.object(node_runtime.urllib.request, "urlopen", return_value=Resp(b"not node")):
            with self.assertRaises(node_runtime.NodeError) as caught:
                node_runtime.install(quiet=True)
        self.assertIn("checksum", str(caught.exception))
        self.assertFalse(vendor.exists())

    def test_every_pinned_platform_has_a_sha256(self):
        for key, digest in node_runtime.PINNED.items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$", key)


if __name__ == "__main__":
    unittest.main()
