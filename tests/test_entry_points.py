"""Entry points in the project tracker: develop, publish, article.

A project starts at one entry, runs that entry's states, completes on that
entry's deliverable, and can be continued into the next entry with everything
recorded so far untouched. Offline; no index, no network. Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import video_project as vp  # noqa: E402


class EntryCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in [("PROJECTS_DIR", self.root / "projects"),
                            ("_index_overview", lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1}),
                            ("index_markdown_artifact", lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1}),
                            ("forget_markdown_artifact", lambda path: None)]:
            mock = patch.object(vp, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def create(self, entry, slug="pilot", **kwargs):
        vp.create_project(title="Pilot", slug=slug, summary="", entry=entry, **kwargs)
        return vp.load_project(slug)

    def attach(self, kind, name=None, content=None, slug="pilot", **kwargs):
        path = self.root / (name or f"{kind}.txt")
        path.write_text(content or kind, encoding="utf-8")
        vp.attach_artifact(slug=slug, kind=kind, raw_path=str(path), **kwargs)
        return path

    def set_state(self, state, slug="pilot"):
        project = vp.load_project(slug)
        project["state"] = state
        vp.save_project(project)

    def approve(self, gate, slug="pilot"):
        vp.approve_gate(slug=slug, gate=gate, approved_by="owner")


class TestSequences(EntryCase):
    def test_develop_runs_development_and_deck_then_completes(self):
        project = self.create("develop")
        self.assertEqual(project["state"], "developing")
        self.assertEqual(project["entries"], ["develop"])
        self.assertEqual(vp.workflow_sequence(project), [
            "developing", "brief-review", "brief-approved", "deck-review", "deck-approved", "complete"])
        self.assertIn("develop entry", project["history"][0]["note"])

    def test_publish_starts_at_master_qc(self):
        project = self.create("publish")
        self.assertEqual(project["state"], "master-qc")
        self.assertEqual(vp.workflow_sequence(project), [
            "master-qc", "package-review", "package-approved", "private-upload", "youtube-qa",
            "release-approved", "published", "complete"])

    def test_article_runs_editorial_states_only(self):
        project = self.create("article")
        self.assertEqual(project["state"], "blog-review")
        self.assertEqual(vp.workflow_sequence(project), ["blog-review", "blog-approved", "complete"])

    def test_legacy_projects_keep_the_full_sequence(self):
        project = self.create(None)
        self.assertNotIn("entries", project)
        self.assertEqual(project["state"], "developing")
        self.assertEqual(vp.workflow_sequence(project), list(vp.STATE_SEQUENCE))

    def test_unknown_entry_is_refused(self):
        with self.assertRaisesRegex(ValueError, "entry must be one of"):
            vp.create_project(title="X", slug="x", summary="", entry="record")

    def test_every_entry_state_is_a_real_state(self):
        for entry in vp.ENTRIES:
            self.assertIn(vp.ENTRY_FIRST_STATE[entry], vp.STATE_SEQUENCE)
            self.assertIn(vp.ENTRY_LAST_STATE[entry], vp.STATE_SEQUENCE)
            self.assertLess(vp.STATE_SEQUENCE.index(vp.ENTRY_FIRST_STATE[entry]),
                            vp.STATE_SEQUENCE.index(vp.ENTRY_LAST_STATE[entry]))


class TestDevelopCompletion(EntryCase):
    def ready_deck(self):
        self.create("develop")
        self.attach("brief", "brief.md", "# Brief\n")
        self.set_state("brief-review")
        self.approve("brief")
        vp.advance_project(slug="pilot", target="brief-approved")
        vp.advance_project(slug="pilot", target="deck-review")
        bundle = self.root / "deck.cuecam"
        bundle.mkdir()
        (bundle / "cards.json").write_text("[]", encoding="utf-8")
        vp.attach_artifact(slug="pilot", kind="cuecam_bundle", raw_path=str(bundle))
        return bundle

    def test_completes_on_deck_approval_and_bundle_only(self):
        self.ready_deck()
        self.approve("deck")
        vp.advance_project(slug="pilot", target="deck-approved")
        before = vp.load_project("pilot")
        vp.advance_project(slug="pilot", target=None)
        after = vp.load_project("pilot")
        self.assertEqual(after["state"], "complete")
        self.assertEqual(after["approvals"], before["approvals"], "completion never adds an approval")
        self.assertIsNone(vp.current_artifact(after, "final_master"), "no video material was needed")
        self.assertEqual(after["deliveries"][0]["entry"], "develop")
        self.assertEqual(list(after["deliveries"][0]["artifacts"]), ["cuecam_bundle"])
        self.assertIn("continue this project into publish", after["next_action"])

    def test_completion_refused_without_deck_approval(self):
        self.ready_deck()
        self.set_state("deck-approved")
        with self.assertRaisesRegex(ValueError, "deck approval"):
            vp.advance_project(slug="pilot", target=None)
        self.assertEqual(vp.load_project("pilot")["state"], "deck-approved")

    def test_completion_refused_when_bundle_changed_after_approval(self):
        bundle = self.ready_deck()
        self.approve("deck")
        vp.advance_project(slug="pilot", target="deck-approved")
        (bundle / "cards.json").write_text("[{}]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "deck approval"):
            vp.advance_project(slug="pilot", target=None)

    def test_completion_refused_without_bundle(self):
        self.create("develop")
        self.set_state("deck-approved")
        with self.assertRaisesRegex(ValueError, "CueCam bundle"):
            vp.advance_project(slug="pilot", target=None)


class TestContinuation(EntryCase):
    def complete_develop(self):
        TestDevelopCompletion.ready_deck(self)
        self.approve("deck")
        vp.advance_project(slug="pilot", target="deck-approved")
        vp.advance_project(slug="pilot", target=None)
        return vp.load_project("pilot")

    def test_develop_continues_into_publish_at_recording(self):
        before = self.complete_develop()
        result = vp.continue_project(slug="pilot", into="publish")
        after = vp.load_project("pilot")
        self.assertEqual(result["to"], "recorded")
        self.assertEqual(after["state"], "recorded")
        self.assertEqual(after["entries"], ["develop", "publish"])
        self.assertEqual(vp.workflow_sequence(after)[:6],
                         ["developing", "brief-review", "brief-approved", "deck-review", "deck-approved", "recorded"])
        self.assertEqual(vp.workflow_sequence(after)[-2:], ["published", "complete"])
        self.assertEqual(after["approvals"], before["approvals"])
        self.assertEqual(after["deliveries"], before["deliveries"])
        self.assertEqual(after["artifacts"], before["artifacts"])
        self.assertEqual(after["history"][:-1], before["history"])
        self.assertEqual(after["history"][-1]["event"], "continued")
        self.assertTrue(vp.approval_is_current(after, "deck"))

    def test_continue_requires_completion(self):
        self.create("develop")
        with self.assertRaisesRegex(ValueError, "state=complete"):
            vp.continue_project(slug="pilot", into="publish")

    def test_continue_only_into_the_next_entry(self):
        self.complete_develop()
        with self.assertRaisesRegex(ValueError, "not article"):
            vp.continue_project(slug="pilot", into="article")
        with self.assertRaisesRegex(ValueError, "not develop"):
            vp.continue_project(slug="pilot", into="develop")

    def test_legacy_project_cannot_be_continued(self):
        self.create(None)
        self.set_state("complete")
        with self.assertRaisesRegex(ValueError, "entry point"):
            vp.continue_project(slug="pilot", into="publish")

    def test_continued_project_advances_through_production(self):
        self.complete_develop()
        vp.continue_project(slug="pilot", into="publish")
        self.attach("raw_recording", "raw.mov")
        # recorded -> clean-ready needs a cleaned video
        with self.assertRaisesRegex(ValueError, "cleaned video"):
            vp.advance_project(slug="pilot", target=None)
        self.attach("cleaned_video", "clean.mov")
        vp.advance_project(slug="pilot", target=None)
        self.assertEqual(vp.load_project("pilot")["state"], "clean-ready")


class TestPublishAndArticleCompletion(EntryCase):
    def test_publish_completion_requires_delivery_and_publication_record(self):
        """T7 defines the shape; the publish entry's own ticket refines release and URL rules."""
        self.create("publish")
        for kind in ["final_master", "qc_report", "transcript", "youtube_package", "thumbnail"]:
            self.attach(kind)
        vp.select_title(slug="pilot", title="Example")
        vp.select_thumbnail(slug="pilot", raw_path=str(self.root / "thumbnail.txt"))
        project = vp.load_project("pilot")
        for gate in ["master", "package"]:
            project["approvals"][gate] = {"approved_by": "Owner", "approved_at": "2026-09-01T12:00:00Z",
                                          "artifact_hashes": vp._approval_hashes(project, gate)}
        project["state"] = "published"
        vp.save_project(project)
        with self.assertRaisesRegex(ValueError, "YouTube publication record"):
            vp.advance_project(slug="pilot", target=None)
        project = vp.load_project("pilot")
        project["publications"]["youtube"] = {"url": "https://youtu.be/x", "confirmed_at": "2026-09-18T00:00:00Z"}
        vp.save_project(project)
        vp.advance_project(slug="pilot", target=None)
        after = vp.load_project("pilot")
        self.assertEqual(after["state"], "complete")
        self.assertEqual(after["deliveries"][0]["entry"], "publish")
        self.assertIn("into article", after["next_action"])

    def test_article_completion_requires_blog_approval(self):
        self.create("article")
        self.attach("blog_draft", "draft.md", "# Draft\n")
        self.set_state("blog-approved")
        with self.assertRaisesRegex(ValueError, "blog approval"):
            vp.advance_project(slug="pilot", target=None)
        self.set_state("blog-review")
        self.approve("blog")
        vp.advance_project(slug="pilot", target="blog-approved")
        vp.advance_project(slug="pilot", target=None)
        after = vp.load_project("pilot")
        self.assertEqual(after["state"], "complete")
        self.assertEqual(after["deliveries"][0]["entry"], "article")


if __name__ == "__main__":
    unittest.main()
