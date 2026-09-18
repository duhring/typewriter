"""Tests for the video project flow map, generated views, and wrapper notes.

No network, no real DB, no writes outside the temporary project root. Run from
the repo root:

    discord-bridge/venv/bin/python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))


class TestFlowMap(unittest.TestCase):
    """The map itself must stay coherent and agree with the documented pipeline."""

    def setUp(self):
        import video_project

        self.vp = video_project

    def test_every_kind_has_a_known_stage(self):
        for kind, spec in self.vp.ARTIFACT_FLOW.items():
            self.assertIn(spec["stage"], self.vp.WORKFLOW_STAGE_SEQUENCE, kind)

    def test_every_expected_input_is_a_real_kind(self):
        for kind, spec in self.vp.ARTIFACT_FLOW.items():
            for upstream in spec["expected_inputs"]:
                self.assertIn(upstream, self.vp.ARTIFACT_FLOW, f"{kind} <- {upstream}")

    def test_artifact_kinds_derive_from_the_map(self):
        self.assertEqual(self.vp.ARTIFACT_KINDS, set(self.vp.ARTIFACT_FLOW))

    def test_declaration_order_groups_stages_contiguously(self):
        """The renderer walks declaration order; interleaved stages would break it."""
        seen: list[str] = []
        for spec in self.vp.ARTIFACT_FLOW.values():
            if not seen or seen[-1] != spec["stage"]:
                seen.append(spec["stage"])
        self.assertEqual(len(seen), len(set(seen)), f"stage declared twice: {seen}")

    def test_no_kind_is_its_own_ancestor(self):
        def ancestors(kind, chain=()):
            self.assertNotIn(kind, chain, f"cycle: {chain + (kind,)}")
            for upstream in self.vp.expected_inputs(kind):
                ancestors(upstream, chain + (kind,))

        for kind in self.vp.ARTIFACT_FLOW:
            ancestors(kind)

    def test_expected_inputs_and_outputs_are_inverses(self):
        for kind in self.vp.ARTIFACT_FLOW:
            for upstream in self.vp.expected_inputs(kind):
                self.assertIn(kind, self.vp.expected_outputs(upstream))

    def test_editorial_loop_matches_the_documented_order(self):
        """Section 7 of docs/larry/video-production.md is the contract here."""
        vp = self.vp
        documented = [
            "editorial_interview",
            "blog_thesis",
            "substack_brief",
            "blog_draft",
            "blog_owner_edit",
            "blog_final",
            "published_blog",
        ]
        editorial = [k for k, s in vp.ARTIFACT_FLOW.items() if s["stage"] == "editorial"]
        self.assertEqual(editorial, documented)
        self.assertEqual(vp.expected_inputs("blog_thesis"), ["editorial_interview"])
        self.assertIn("blog_thesis", vp.expected_inputs("substack_brief"))
        self.assertIn("substack_brief", vp.expected_inputs("blog_draft"))

    def test_flow_order_is_pipeline_order_not_alphabetical(self):
        attached = {"youtube_package": {}, "interview": {}, "brief": {}, "blog_draft": {}}
        self.assertEqual(
            self.vp.flow_order(attached),
            ["interview", "brief", "youtube_package", "blog_draft"],
        )

    def test_unmapped_kinds_sort_last(self):
        order = self.vp.flow_order({"zebra": {}, "interview": {}, "aardvark": {}})
        self.assertEqual(order, ["interview", "aardvark", "zebra"])

    def test_editorial_is_governed_first_by_the_doc_that_defines_it(self):
        """Sections 7 and 9 of video-production.md define the editorial loop."""
        self.assertEqual(
            self.vp.primary_governance("editorial"), "docs/larry/video-production.md"
        )
        self.assertIn("docs/larry/youtube-writing.md", self.vp.governing_docs("editorial"))

    def test_every_stage_names_at_least_one_governing_doc_that_exists(self):
        for stage in self.vp.WORKFLOW_STAGE_SEQUENCE:
            docs = self.vp.governing_docs(stage)
            self.assertTrue(docs, f"{stage} names no governing procedure")
            for doc in docs:
                self.assertTrue((PKA_ROOT / doc).is_file(), f"{stage} -> missing {doc}")

    def test_observation_transitions_cover_every_status(self):
        self.assertEqual(
            set(self.vp.OBSERVATION_TRANSITIONS), set(self.vp.OBSERVATION_STATUSES)
        )
        for status, allowed in self.vp.OBSERVATION_TRANSITIONS.items():
            for target in allowed:
                self.assertIn(target, self.vp.OBSERVATION_STATUSES, f"{status} -> {target}")

    def test_vault_note_classification(self):
        self.assertTrue(self.vp._is_vault_note("owners-inbox/development/x/interview.md"))
        self.assertFalse(self.vp._is_vault_note("/Volumes/Movies/take.mov"))
        self.assertFalse(self.vp._is_vault_note("/Volumes/Elsewhere/notes.md"))
        self.assertFalse(self.vp._is_vault_note("owners-inbox/x/claims.json"))


class _ProjectFixture(unittest.TestCase):
    """Shared setup: a project in a redirected root, with indexing stubbed out.

    Holds no tests of its own so subclasses do not re-run the base suite.
    """

    def setUp(self):
        import video_project

        self.vp = video_project
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original_dir = video_project.PROJECTS_DIR
        self.original_index = video_project._index_overview
        self.original_markdown_index = video_project.index_markdown_artifact
        self.original_forget = video_project.forget_markdown_artifact
        self.forgotten: list[str] = []

        video_project.PROJECTS_DIR = self.root / "projects"
        video_project._index_overview = lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1}
        video_project.index_markdown_artifact = lambda *a, **k: {
            "knowledge_base_id": 2,
            "file_id": 2,
        }
        video_project.forget_markdown_artifact = lambda path: self.forgotten.append(path.name)
        self.addCleanup(self._restore)

        video_project.create_project(
            title="Flow Pilot", slug="flow-pilot", summary="Exercise the generated views."
        )

    def _restore(self):
        self.vp.PROJECTS_DIR = self.original_dir
        self.vp._index_overview = self.original_index
        self.vp.index_markdown_artifact = self.original_markdown_index
        self.vp.forget_markdown_artifact = self.original_forget

    def _media(self, name: str, content: str = "media") -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def _project_dir(self) -> Path:
        return self.vp.PROJECTS_DIR / "flow-pilot"

    def _overview(self) -> str:
        return (self._project_dir() / "project.md").read_text(encoding="utf-8")

    def _run_created(self, slug: str, *, offset_minutes: int) -> str:
        """A second run whose created_at is deliberately offset from now."""
        self.vp.create_project(title=slug, slug=slug, summary="Another run.")
        project = self.vp.load_project(slug)
        shifted = datetime.fromisoformat(project["created_at"]) + timedelta(
            minutes=offset_minutes
        )
        project["created_at"] = shifted.isoformat(timespec="seconds")
        self.vp.save_project(project)
        return slug

    def _later_run(self, slug: str = "later-run") -> str:
        return self._run_created(slug, offset_minutes=1)


class TestGeneratedViews(_ProjectFixture):
    # --- the regression that let tests write into the real repo --------------

    def test_generated_files_stay_inside_the_redirected_root(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        stray = PKA_ROOT / "owners-inbox" / "video-projects" / "flow-pilot"
        self.assertFalse(stray.exists(), f"generated views escaped to {stray}")
        self.assertTrue((self._project_dir() / "media" / "final_master.md").is_file())

    # --- wrapper notes -------------------------------------------------------

    def test_wrapper_note_is_written_for_a_non_note_artifact(self):
        media = self._media("master.mp4", "master bytes")
        self.vp.attach_artifact(slug="flow-pilot", kind="final_master", raw_path=str(media))
        note = (self._project_dir() / "media" / "final_master.md").read_text(encoding="utf-8")
        digest = hashlib.sha256(b"master bytes").hexdigest()
        self.assertIn(f"sha256: {digest}", note)
        self.assertIn("artifact_kind: final_master", note)
        self.assertIn("stage: master", note)
        self.assertIn("in_repo: false", note)
        self.assertIn("present: true", note)
        self.assertIn(str(media), note)

    def test_no_wrapper_note_for_an_artifact_already_in_the_vault(self):
        """A markdown artifact inside the repo is linked directly, not wrapped."""
        interview = PKA_ROOT / "owners-inbox" / "development" / "development-editor-process" / "interview.md"
        if not interview.is_file():
            self.skipTest("reference interview artifact is not present")
        self.vp.attach_artifact(slug="flow-pilot", kind="interview", raw_path=str(interview))
        self.assertFalse((self._project_dir() / "media" / "interview.md").exists())
        self.assertIn("[[owners-inbox/development/", self._overview())

    def test_wrapper_note_records_expected_neighbours_that_are_present(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="cleaned_video", raw_path=str(self._media("clean.mp4"))
        )
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        note = (self._project_dir() / "media" / "final_master.md").read_text(encoding="utf-8")
        self.assertIn("Expected inputs (present on this run):", note)
        self.assertIn("clean", note)

    def test_missing_media_is_reported_not_hidden(self):
        media = self._media("gone.mp4")
        self.vp.attach_artifact(slug="flow-pilot", kind="final_master", raw_path=str(media))
        media.unlink()
        self.vp.render_views(self.vp.load_project("flow-pilot"))
        note = (self._project_dir() / "media" / "final_master.md").read_text(encoding="utf-8")
        self.assertIn("present: false", note)
        self.assertIn("**NO — path does not resolve**", note)

    # --- multi-artifact kinds ------------------------------------------------

    def test_multi_artifact_kinds_get_one_note_each(self):
        for name, body in (("t1.png", "one"), ("t2.png", "two")):
            self.vp.attach_artifact(
                slug="flow-pilot", kind="thumbnail", raw_path=str(self._media(name, body))
            )
        notes = sorted(p.name for p in (self._project_dir() / "media").glob("thumbnail-*.md"))
        self.assertEqual(len(notes), 2, notes)
        for digest in (hashlib.sha256(b"one").hexdigest(), hashlib.sha256(b"two").hexdigest()):
            self.assertIn(f"thumbnail-{digest[:8]}.md", notes)

    def test_replacing_an_artifact_removes_the_stale_note_and_its_index_rows(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="cleaned_video", raw_path=str(self._media("v1.mp4", "one"))
        )
        stale = self._project_dir() / "media" / "cleaned_video.md"
        self.assertTrue(stale.is_file())

        # A single-artifact kind reuses its filename, so force a stale note by
        # renaming the current one and re-rendering.
        orphan = self._project_dir() / "media" / "cleaned_video-old.md"
        stale.rename(orphan)
        self.vp.render_views(self.vp.load_project("flow-pilot"))

        self.assertFalse(orphan.exists(), "stale note was left on disk")
        self.assertIn("cleaned_video-old.md", self.forgotten, "index rows were not dropped")

    def test_a_governed_stale_note_is_kept_not_destroyed(self):
        import pka_index

        self.vp.attach_artifact(
            slug="flow-pilot", kind="cleaned_video", raw_path=str(self._media("v1.mp4"))
        )
        orphan = self._project_dir() / "media" / "cleaned_video-old.md"
        (self._project_dir() / "media" / "cleaned_video.md").rename(orphan)

        def refuse(path):
            raise pka_index.GovernedRecordError(f"governed record cites {path.name}")

        self.vp.forget_markdown_artifact = refuse
        self.vp.render_views(self.vp.load_project("flow-pilot"))
        self.assertTrue(orphan.exists(), "a cited note must not be deleted")

    # --- render idempotence --------------------------------------------------

    def test_render_does_not_touch_the_manifest(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        manifest = self._project_dir() / "project.json"
        before = manifest.read_text(encoding="utf-8")
        self.vp.render_views(self.vp.load_project("flow-pilot"))
        self.assertEqual(manifest.read_text(encoding="utf-8"), before)

    def test_render_is_idempotent(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        first = self._overview()
        self.vp.render_views(self.vp.load_project("flow-pilot"))
        self.assertEqual(self._overview(), first)

    def test_save_project_still_writes_the_manifest(self):
        """render_views leaves the manifest alone; save_project must not."""
        manifest = self._project_dir() / "project.json"
        before = manifest.read_text(encoding="utf-8")
        project = self.vp.load_project("flow-pilot")
        project["summary"] = "changed"
        self.vp.save_project(project)
        self.assertNotEqual(manifest.read_text(encoding="utf-8"), before)
        self.assertEqual(self.vp.load_project("flow-pilot")["summary"], "changed")

    # --- overview rendering --------------------------------------------------

    def test_overview_labels_columns_as_expectation(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        overview = self._overview()
        self.assertIn("| Kind | Artifact | Expected inputs | Expected outputs |", overview)
        self.assertIn("as designed", overview)

    def test_overview_groups_by_stage_in_pipeline_order(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="blog_draft", raw_path=str(self._media("draft.txt"))
        )
        self.vp.attach_artifact(
            slug="flow-pilot", kind="raw_recording", raw_path=str(self._media("take.mov"))
        )
        overview = self._overview()
        self.assertLess(overview.index("### Production"), overview.index("### Editorial"))

    def test_expected_but_absent_kinds_are_struck_through(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        self.assertIn("~~cleaned_video~~", self._overview())

    def test_artifact_links_point_at_the_wrapper_note(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        self.assertIn("media/final_master|master.mp4]]", self._overview())


class TestDecisionsAndObservations(_ProjectFixture):
    """The sideways axis: judgment recorded during a run, and claims about the pipeline."""

    def test_decision_is_recorded_and_rendered(self):
        self.vp.record_decision(
            slug="flow-pilot", stage="master", summary="Re-exported the master",
            why="black bar on the first export", artifacts=["final_master"], by="John",
        )
        project = self.vp.load_project("flow-pilot")
        self.assertEqual(len(project["decisions"]), 1)
        decision = project["decisions"][0]
        self.assertEqual(decision["stage"], "master")
        self.assertEqual(decision["artifacts"], ["final_master"])
        self.assertNotIn("status", decision, "a decision is closed and carries no status")
        overview = self._overview()
        self.assertIn("## Decisions", overview)
        self.assertIn("Re-exported the master", overview)

    def test_the_same_decision_can_be_made_twice(self):
        """Two identical re-exports in one run are two events, not a duplicate."""
        first = self.vp.record_decision(
            slug="flow-pilot", stage="master", summary="Re-exported the master"
        )["decision"]
        second = self.vp.record_decision(
            slug="flow-pilot", stage="master", summary="Re-exported the master"
        )["decision"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.vp.load_project("flow-pilot")["decisions"]), 2)

    def test_unknown_stage_is_refused(self):
        with self.assertRaises(ValueError):
            self.vp.record_decision(slug="flow-pilot", stage="publication", summary="x")

    def test_unknown_artifact_kind_is_refused(self):
        with self.assertRaises(ValueError):
            self.vp.record_decision(
                slug="flow-pilot", stage="master", summary="x", artifacts=["nonsense"]
            )

    def test_observation_starts_proposed_and_carries_history(self):
        result = self.vp.record_observation(
            slug="flow-pilot", stage="editorial",
            summary="The editorial interview came too late",
            proposes="Move it immediately after master transcription", by="John",
        )
        observation = result["observation"]
        self.assertEqual(observation["status"], "proposed")
        self.assertEqual(len(observation["history"]), 1)
        self.assertIn("## Observations", self._overview())
        self.assertIn("`proposed`", self._overview())

    def test_observation_advances_through_its_lifecycle(self):
        self._later_run("next-run")
        obs = self.vp.record_observation(
            slug="flow-pilot", stage="editorial", summary="Interview lands too late"
        )["observation"]
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs["id"], status="accepted", note="agreed"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs["id"], status="implemented",
            changed="docs/larry/video-production.md",
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs["id"], status="verified",
            verified_by_run="next-run",
        )
        stored = self.vp.load_project("flow-pilot")["observations"][0]
        self.assertEqual(stored["status"], "verified")
        self.assertEqual(stored["changed"], "docs/larry/video-production.md")
        self.assertEqual(stored["verified_by_run"], "next-run")
        self.assertEqual(
            [entry["status"] for entry in stored["history"]],
            ["proposed", "accepted", "implemented", "verified"],
        )

    def test_implemented_must_name_what_changed(self):
        obs = self.vp.record_observation(
            slug="flow-pilot", stage="editorial", summary="Something to fix"
        )["observation"]
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs["id"], status="accepted"
        )
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs["id"], status="implemented"
            )
        self.assertIn("--changed", str(caught.exception))

    # --- the state machine is enforced, not merely described -----------------

    def _observation(self, summary: str = "A claim") -> str:
        return self.vp.record_observation(
            slug="flow-pilot", stage="editorial", summary=summary
        )["observation"]["id"]

    def test_cannot_skip_from_proposed_to_verified(self):
        obs_id = self._observation()
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="verified"
            )
        self.assertIn("from `proposed` to `verified`", str(caught.exception))

    def test_cannot_skip_from_proposed_to_implemented(self):
        obs_id = self._observation()
        with self.assertRaises(ValueError):
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="implemented",
                changed="docs/larry/video-production.md",
            )

    def test_rejected_cannot_jump_to_implemented(self):
        obs_id = self._observation()
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="rejected"
        )
        with self.assertRaises(ValueError):
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="implemented",
                changed="docs/larry/video-production.md",
            )

    def test_rejected_can_be_reopened(self):
        obs_id = self._observation()
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="rejected"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="proposed", note="new evidence"
        )
        self.assertEqual(
            self.vp.load_project("flow-pilot")["observations"][0]["status"], "proposed"
        )

    def test_verified_is_terminal(self):
        self._later_run()
        obs_id = self._observation()
        for status, kwargs in (
            ("accepted", {}),
            ("implemented", {"changed": "docs/larry/video-production.md"}),
            ("verified", {"verified_by_run": "later-run"}),
        ):
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status=status, **kwargs
            )
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="proposed"
            )
        self.assertIn("terminal", str(caught.exception))

    def test_evidence_cannot_be_banked_before_its_transition(self):
        """--changed and --verified-by-run belong to one transition each."""
        self._later_run()
        obs_id = self._observation()
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="accepted",
                verified_by_run="later-run",
            )
        self.assertIn("evidence for the `verified` transition", str(caught.exception))

        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="accepted",
                changed="docs/larry/video-production.md",
            )
        self.assertIn("evidence for the `implemented` transition", str(caught.exception))

    def test_banked_evidence_cannot_satisfy_a_later_transition(self):
        """The whole point: reaching `verified` requires evidence given there."""
        self._later_run()
        obs_id = self._observation()
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="accepted"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="implemented",
            changed="docs/larry/video-production.md",
        )
        # `changed` is stored now, but that must not stand in for the run.
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="verified"
            )
        self.assertIn("--verified-by-run", str(caught.exception))

    def test_verifying_run_must_be_created_after_the_observation(self):
        """A run that predates the observation cannot have tested the change."""
        earlier = self._run_created("earlier-run", offset_minutes=-5)
        obs_id = self._observation()
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="accepted"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="implemented",
            changed="docs/larry/video-production.md",
        )
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="verified",
                verified_by_run=earlier,
            )
        self.assertIn("cannot have", str(caught.exception))

    def test_verifying_run_must_exist(self):
        obs_id = self._observation()
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="accepted"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="implemented",
            changed="docs/larry/video-production.md",
        )
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="verified",
                verified_by_run="never-happened",
            )
        self.assertIn("No such run", str(caught.exception))

    def test_a_run_cannot_verify_its_own_observation(self):
        obs_id = self._observation()
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="accepted"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="implemented",
            changed="docs/larry/video-production.md",
        )
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="verified",
                verified_by_run="flow-pilot",
            )
        self.assertIn("cannot be verified by the run that raised it", str(caught.exception))

    def test_verified_must_name_the_run_that_tested_it(self):
        obs_id = self._observation("Another thing")
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="accepted"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs_id, status="implemented",
            changed="docs/larry/video-production.md",
        )
        with self.assertRaises(ValueError) as caught:
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="verified"
            )
        self.assertIn("--verified-by-run", str(caught.exception))

    def test_unknown_status_is_refused(self):
        obs_id = self._observation("Yet another")
        with self.assertRaises(ValueError):
            self.vp.update_observation(
                slug="flow-pilot", observation_id=obs_id, status="done"
            )

    def test_missing_observation_is_refused(self):
        with self.assertRaises(ValueError):
            self.vp.update_observation(
                slug="flow-pilot", observation_id="deadbeef", status="accepted"
            )

    def test_empty_logs_render_as_none_recorded(self):
        overview = self._overview()
        self.assertIn("## Decisions\n\n- None recorded.", overview)
        self.assertIn("## Observations\n\n- None recorded.", overview)


class TestRetrospective(_ProjectFixture):
    def _retro(self) -> str:
        self.vp.generate_retrospective(slug="flow-pilot")
        return (self._project_dir() / "retrospective.md").read_text(encoding="utf-8")

    def test_reports_divergence_from_the_designed_flow(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        retro = self._retro()
        self.assertIn("## Divergence from the designed flow", retro)
        self.assertIn("`final_master`", retro)
        self.assertIn("missing `cleaned_video`", retro)

    def test_reports_rework_when_an_artifact_is_replaced(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("m.mp4", "one"))
        )
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("m2.mp4", "two"))
        )
        self.assertIn("`final_master` replaced", self._retro())

    def test_says_plainly_when_no_decision_was_captured(self):
        retro = self._retro()
        self.assertIn("## Decisions recorded", retro)
        self.assertIn("none of it", retro)
        self.assertIn("video_project.py decision", retro)

    def test_open_observations_become_candidate_changes(self):
        self.vp.record_observation(
            slug="flow-pilot", stage="editorial", summary="Interview lands too late",
            proposes="Move it after master transcription",
        )
        retro = self._retro()
        self.assertIn("## Candidate workflow changes", retro)
        self.assertIn("Move it after master transcription", retro)
        self.assertIn("video-production", retro, "should name the primary governing procedure")

    def test_verified_observations_drop_out_of_candidates(self):
        self._later_run()
        obs = self.vp.record_observation(
            slug="flow-pilot", stage="editorial", summary="Already handled"
        )["observation"]
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs["id"], status="accepted"
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs["id"], status="implemented",
            changed="docs/larry/video-production.md",
        )
        self.vp.update_observation(
            slug="flow-pilot", observation_id=obs["id"], status="verified",
            verified_by_run="later-run",
        )
        retro = self._retro()
        self.assertIn("## Candidate workflow changes\n\n- None open.", retro)

    def test_project_page_gains_a_link_to_the_retrospective(self):
        overview_before = self._overview()
        self.assertNotIn("**Retrospective:**", overview_before)
        self.vp.generate_retrospective(slug="flow-pilot")
        overview_after = self._overview()
        self.assertIn("**Retrospective:**", overview_after)
        self.assertIn("retrospective|Retrospective]]", overview_after)

    def test_does_not_mutate_the_manifest(self):
        self.vp.attach_artifact(
            slug="flow-pilot", kind="final_master", raw_path=str(self._media("master.mp4"))
        )
        manifest = self._project_dir() / "project.json"
        before = manifest.read_text(encoding="utf-8")
        self.vp.generate_retrospective(slug="flow-pilot")
        self.assertEqual(manifest.read_text(encoding="utf-8"), before)


class TestPipelinePage(unittest.TestCase):
    """The cross-run page reads manifests directly and must stay dry-runnable."""

    def setUp(self):
        import json as json_module

        import wiki_compile

        self.wc = wiki_compile
        self.json = json_module
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = wiki_compile.VIDEO_PROJECTS_DIR
        wiki_compile.VIDEO_PROJECTS_DIR = self.root
        self.addCleanup(setattr, wiki_compile, "VIDEO_PROJECTS_DIR", self.original)

    def _run(self, slug: str, **overrides):
        payload = {
            "slug": slug,
            "title": slug,
            "state": "complete",
            "artifacts": {"final_master": {"path": "m.mp4", "sha256": "a"}},
            "publications": {},
            "decisions": [],
            "observations": [],
        }
        payload.update(overrides)
        directory = self.root / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "project.json").write_text(self.json.dumps(payload), encoding="utf-8")

    def test_no_runs_is_reported_not_crashed(self):
        self.assertEqual(self.wc.compile_pipeline(dry_run=True)["status"], "no-runs")

    def test_counts_divergence_across_runs(self):
        self._run("alpha")
        self._run("beta")
        markdown = self.wc.compile_pipeline(dry_run=True)["markdown"]
        self.assertIn("`final_master` without `cleaned_video` - 2/2 runs", markdown)

    def test_stage_coverage_marks_each_run(self):
        self._run("alpha")
        markdown = self.wc.compile_pipeline(dry_run=True)["markdown"]
        self.assertIn("| `final_master` | master | x |", markdown)
        self.assertIn("| `interview` | development | - |", markdown)

    def test_open_observations_surface_with_their_governing_doc(self):
        self._run("alpha", observations=[{
            "id": "abc12345", "stage": "editorial", "status": "proposed",
            "summary": "Interview lands too late",
        }])
        markdown = self.wc.compile_pipeline(dry_run=True)["markdown"]
        self.assertIn("Interview lands too late", markdown)
        self.assertIn("docs/larry/video-production.md", markdown)

    def test_verified_observations_are_not_listed_as_open(self):
        self._run("alpha", observations=[{
            "id": "abc12345", "stage": "editorial", "status": "verified",
            "summary": "Already handled",
        }])
        markdown = self.wc.compile_pipeline(dry_run=True)["markdown"]
        self.assertIn("## Open observations\n\n- None raised yet.", markdown)

    def test_absent_decisions_are_stated_not_glossed(self):
        self._run("alpha")
        markdown = self.wc.compile_pipeline(dry_run=True)["markdown"]
        self.assertIn("No decisions have been recorded", markdown)

    def test_malformed_manifest_is_listed_not_dropped(self):
        self._run("alpha")
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "project.json").write_text("{not json", encoding="utf-8")
        result = self.wc.compile_pipeline(dry_run=True)
        self.assertEqual(result["runs"], 1)
        self.assertEqual(len(result["excluded"]), 1)
        self.assertIn("## Manifests excluded from this page", result["markdown"])
        self.assertIn("broken/project.json", result["markdown"])
        self.assertIn("malformed JSON", result["markdown"])

    def test_manifest_without_a_slug_is_excluded(self):
        self._run("alpha")
        headless = self.root / "headless"
        headless.mkdir()
        (headless / "project.json").write_text('{"state": "complete"}', encoding="utf-8")
        result = self.wc.compile_pipeline(dry_run=True)
        self.assertEqual(result["runs"], 1)
        self.assertIn("missing required key: slug", result["markdown"])

    def test_all_invalid_manifests_still_produce_the_exclusion_page(self):
        for name in ("broken-one", "broken-two"):
            directory = self.root / name
            directory.mkdir()
            (directory / "project.json").write_text("{nope", encoding="utf-8")
        result = self.wc.compile_pipeline(dry_run=True)
        self.assertEqual(result["status"], "dry-run", "must not short-circuit to no-runs")
        self.assertEqual(result["runs"], 0)
        self.assertEqual(len(result["excluded"]), 2)
        self.assertIn("## Manifests excluded from this page", result["markdown"])
        self.assertIn("No manifest could be read", result["markdown"])
        self.assertNotIn("## Stage coverage", result["markdown"])

    def test_no_manifests_at_all_is_still_no_runs(self):
        self.assertEqual(self.wc.compile_pipeline(dry_run=True)["status"], "no-runs")

    def test_no_exclusion_section_when_everything_reads(self):
        self._run("alpha")
        markdown = self.wc.compile_pipeline(dry_run=True)["markdown"]
        self.assertNotIn("Manifests excluded", markdown)

    def test_dry_run_returns_markdown_without_writing(self):
        self._run("alpha")
        page = self.wc.OPERATIONS_DIR / "video-editorial-pipeline.md"
        before = page.stat().st_mtime if page.exists() else None
        result = self.wc.compile_pipeline(dry_run=True)
        self.assertEqual(result["status"], "dry-run")
        self.assertIsNone(result["page"])
        self.assertIn("# Video Editorial Pipeline", result["markdown"])
        after = page.stat().st_mtime if page.exists() else None
        self.assertEqual(before, after, "dry run must not touch the compiled page")


class TestForgetMarkdownArtifact(unittest.TestCase):
    """Removing a generated note must not leave dangling relationships behind."""

    SCHEMA = """
    CREATE TABLE knowledge_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, content TEXT,
        category TEXT, tags TEXT, source_file TEXT, created_at TEXT,
        updated_at TEXT, source_url TEXT, confidence TEXT);
    CREATE TABLE files (
        id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, filepath TEXT,
        category TEXT, description TEXT, file_type TEXT, ocr_text TEXT);
    CREATE TABLE kb_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL, relationship TEXT, created_at TEXT,
        FOREIGN KEY (source_id) REFERENCES knowledge_base(id),
        FOREIGN KEY (target_id) REFERENCES knowledge_base(id));
    CREATE TABLE records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL, record_class TEXT NOT NULL, record_series TEXT NOT NULL,
        file_id INTEGER, knowledge_base_id INTEGER,
        FOREIGN KEY (file_id) REFERENCES files(id),
        FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base(id));
    """

    def setUp(self):
        import sqlite3

        import pka_index

        self.pi = pka_index
        self.sqlite3 = sqlite3
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "test.db"
        conn = sqlite3.connect(self.db)
        conn.executescript(self.SCHEMA)
        conn.commit()
        conn.close()

        self.original_connect = pka_index._connect
        pka_index._connect = lambda: sqlite3.connect(str(self.db))
        self.addCleanup(setattr, pka_index, "_connect", self.original_connect)

        self.note = self.root / "final_master.md"
        self.note.write_text("wrapper", encoding="utf-8")
        self.rel = pka_index._rel_path(self.note)

    def _conn(self):
        return self.sqlite3.connect(str(self.db))

    def _seed(self, category: str = "video-media") -> tuple[int, int]:
        with self._conn() as conn:
            kb_id = conn.execute(
                "INSERT INTO knowledge_base (title, category, source_file) VALUES (?, ?, ?)",
                ("wrapper", category, self.rel),
            ).lastrowid
            file_id = conn.execute(
                "INSERT INTO files (filename, filepath, category) VALUES (?, ?, ?)",
                (self.note.name, self.rel, category),
            ).lastrowid
            neighbour = conn.execute(
                "INSERT INTO knowledge_base (title, category, source_file) VALUES (?, ?, ?)",
                ("neighbour", "knowledge-base", "somewhere/else.md"),
            ).lastrowid
            # one link out of the doomed row, one into it
            conn.execute(
                "INSERT INTO kb_links (source_id, target_id, relationship) VALUES (?, ?, ?)",
                (kb_id, neighbour, "related"),
            )
            conn.execute(
                "INSERT INTO kb_links (source_id, target_id, relationship) VALUES (?, ?, ?)",
                (neighbour, kb_id, "related"),
            )
        return kb_id, file_id

    def _counts(self) -> tuple[int, int, int]:
        with self._conn() as conn:
            return (
                conn.execute(
                    "SELECT count(*) FROM knowledge_base WHERE source_file = ?", (self.rel,)
                ).fetchone()[0],
                conn.execute(
                    "SELECT count(*) FROM files WHERE filepath = ?", (self.rel,)
                ).fetchone()[0],
                conn.execute("SELECT count(*) FROM kb_links").fetchone()[0],
            )

    def test_removes_links_in_both_directions(self):
        self._seed()
        self.assertEqual(self._counts(), (1, 1, 2))
        result = self.pi.forget_markdown_artifact(self.note)
        self.assertEqual(self._counts(), (0, 0, 0))
        self.assertEqual(result["kb_links"], 2)
        self.assertEqual(result["knowledge_base_rows"], 1)
        self.assertEqual(result["file_rows"], 1)

    def test_refuses_when_a_governed_record_cites_the_rows(self):
        kb_id, _ = self._seed()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO records (record_key, title, record_class, record_series,"
                " knowledge_base_id) VALUES (?, ?, ?, ?, ?)",
                ("REC-1", "Governed", "evidence", "video", kb_id),
            )
        with self.assertRaises(self.pi.GovernedRecordError) as caught:
            self.pi.forget_markdown_artifact(self.note)
        self.assertIn("REC-1", str(caught.exception))
        self.assertEqual(self._counts(), (1, 1, 2), "refusal must delete nothing")

    def test_refuses_a_category_outside_the_allowlist(self):
        self._seed(category="blog")
        with self.assertRaises(ValueError):
            self.pi.forget_markdown_artifact(self.note)
        self.assertEqual(self._counts(), (1, 1, 2), "refusal must delete nothing")

    def test_allowlist_can_be_lifted_deliberately(self):
        self._seed(category="blog")
        self.pi.forget_markdown_artifact(self.note, allowed_categories=None)
        self.assertEqual(self._counts(), (0, 0, 0))

    def test_unindexed_note_is_a_no_op(self):
        result = self.pi.forget_markdown_artifact(self.note)
        self.assertEqual(result["knowledge_base_rows"], 0)
        self.assertEqual(result["file_rows"], 0)
        self.assertEqual(result["kb_links"], 0)

    def test_no_dangling_links_survive(self):
        kb_id, _ = self._seed()
        self.pi.forget_markdown_artifact(self.note)
        with self._conn() as conn:
            dangling = conn.execute(
                "SELECT count(*) FROM kb_links WHERE source_id NOT IN"
                " (SELECT id FROM knowledge_base) OR target_id NOT IN"
                " (SELECT id FROM knowledge_base)"
            ).fetchone()[0]
        self.assertEqual(dangling, 0)


if __name__ == "__main__":
    unittest.main()
