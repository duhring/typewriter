"""CueCam local bundle creation must need no model, no Google packages, no OAuth.

Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

SPEC = 'Title: Smoke deck\n\n1. "First point", say hello\n2. teleprompter: something to read\n'


class TestNoModuleLevelDependencies(unittest.TestCase):
    def test_google_and_requests_are_not_imported_at_module_level(self):
        source = (PKA_ROOT / "tools" / "cuecam.py").read_text(encoding="utf-8")
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        for line in top_level:
            self.assertNotIn("google", line, line)
            self.assertNotIn("requests", line, line)
            self.assertNotIn("llm", line, line)

    def test_no_model_call_remains(self):
        source = (PKA_ROOT / "tools" / "cuecam.py").read_text(encoding="utf-8")
        for token in ("raw_prose", "raw-prose", "normalize_prose_to_spec", "import llm", "chat_text"):
            self.assertNotIn(token, source, token)

    def test_module_imports_with_google_blocked(self):
        """Simulate a machine without the Google client packages installed."""
        code = (
            "import sys, builtins\n"
            "real = builtins.__import__\n"
            "def fake(name, *a, **k):\n"
            "    if name.split('.')[0] in ('google', 'googleapiclient', 'requests'):\n"
            "        raise ImportError('blocked: ' + name)\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = fake\n"
            f"sys.path.insert(0, {str(PKA_ROOT / 'tools')!r})\n"
            "import cuecam\n"
            "print('ok')\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PKA_ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "ok")


class TestComposeLocally(unittest.TestCase):
    def setUp(self):
        import cuecam

        self.cuecam = cuecam
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec = Path(self.tmp.name) / "deck.spec"
        self.spec.write_text(SPEC, encoding="utf-8")

    def test_preview_parses_a_spec_file(self):
        proc = subprocess.run([sys.executable, str(PKA_ROOT / "tools" / "cuecam.py"), "compose", "--preview",
                               "--spec-file", str(self.spec)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "preview")
        self.assertEqual(payload["title"], "Smoke deck")
        self.assertEqual([c["headline"] for c in payload["cards"]][0], "First point")
        self.assertEqual(len(payload["cards"]), 2)

    def test_spec_file_is_required(self):
        proc = subprocess.run([sys.executable, str(PKA_ROOT / "tools" / "cuecam.py"), "compose", "--preview"],
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--spec-file", proc.stderr)

    def test_google_feature_reports_missing_packages_plainly(self):
        code = (
            "import sys, builtins\n"
            "real = builtins.__import__\n"
            "def fake(name, *a, **k):\n"
            "    if name.split('.')[0] in ('google', 'googleapiclient'):\n"
            "        raise ImportError('blocked')\n"
            "    return real(name, *a, **k)\n"
            "builtins.__import__ = fake\n"
            f"sys.path.insert(0, {str(PKA_ROOT / 'tools')!r})\n"
            "import cuecam\n"
            "cuecam._google()\n"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(PKA_ROOT))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stderr.strip().splitlines()[-1])["error"], "google_packages_missing")


if __name__ == "__main__":
    unittest.main()
