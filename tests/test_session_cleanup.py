"""Tests for session clean-up and closeout (tools/session_cleanup.py).

No network, no model service. Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

import session_cleanup  # noqa: E402


class TestSessionCleanup(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.tmp_dir = self.root / "tmp"
        self.tmp_dir.mkdir(parents=True)
        self.downloads_dir = self.root / "Downloads"
        self.downloads_dir.mkdir(parents=True)
        self.owners_inbox = self.root / "owners-inbox"
        self.owners_inbox.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_clean_scratch_removes_ephemeral_files(self):
        # Create scratch files and a gitkeep
        (self.tmp_dir / ".gitkeep").write_text("")
        (self.tmp_dir / "scratch.wav").write_text("audio")
        (self.tmp_dir / "temp_folder").mkdir()
        (self.tmp_dir / "temp_folder" / "temp.txt").write_text("data")

        cleaned = session_cleanup.clean_scratch(tmp_dir=self.tmp_dir, dry_run=False)

        self.assertIn("scratch.wav", cleaned)
        self.assertIn("temp_folder", cleaned)
        self.assertNotIn(".gitkeep", cleaned)
        self.assertTrue((self.tmp_dir / ".gitkeep").exists())
        self.assertFalse((self.tmp_dir / "scratch.wav").exists())
        self.assertFalse((self.tmp_dir / "temp_folder").exists())

    def test_mirror_to_downloads_with_slug(self):
        slug = "test-project"
        dev_dir = self.owners_inbox / "development" / slug
        dev_dir.mkdir(parents=True)
        (dev_dir / "brief.md").write_text("# Brief")
        (dev_dir / "storyboard.jpg").write_bytes(b"image")

        mirrored = session_cleanup.mirror_to_downloads(
            slug=slug,
            downloads_dir=self.downloads_dir,
            owners_inbox=self.owners_inbox,
            dry_run=False,
        )

        self.assertEqual(len(mirrored), 2)
        self.assertTrue((self.downloads_dir / "brief.md").exists())
        self.assertTrue((self.downloads_dir / "storyboard.jpg").exists())

    def test_cleanup_session_summary(self):
        (self.tmp_dir / "junk.txt").write_text("junk")
        res = session_cleanup.cleanup_session(
            tmp_dir=self.tmp_dir,
            downloads_dir=self.downloads_dir,
            owners_inbox=self.owners_inbox,
            dry_run=False,
            log=False,
        )

        self.assertEqual(res["status"], "clean")
        self.assertIn("junk.txt", res["scratch_cleaned"])
        self.assertFalse((self.tmp_dir / "junk.txt").exists())


if __name__ == "__main__":
    unittest.main()
