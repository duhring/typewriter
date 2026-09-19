"""The thumbnail is artwork the owner supplies; nothing in the repo generates one.

Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

import video_project as vp  # noqa: E402


class TestNoGeneration(unittest.TestCase):
    def test_generator_is_gone_and_unreferenced(self):
        self.assertFalse((PKA_ROOT / "tools" / "generate_thumbnail.py").exists())
        for path in list((PKA_ROOT / "tools").glob("*.py")) + [PKA_ROOT / "README.md", PKA_ROOT / "AGENTS.md",
                                                                 PKA_ROOT / "discord-bridge" / ".env.example"]:
            self.assertNotIn("generate_thumbnail", path.read_text(encoding="utf-8"), path.name)

    def test_publish_tool_has_no_thumbnail_command_and_no_image_api_key(self):
        source = (PKA_ROOT / "tools" / "publish_to_youtube.py").read_text(encoding="utf-8")
        self.assertNotIn('add_parser("thumbnail"', source)
        self.assertNotIn("cmd_thumbnail", source)
        self.assertNotIn("OPENAI_API_KEY", (PKA_ROOT / "discord-bridge" / ".env.example").read_text(encoding="utf-8"))

    def test_pipeline_never_calls_a_thumbnail_step(self):
        source = (PKA_ROOT / "tools" / "pipeline.py").read_text(encoding="utf-8")
        # The old generation call was `YOUTUBE_TOOL, "thumbnail"`; the upload's
        # `--thumbnail` flag (supplied artwork) is the only thumbnail left.
        for token in ('YOUTUBE_TOOL), "thumbnail"', "no_thumbnail", "thumbnail_result", "generate thumbnail"):
            self.assertNotIn(token, source, token)
        self.assertIn('"--thumbnail"', source)


class TestSuppliedThumbnail(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        for name, value in [("PROJECTS_DIR", self.root / "projects"),
                            ("_index_overview", lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1}),
                            ("index_markdown_artifact", lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1}),
                            ("forget_markdown_artifact", lambda path: None)]:
            m = patch.object(vp, name, value); m.start(); self.addCleanup(m.stop)
        vp.create_project(title="Art", slug="art", summary="", entry="publish")

    def file(self, name, content=b"bytes"):
        path = self.root / name
        path.write_bytes(content)
        return path

    def test_supplied_artwork_is_attached_selected_and_bound_to_the_package_approval(self):
        for kind in ("final_master", "qc_report", "youtube_package", "transcript"):
            vp.attach_artifact(slug="art", kind=kind, raw_path=str(self.file(kind + ".bin")))
        art = self.file("cover.png", b"png-bytes")
        vp.attach_artifact(slug="art", kind="thumbnail", raw_path=str(art), note="Owner-supplied artwork.",
                           source="owner", rights="owner-created")
        vp.select_thumbnail(slug="art", raw_path=str(art))
        vp.select_title(slug="art", title="Chosen")
        project = vp.load_project("art")
        self.assertEqual(project["selections"]["thumbnail"]["sha256"], vp.hash_path(art))
        project["state"] = "package-review"
        vp.save_project(project)
        vp.approve_gate(slug="art", gate="package", approved_by="owner")
        self.assertTrue(vp.approval_is_current(vp.load_project("art"), "package"))
        art.write_bytes(b"replaced artwork")
        self.assertFalse(vp.approval_is_current(vp.load_project("art"), "package"),
                         "replacing the artwork after approval must stale the package approval")

    def test_reference_artwork_needs_rights_before_package_approval(self):
        for kind in ("final_master", "qc_report", "youtube_package", "transcript"):
            vp.attach_artifact(slug="art", kind=kind, raw_path=str(self.file(kind + ".bin")))
        art = self.file("cover.png")
        vp.attach_artifact(slug="art", kind="thumbnail", raw_path=str(art))
        vp.attach_artifact(slug="art", kind="thumbnail_reference", raw_path=str(self.file("ref.jpg")))
        vp.select_thumbnail(slug="art", raw_path=str(art))
        vp.select_title(slug="art", title="Chosen")
        project = vp.load_project("art")
        project["state"] = "package-review"
        vp.save_project(project)
        with self.assertRaisesRegex(ValueError, "rights are unrecorded"):
            vp.approve_gate(slug="art", gate="package", approved_by="owner")


if __name__ == "__main__":
    unittest.main()
