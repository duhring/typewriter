"""tools/build_release.py and tools/update.py: build two releases of a small
repository, install the first from its tarball, change some files, update."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REAL = Path(__file__).resolve().parents[1]
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True, capture_output=True)


class ReleaseAndUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.dist, self.backups = t / "dist", t / "backups"
        up = t / "up"
        (up / "tools").mkdir(parents=True)
        for name in ("update.py", "build_release.py", "backup_pka_db.py"):
            shutil.copy(REAL / "tools" / name, up / "tools" / name)
        for rel, text in {"docs/a.md": "a\n", "docs/both.md": "both\n", "docs/mine.md": "mine\n",
                          "docs/gone.md": "gone\n", "owners-inbox/brand.md": "brand\n", "data/.gitkeep": ""}.items():
            (up / rel).parent.mkdir(parents=True, exist_ok=True)
            (up / rel).write_text(text)
        git(up, "init", "-q", "-b", "main"); git(up, "add", "-A"); git(up, "commit", "-qm", "v1")
        self.build(up, "v1")
        (up / "docs/a.md").write_text("a2\n")
        (up / "docs/both.md").write_text("both-upstream\n")
        (up / "docs/new.md").write_text("new\n")
        (up / "owners-inbox/brand.md").write_text("brand-upstream\n")
        (up / "docs/gone.md").unlink()
        git(up, "add", "-A"); git(up, "commit", "-qm", "v2")
        self.build(up, "v2")
        with tarfile.open(self.dist / "typewriter-v1.tar.gz") as tar:
            tar.extractall(t / "inst")
        self.inst = t / "inst" / "typewriter-v1"
        (self.inst / "docs/both.md").write_text("both-mine\n")
        (self.inst / "docs/mine.md").write_text("mine-edited\n")
        (self.inst / "owners-inbox/brand.md").write_text("brand-mine\n")
        (self.inst / "owners-inbox/private.md").write_text("private\n")
        con = sqlite3.connect(self.inst / "data/pka.db"); con.execute("create table t(x)"); con.commit(); con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, repo, version):
        subprocess.run([sys.executable, "tools/build_release.py", "--version", version, "--out", str(self.dist)],
                       cwd=repo, check=True, capture_output=True)

    def update(self, *args):
        env = dict(os.environ, PKA_BACKUP_ROOT=str(self.backups))
        env.pop("PKA_UPDATE_ROOT", None)
        return subprocess.run([sys.executable, "tools/update.py", *args], cwd=self.inst, env=env,
                              capture_output=True, text=True)

    def test_tarball_has_version_and_manifest(self):
        self.assertIn("version=v1", (self.inst / "VERSION").read_text())
        self.assertIn("docs/a.md", (self.inst / "MANIFEST.sha256").read_text())
        side = (self.dist / "typewriter-v1.tar.gz.sha256").read_text().split()
        self.assertEqual(side[1], "typewriter-v1.tar.gz")

    def test_fetch_changes_nothing_and_apply_respects_local_changes(self):
        r = self.update("fetch", "--from", str(self.dist / "typewriter-v2.tar.gz"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.inst / "docs/a.md").read_text(), "a\n", "fetch must not change files")
        self.assertNotEqual(self.update("apply").returncode, 0, "apply without --yes must not apply")
        r = self.update("apply", "--yes", "--skip-tests")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        read = lambda p: (self.inst / p).read_text()
        self.assertEqual(read("docs/a.md"), "a2\n")                    # update
        self.assertEqual(read("docs/new.md"), "new\n")                 # add
        self.assertEqual(read("docs/both.md"), "both-mine\n")          # conflict kept
        self.assertEqual(read("docs/mine.md"), "mine-edited\n")        # keep-local
        self.assertEqual(read("owners-inbox/brand.md"), "brand-mine\n")  # protected
        self.assertEqual(read("owners-inbox/private.md"), "private\n")
        self.assertTrue((self.inst / "docs/gone.md").exists())         # removed upstream, left
        self.assertIn("version=v2", read("VERSION"))
        backup = next(self.backups.iterdir())
        self.assertEqual((backup / "files/docs/a.md").read_text(), "a\n")
        self.assertEqual((backup / "release-copies-of-conflicts/docs/both.md").read_text(), "both-upstream\n")
        self.assertTrue(list((backup / "database").glob("*.db")))
        self.assertTrue(list((self.inst / "owners-inbox/setup-notes").glob("*-typewriter-update-v2.md")))
        # The unresolved conflict stays flagged on the next comparison.
        r = self.update("fetch", "--from", str(self.dist / "typewriter-v2.tar.gz"))
        self.assertIn("conflict: 1", r.stdout)

    def test_bad_checksum_is_refused(self):
        side = self.dist / "typewriter-v2.tar.gz.sha256"
        side.write_text("0" * 64 + "  typewriter-v2.tar.gz\n")
        r = self.update("fetch", "--from", str(self.dist / "typewriter-v2.tar.gz"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not match", r.stderr)

    def test_git_clone_is_sent_to_the_git_procedure(self):
        git(self.inst, "init", "-q")
        r = self.update("fetch", "--from", str(self.dist / "typewriter-v2.tar.gz"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("git clone", r.stderr)


if __name__ == "__main__":
    unittest.main()
