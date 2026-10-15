"""install.sh: the one-line installer. Runs it piped on stdin, as curl | bash does,
against a small local repository with a stand-in bootstrap.sh."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[1] / "install.sh"
FAKE_BOOTSTRAP = """#!/bin/bash
# records its arguments and whether stdin is empty
printf '%s\\n' "$@" > bootstrap-args.txt
if read -r -t 1 line; then echo "stdin had: $line" > bootstrap-stdin.txt; fi
echo bootstrap ran
"""


class InstallScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "upstream"
        self.repo.mkdir()
        (self.repo / "bootstrap.sh").write_text(FAKE_BOOTSTRAP)
        (self.repo / "bootstrap.sh").chmod(0o755)
        (self.repo / "AGENTS.md").write_text("agents\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
            subprocess.run(cmd, cwd=self.repo, env=env, check=True)
        self.env = dict(os.environ, TYPEWRITER_REPO=str(self.repo), HOME=str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def run_piped(self, *args):
        return subprocess.run(["bash", "-s", "--", *args], input=INSTALL.read_text(),
                              env=self.env, capture_output=True, text=True)

    def test_fresh_install_clones_and_runs_bootstrap_with_passthrough(self):
        result = self.run_piped("--with-transcribe")
        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.root / "Typewriter"
        self.assertTrue((target / ".git").is_dir())
        self.assertEqual((target / "bootstrap-args.txt").read_text().split(), ["--with-transcribe"])
        self.assertFalse((target / "bootstrap-stdin.txt").exists(), "bootstrap read the piped script")

    def test_existing_clone_is_reused_not_recloned(self):
        self.assertEqual(self.run_piped("--no-bootstrap").returncode, 0)
        marker = self.root / "Typewriter" / "mine.txt"
        marker.write_text("keep")
        result = self.run_piped()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already at", result.stdout)
        self.assertEqual(marker.read_text(), "keep")

    def test_other_existing_folder_is_refused_and_untouched(self):
        other = self.root / "Typewriter"
        other.mkdir()
        (other / "notes.txt").write_text("private")
        result = self.run_piped()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a Typewriter clone", result.stderr)
        self.assertEqual(sorted(p.name for p in other.iterdir()), ["notes.txt"])

    def test_dir_option_and_no_bootstrap(self):
        target = self.root / "elsewhere" / "PKA"
        result = self.run_piped("--dir", str(target), "--no-bootstrap")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / "bootstrap.sh").exists())
        self.assertFalse((target / "bootstrap-args.txt").exists())

    def test_truncated_download_does_nothing(self):
        text = INSTALL.read_text()
        cut = text[: text.index('main "$@"')]
        result = subprocess.run(["bash", "-s"], input=cut, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.root / "Typewriter").exists())


if __name__ == "__main__":
    unittest.main()
