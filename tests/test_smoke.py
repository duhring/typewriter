"""Smoke tests for the load-bearing PKA tools.

No network, no real DB, no API keys. Run from the repo root:

    discord-bridge/venv/bin/python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))


class TestImports(unittest.TestCase):
    """Every module the pipeline or bridge depends on must at least import."""

    MODULES = [
        "pipeline",
        "pka_index",
        "records",
        "task_record",
        "llm",
        "glm",
        "machine_role",
        "cuecam",
        "wiki_compile",
        "editorial_recurrence",
        "balance_check",
        "harness_audit",
        "health_escalation",
        "video_project",
        "video_qc",
    ]

    def test_imports(self):
        import importlib

        for name in self.MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)


class TestPipelineHelpers(unittest.TestCase):
    def test_slugify(self):
        import pipeline

        self.assertEqual(pipeline.slugify("Hello, World!"), "hello-world")
        self.assertEqual(pipeline.slugify("  A_B  C  "), "a-b-c")
        self.assertEqual(pipeline.slugify("!!!"), "video")
        self.assertLessEqual(len(pipeline.slugify("x" * 200)), 60)

    def test_cleaned_path(self):
        import pipeline

        p = pipeline.cleaned_path(Path("/tmp/rec.mov"))
        self.assertEqual(p, Path("/tmp/rec_cleaned.mov"))

    def test_generate_package_dry_run_makes_no_calls(self):
        import pipeline

        out = pipeline.generate_package("transcript text", dry_run=True)
        self.assertIn("Title Options", out)


class TestLLMRouter(unittest.TestCase):
    def test_glm_registered(self):
        import llm

        self.assertIn("glm", llm.SUPPORTED_PROVIDERS)


class TestMachineRoleGuard(unittest.TestCase):
    """Federated peers retain compatibility with the former writer guards."""

    def test_legacy_satellite_config_no_longer_blocks_local_writes(self):
        import machine_role

        original = machine_role.load_machine
        machine_role.load_machine = lambda: {"role": "satellite", "db_write": False}
        try:
            machine_role.require_writer("test-op")
        finally:
            machine_role.load_machine = original

    def test_primary_allows_writes(self):
        import machine_role

        original = machine_role.load_machine
        machine_role.load_machine = lambda: {"role": "primary", "db_write": True}
        try:
            machine_role.require_writer("test-op")
        finally:
            machine_role.load_machine = original


class TestMemoryRetrievalDatabaseMode(unittest.TestCase):
    def test_status_connection_is_sqlite_read_only(self):
        import memory_retrieval

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "memory.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE marker (value TEXT)")

        original_db = memory_retrieval.DB_PATH
        memory_retrieval.DB_PATH = db
        try:
            conn = memory_retrieval._open_db("status")
            self.addCleanup(conn.close)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO marker VALUES ('write')")
        finally:
            memory_retrieval.DB_PATH = original_db


class TestCalendarWriterGuard(unittest.TestCase):
    def test_legacy_satellite_config_can_open_its_local_mirror(self):
        import gcal
        import machine_role

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "calendar.db"
        db.touch()

        original_db = gcal.DB_PATH
        original_load_machine = machine_role.load_machine
        gcal.DB_PATH = db
        machine_role.load_machine = lambda: {"role": "satellite", "db_write": False}
        try:
            conn = gcal._connect_db()
            conn.close()
        finally:
            gcal.DB_PATH = original_db
            machine_role.load_machine = original_load_machine


class TestDatabaseFreshness(unittest.TestCase):
    def test_existing_local_database_is_current_by_definition(self):
        import pka_health

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "pka.db"
        db.touch()
        original_db = pka_health.DB_PATH
        pka_health.DB_PATH = db
        try:
            result = pka_health.check_db_freshness()
            self.assertEqual(result["status"], "ok")
            self.assertIn("local database present", result["detail"])
        finally:
            pka_health.DB_PATH = original_db


class TestHarnessAudit(unittest.TestCase):
    def test_core_routes_stay_within_budget(self):
        import harness_audit

        report = harness_audit.build_report()
        self.assertEqual(report["status"], "ok")
        self.assertLessEqual(report["routes"]["claude_core_words"], 2000)
        self.assertLessEqual(report["routes"]["codex_core_words"], 2000)


class TestTaskRecordState(unittest.TestCase):
    """Mutable task state must replace cleanly while history remains durable."""

    def setUp(self):
        import task_record

        self.task_record = task_record
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tasks = self.root / "tasks"
        self.tasks.mkdir()
        self.db = self.root / "pka.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "CREATE TABLE projects (id INTEGER PRIMARY KEY, status TEXT, updated_at TEXT)"
            )
            conn.execute("INSERT INTO projects VALUES (1, 'active', '2026-01-01')")

        self.original_tasks = task_record.TASKS_DIR
        self.original_db = task_record.DB_PATH
        self.original_index = task_record._index_task
        self.original_connect = task_record._connect
        self.original_upsert_project = task_record._upsert_project
        task_record.TASKS_DIR = self.tasks
        task_record.DB_PATH = self.db
        task_record._index_task = lambda *a, **k: {
            "knowledge_base_id": 1,
            "file_id": 1,
        }
        task_record._connect = lambda: sqlite3.connect(self.db)
        def fake_upsert(conn, **kwargs):
            conn.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = 1",
                (kwargs["status"], "2026-08-12"),
            )
            return 1

        task_record._upsert_project = fake_upsert
        self.addCleanup(self._restore)

        self.path = self.tasks / "pilot.md"
        self.path.write_text(
            """---
project_id: 1
status: active
priority: medium
updated_at: 2026-01-01T00:00:00
title: "Pilot"
tags: ["pilot"]
---

# Pilot

## Summary

Pilot summary.

## Current Focus

- Old focus that must stop governing.

## Open Loops

- Repair the first issue.
- Repair the second issue.

## Custom Evidence

- Preserve this unrelated section.

## Canonical Record

- This file is canonical.
""",
            encoding="utf-8",
        )

    def _restore(self):
        self.task_record.TASKS_DIR = self.original_tasks
        self.task_record.DB_PATH = self.original_db
        self.task_record._index_task = self.original_index
        self.task_record._connect = self.original_connect
        self.task_record._upsert_project = self.original_upsert_project

    def test_checkpoint_replaces_current_state_and_preserves_history(self):
        tr = self.task_record
        tr.checkpoint_task_record(
            name="Pilot",
            phase="implementation",
            status="active",
            owner="Sable",
            objective="Ship the pilot.",
            now="Implement replacement.",
            next_action="Run verification.",
            blockers=[],
            acceptance=["Tests pass."],
        )
        tr.checkpoint_task_record(
            name="Pilot",
            phase="verification",
            status="blocked",
            owner="Vera",
            objective="Verify the pilot.",
            now="Inspect the output.",
            next_action="Clear the blocker.",
            blockers=["Awaiting evidence."],
            acceptance=["Review passes."],
        )

        text = self.path.read_text(encoding="utf-8")
        _, sections = tr._parse_sectioned_markdown(text)
        self.assertIn("- Now: Inspect the output.", sections["Current State"])
        self.assertNotIn("Implement replacement.", sections["Current State"])
        self.assertIn("Implement replacement.", sections["Checkpoint History"])
        self.assertIn("Old focus that must stop governing.", sections["Checkpoint History"])
        self.assertIn("Preserve this unrelated section.", sections["Custom Evidence"])
        self.assertIn("project_key: pilot", text)
        self.assertNotIn("project_id:", text)
        self.assertLess(text.index("## Acceptance"), text.index("## Blockers"))
        self.assertLess(text.index("## Blockers"), text.index("## Canonical Record"))
        self.assertEqual(tr._parse_bullets(sections["Current Focus"]), [
            "Objective: Verify the pilot.",
            "Now: Inspect the output.",
            "Next: Clear the blocker.",
        ])
        with sqlite3.connect(self.db) as conn:
            status = conn.execute("SELECT status FROM projects WHERE id = 1").fetchone()[0]
        self.assertEqual(status, "blocked")

    def test_resolve_loop_moves_item_to_history(self):
        tr = self.task_record
        tr.resolve_open_loop(
            name="Pilot",
            loop="first issue",
            resolution="Fixed in the pilot.",
        )
        _, sections = tr._parse_sectioned_markdown(self.path.read_text(encoding="utf-8"))
        self.assertEqual(tr._parse_bullets(sections["Open Loops"]), ["Repair the second issue."])
        self.assertIn("Repair the first issue.", sections["Resolved"])
        self.assertIn("Fixed in the pilot.", sections["Resolved"])

    def test_upsert_preserves_checkpointed_sections(self):
        tr = self.task_record
        tr.checkpoint_task_record(
            name="Pilot",
            phase="implementation",
            status="active",
            owner="Sable",
            objective="Ship the pilot.",
            now="Implement replacement.",
            next_action="Run verification.",
            blockers=[],
            acceptance=["Tests pass."],
        )
        tr.upsert_task_record(
            name="Pilot",
            summary="",
            status="active",
            priority="medium",
            due_date=None,
            focus=[],
            artifact=[],
            decision=["Preserve checkpointed sections."],
            open_loop=[],
            tag=[],
        )
        _, sections = tr._parse_sectioned_markdown(self.path.read_text(encoding="utf-8"))
        self.assertIn("- Now: Implement replacement.", sections["Current State"])
        self.assertIn("Tests pass.", sections["Acceptance"])
        self.assertIn("Preserve checkpointed sections.", sections["Key Decisions"])

    def test_record_attempt_preserves_reason(self):
        tr = self.task_record
        tr.record_attempt(
            name="Pilot",
            approach="Retry the repair call.",
            outcome="The process stalled.",
            reason="The local model returned unstable JSON.",
            result="failed",
        )
        _, sections = tr._parse_sectioned_markdown(self.path.read_text(encoding="utf-8"))
        self.assertIn("Retry the repair call.", sections["Attempts"])
        self.assertIn("The process stalled.", sections["Attempts"])
        self.assertIn("unstable JSON", sections["Attempts"])

    def test_audit_is_read_only_and_flags_legacy_schema(self):
        before = self.path.read_bytes()
        result = self.task_record.audit_task_records(stale_days=30)
        after = self.path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(result["record_count"], 1)
        self.assertIn("missing_current_state", result["records"][0]["issues"])
        self.assertIn("missing_acceptance", result["records"][0]["issues"])


class TestHealthEscalation(unittest.TestCase):
    def setUp(self):
        import health_escalation

        self.health_escalation = health_escalation
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_path = Path(self.tmp.name) / "recurrence.json"
        self.finding = {
            "status": "attention_needed",
            "checks": [
                {
                    "check": "kb_provenance",
                    "status": "fail",
                    "detail": "Two sources are missing.",
                    "offenders": [{"kb_id": 454}, {"kb_id": 455}],
                },
                {"check": "git", "status": "ok", "detail": "clean"},
            ],
        }

    def test_daily_deduplication_and_three_strike_escalation(self):
        he = self.health_escalation
        for day in (1, 1, 2, 3):
            he.update_recurrence(
                self.finding,
                state_path=self.state_path,
                observed_at=datetime.fromisoformat(f"2026-08-{day:02d}T08:00:00-07:00"),
                threshold=3,
            )
        visible = he.list_escalations(
            state_path=self.state_path,
            on_date=datetime.fromisoformat("2026-08-03").date(),
        )
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["consecutive_runs"], 3)
        self.assertEqual(visible[0]["state"], "escalated")

    def test_clean_run_resolves_prior_finding(self):
        he = self.health_escalation
        he.update_recurrence(
            self.finding,
            state_path=self.state_path,
            observed_at=datetime.fromisoformat("2026-08-01T08:00:00-07:00"),
            threshold=1,
        )
        clean = {
            "status": "ok",
            "checks": [{"check": "kb_provenance", "status": "ok", "detail": "clean"}],
        }
        state = he.update_recurrence(
            clean,
            state_path=self.state_path,
            observed_at=datetime.fromisoformat("2026-08-02T08:00:00-07:00"),
            threshold=1,
        )
        item = next(iter(state["findings"].values()))
        self.assertEqual(item["state"], "resolved")
        self.assertEqual(he.list_escalations(state_path=self.state_path), [])


class TestVideoProjectWorkflow(unittest.TestCase):
    def setUp(self):
        import video_project

        self.vp = video_project
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original_dir = video_project.PROJECTS_DIR
        self.original_index = video_project._index_overview
        self.original_markdown_index = video_project.index_markdown_artifact
        video_project.PROJECTS_DIR = self.root / "projects"
        video_project._index_overview = lambda *a, **k: {
            "knowledge_base_id": 1,
            "file_id": 1,
        }
        video_project.index_markdown_artifact = lambda *a, **k: {
            "knowledge_base_id": 2,
            "file_id": 2,
        }
        self.addCleanup(self._restore)
        video_project.create_project(
            title="Pilot Video", slug="pilot-video", summary="Test the workflow."
        )

    def _restore(self):
        self.vp.PROJECTS_DIR = self.original_dir
        self.vp._index_overview = self.original_index
        self.vp.index_markdown_artifact = self.original_markdown_index

    def _file(self, name: str, content: str = "artifact") -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_approval_is_bound_to_artifact_hash(self):
        vp = self.vp
        brief = self._file("brief.md", "version one")
        vp.attach_artifact(slug="pilot-video", kind="brief", raw_path=str(brief))
        vp.advance_project(slug="pilot-video", target="brief-review")
        vp.approve_gate(slug="pilot-video", gate="brief", approved_by="John")
        project = vp.load_project("pilot-video")
        self.assertTrue(vp.approval_is_current(project, "brief"))

        brief.write_text("version two", encoding="utf-8")
        vp.attach_artifact(slug="pilot-video", kind="brief", raw_path=str(brief))
        project = vp.load_project("pilot-video")
        self.assertFalse(vp.approval_is_current(project, "brief"))
        with self.assertRaisesRegex(ValueError, "approval"):
            vp.advance_project(slug="pilot-video", target="brief-approved")

    def test_package_release_and_publication_gates(self):
        vp = self.vp
        master = self._file("master.mp4")
        qc = self._file("qc.md")
        package = self._file("package.md")
        thumbnail = self._file("thumb.png")
        for kind, path in (
            ("final_master", master),
            ("qc_report", qc),
            ("youtube_package", package),
            ("thumbnail", thumbnail),
        ):
            vp.attach_artifact(slug="pilot-video", kind=kind, raw_path=str(path))
        vp.record_qc(
            slug="pilot-video", report_path=str(qc), status="pass",
            checks={"complete_decode": "pass"},
        )
        project = vp.load_project("pilot-video")
        project["state"] = "master-qc"
        vp.save_project(project)
        vp.approve_gate(slug="pilot-video", gate="master", approved_by="John")
        vp.select_title(slug="pilot-video", title="Approved Title", option=2)
        vp.select_thumbnail(slug="pilot-video", raw_path=str(thumbnail))
        project = vp.load_project("pilot-video")
        project["state"] = "package-review"
        vp.save_project(project)
        vp.approve_gate(slug="pilot-video", gate="package", approved_by="John")
        self.assertTrue(vp.approval_is_current(vp.load_project("pilot-video"), "package"))
        vp.advance_project(slug="pilot-video", target="package-approved")
        vp.record_youtube_private(
            slug="pilot-video", video_id="abc123", url="https://youtu.be/abc123",
            privacy="private",
        )
        vp.record_youtube_qa(
            slug="pilot-video",
            passed=sorted(vp.YOUTUBE_QA_REQUIRED),
            not_applicable=[],
        )
        vp.approve_gate(slug="pilot-video", gate="release", approved_by="John")
        vp.advance_project(slug="pilot-video", target="release-approved")
        result = vp.record_publication(
            slug="pilot-video", channel="youtube", url="https://youtu.be/abc123"
        )
        self.assertEqual(result["published"], "youtube")

    def test_changing_selected_title_stales_package_approval(self):
        vp = self.vp
        master = self._file("master.mp4")
        package = self._file("package.md")
        thumbnail = self._file("thumb.png")
        for kind, path in (
            ("final_master", master), ("youtube_package", package), ("thumbnail", thumbnail),
        ):
            vp.attach_artifact(slug="pilot-video", kind=kind, raw_path=str(path))
        vp.select_title(slug="pilot-video", title="First approved title")
        vp.select_thumbnail(slug="pilot-video", raw_path=str(thumbnail))
        project = vp.load_project("pilot-video")
        project["state"] = "package-review"
        vp.save_project(project)
        vp.approve_gate(slug="pilot-video", gate="package", approved_by="John")
        vp.select_title(slug="pilot-video", title="Changed after approval")
        self.assertFalse(vp.approval_is_current(vp.load_project("pilot-video"), "package"))

    def test_thumbnail_reference_requires_rights_for_package_approval(self):
        vp = self.vp
        master = self._file("master.mp4")
        qc = self._file("qc.md")
        package = self._file("package.md")
        thumbnail = self._file("thumb.png")
        reference = self._file("reference.png")
        for kind, path in (
            ("final_master", master), ("qc_report", qc),
            ("youtube_package", package), ("thumbnail", thumbnail),
        ):
            vp.attach_artifact(slug="pilot-video", kind=kind, raw_path=str(path))
        vp.attach_artifact(
            slug="pilot-video", kind="thumbnail_reference", raw_path=str(reference)
        )
        vp.select_title(slug="pilot-video", title="Approved Title")
        vp.select_thumbnail(slug="pilot-video", raw_path=str(thumbnail))
        project = vp.load_project("pilot-video")
        project["state"] = "package-review"
        vp.save_project(project)
        with self.assertRaisesRegex(ValueError, "rights"):
            vp.approve_gate(slug="pilot-video", gate="package", approved_by="John")

    def test_pipeline_package_dry_run_stops_before_upload(self):
        import pipeline
        from types import SimpleNamespace

        vp = self.vp
        master = self._file("master.mp4")
        qc = self._file("qc.md")
        vp.attach_artifact(slug="pilot-video", kind="final_master", raw_path=str(master))
        vp.record_qc(
            slug="pilot-video", report_path=str(qc), status="pass",
            checks={"complete_decode": "pass"},
        )
        project = vp.load_project("pilot-video")
        project["state"] = "master-qc"
        vp.save_project(project)
        vp.approve_gate(slug="pilot-video", gate="master", approved_by="John")
        result = pipeline.package_project(SimpleNamespace(
            project="pilot-video", video=None, dry_run=True,
            no_thumbnail=False, reference_image=[], reference_rights="", yes=True,
        ))
        self.assertEqual(result, 0)
        self.assertEqual(vp.load_project("pilot-video")["state"], "master-qc")

    def test_import_master_enters_qc_gate_without_upstream_approvals(self):
        vp = self.vp
        master = self._file("imported-master.mp4")
        qc = self._file("imported-qc.md")
        vp.attach_artifact(slug="pilot-video", kind="final_master", raw_path=str(master))
        vp.record_qc(
            slug="pilot-video", report_path=str(qc), status="pass",
            checks={"complete_decode": "pass"},
        )

        result = vp.import_master_qc(
            slug="pilot-video", note="Production completed outside the tracker."
        )

        self.assertEqual(result["to"], "master-qc")
        project = vp.load_project("pilot-video")
        self.assertEqual(project["state"], "master-qc")
        self.assertEqual(project["approvals"], {})
        self.assertIn("outside the tracker", project["history"][-1]["note"])

    def test_import_master_requires_passing_qc(self):
        vp = self.vp
        master = self._file("failed-master.mp4")
        qc = self._file("failed-qc.md")
        vp.attach_artifact(slug="pilot-video", kind="final_master", raw_path=str(master))
        vp.record_qc(
            slug="pilot-video", report_path=str(qc), status="fail",
            checks={"complete_decode": "fail"},
        )

        with self.assertRaisesRegex(ValueError, "has not passed"):
            vp.import_master_qc(slug="pilot-video")

    def test_pipeline_upload_dry_run_requires_approved_package(self):
        import pipeline
        from types import SimpleNamespace

        vp = self.vp
        master = self._file("master.mp4")
        package = self._file("package.md")
        thumbnail = self._file("thumb.png")
        for kind, path in (
            ("final_master", master), ("youtube_package", package), ("thumbnail", thumbnail),
        ):
            vp.attach_artifact(slug="pilot-video", kind=kind, raw_path=str(path))
        vp.select_title(slug="pilot-video", title="Approved Title")
        vp.select_thumbnail(slug="pilot-video", raw_path=str(thumbnail))
        project = vp.load_project("pilot-video")
        project["state"] = "package-review"
        vp.save_project(project)
        vp.approve_gate(slug="pilot-video", gate="package", approved_by="John")
        vp.advance_project(slug="pilot-video", target="package-approved")
        result = pipeline.upload_project(SimpleNamespace(
            project="pilot-video", yes=True, dry_run=True,
        ))
        self.assertEqual(result, 0)
        self.assertEqual(vp.load_project("pilot-video")["state"], "package-approved")

    def test_active_blocker_prevents_advance_until_resolved(self):
        vp = self.vp
        brief = self._file("brief.md")
        vp.attach_artifact(slug="pilot-video", kind="brief", raw_path=str(brief))
        result = vp.add_blocker(
            slug="pilot-video", message="Need the missing source image.", owner="John"
        )
        with self.assertRaisesRegex(ValueError, "active blocker"):
            vp.advance_project(slug="pilot-video", target="brief-review")
        vp.resolve_blocker(
            slug="pilot-video", blocker_id=result["blocker"]["id"],
            resolution="John supplied the image.",
        )
        advanced = vp.advance_project(slug="pilot-video", target="brief-review")
        self.assertEqual(advanced["to"], "brief-review")

    def test_substack_brief_and_metrics_are_durable(self):
        vp = self.vp
        transcript = self._file("transcript.md", "# Transcript\n\nSpoken words.")
        brief = self._file("brief.md", "# Brief\n")
        vp.attach_artifact(slug="pilot-video", kind="transcript", raw_path=str(transcript))
        vp.attach_artifact(slug="pilot-video", kind="brief", raw_path=str(brief))
        result = vp.create_substack_brief(
            slug="pilot-video", pov="Tools should preserve judgment.",
            reader="Independent creators", mode="companion",
            adds="A practical operating framework.", cta="Try the workflow.",
            headline="Human gates, useful automation",
        )
        content = Path(result["substack_brief"]).read_text(encoding="utf-8")
        self.assertIn("Tools should preserve judgment.", content)
        self.assertIn(str(transcript), content)
        vp.record_metrics(
            slug="pilot-video", window="7d",
            values={"views": 1200, "ctr": 4.8}, note="First review.",
        )
        project = vp.load_project("pilot-video")
        self.assertEqual(project["metrics"][0]["views"], 1200)

    def test_package_review_combines_choices_and_rights(self):
        vp = self.vp
        package = self._file(
            "package.md",
            """# YouTube Content Package

## 1. Title Options
1. **Curiosity Gap** — *A Better Workflow*

## 2. YouTube Description
Useful description.

**Chapters**
- 0:00 — Opening

**Links**
- https://example.com

**Tags**
#workflow

## 3. Thumbnail Prompt
John at a desk with the words HUMAN GATES.
""",
        )
        reference = self._file("reference.png")
        vp.attach_artifact(slug="pilot-video", kind="youtube_package", raw_path=str(package))
        vp.attach_artifact(
            slug="pilot-video", kind="thumbnail_reference", raw_path=str(reference),
            source="John", rights="owner-created",
        )
        review = vp.package_review("pilot-video")
        self.assertEqual(review["titles"], ["A Better Workflow"])
        self.assertEqual(review["unverified_reference_rights"], [])

    def test_development_sync_binds_challenge_material_to_brief_approval(self):
        vp = self.vp
        development = self.root / "development" / "pilot-video"
        development.mkdir(parents=True)
        (development / "interview.md").write_text("Owner interview", encoding="utf-8")
        (development / "claims.json").write_text('{"claims": []}', encoding="utf-8")
        challenge = development / "challenge.md"
        challenge.write_text("First challenge pass", encoding="utf-8")
        (development / "outline.md").write_text("Cleared outline", encoding="utf-8")
        (development / "brief.md").write_text("Approved brief", encoding="utf-8")

        synced = vp.sync_development(slug="pilot-video", development_dir=development)
        self.assertIn("claims_register", synced["attached"])
        self.assertIn("challenge_report", synced["attached"])
        vp.advance_project(slug="pilot-video", target="brief-review")
        vp.approve_gate(slug="pilot-video", gate="brief", approved_by="John")
        self.assertTrue(vp.approval_is_current(vp.load_project("pilot-video"), "brief"))

        challenge.write_text("Revised challenge pass", encoding="utf-8")
        vp.attach_artifact(
            slug="pilot-video", kind="challenge_report", raw_path=str(challenge)
        )
        self.assertFalse(vp.approval_is_current(vp.load_project("pilot-video"), "brief"))


class TestVideoQC(unittest.TestCase):
    def test_probe_evaluation_reports_video_and_audio(self):
        import video_qc

        probe = {
            "format": {"duration": "120.5"},
            "streams": [
                {
                    "codec_type": "video", "codec_name": "h264",
                    "width": 1920, "height": 1080, "avg_frame_rate": "30/1",
                },
                {
                    "codec_type": "audio", "codec_name": "aac",
                    "sample_rate": "48000", "channels": 2,
                },
            ],
        }
        checks, metadata = video_qc.evaluate_probe(probe)
        self.assertFalse(any(item["status"] == "fail" for item in checks))
        self.assertEqual(metadata["width"], 1920)
        self.assertEqual(metadata["sample_rate"], 48000)

    def test_probe_evaluation_fails_without_audio(self):
        import video_qc

        checks, _ = video_qc.evaluate_probe({
            "format": {"duration": "10"},
            "streams": [{
                "codec_type": "video", "codec_name": "h264",
                "width": 1920, "height": 1080, "avg_frame_rate": "30/1",
            }],
        })
        audio = next(item for item in checks if item["check"] == "audio_stream")
        self.assertEqual(audio["status"], "fail")


class TestYouTubeUploadCLI(unittest.TestCase):
    def test_upload_accepts_selected_thumbnail(self):
        import publish_to_youtube

        args = publish_to_youtube.build_parser().parse_args([
            "upload", "--video", "master.mp4", "--package", "package.md",
            "--title", "Chosen title", "--privacy", "private",
            "--thumbnail", "thumb.png",
        ])
        self.assertEqual(args.thumbnail, "thumb.png")


class TestBalanceCheck(unittest.TestCase):
    def test_rank_overlaps_finds_similar_doc(self):
        import balance_check
        from collections import Counter

        def doc(path, text):
            tokens = balance_check.tokenize(text)
            return {
                "kind": "blog", "path": path, "title": path, "body": text,
                "tf": Counter(tokens), "length": len(tokens),
            }

        docs = [
            doc("match.md", "agents read markdown knowledge files " * 20),
            doc("other.md", "sonoma rent receipts depositor ledger " * 20),
        ]
        ranked = balance_check.rank_overlaps(
            "an agent that reads markdown knowledge files", docs, top=2
        )
        self.assertTrue(ranked)
        self.assertEqual(ranked[0]["path"], "match.md")
        self.assertGreater(ranked[0]["score"], ranked[-1]["score"] if len(ranked) > 1 else 0)

    def test_strip_frontmatter(self):
        import balance_check

        text = "---\ntitle: x\n---\n# Body\n"
        self.assertEqual(balance_check.strip_frontmatter(text), "# Body\n")


class TestIndexRoundTrip(unittest.TestCase):
    """index_markdown_artifact must upsert, not duplicate, on re-index."""

    SCHEMA = """
    CREATE TABLE files (
        id INTEGER PRIMARY KEY,
        filename TEXT, filepath TEXT, category TEXT,
        description TEXT, file_type TEXT, ocr_text TEXT
    );
    CREATE TABLE knowledge_base (
        id INTEGER PRIMARY KEY,
        title TEXT, content TEXT, category TEXT, tags TEXT,
        source_file TEXT, created_at TEXT, updated_at TEXT,
        source_url TEXT, confidence TEXT
    );
    """

    def test_round_trip(self):
        import machine_role
        import pka_index

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "test.db"
        with sqlite3.connect(db) as conn:
            conn.executescript(self.SCHEMA)

        note = Path(tmp.name) / "note.md"
        note.write_text("# Test Note\n\nBody text.\n", encoding="utf-8")

        original_db = pka_index.DB_PATH
        original_guard = machine_role.require_writer
        pka_index.DB_PATH = db
        machine_role.require_writer = lambda *a, **k: None
        try:
            first = pka_index.index_markdown_artifact(
                note, title="Test Note", category="research", tags=["t1", "t2"]
            )
            second = pka_index.index_markdown_artifact(
                note, title="Test Note v2", category="research", tags="t1"
            )
        finally:
            pka_index.DB_PATH = original_db
            machine_role.require_writer = original_guard

        self.assertEqual(first["knowledge_base_id"], second["knowledge_base_id"])
        self.assertEqual(first["file_id"], second["file_id"])
        with sqlite3.connect(db) as conn:
            (kb_count,) = conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()
            (title,) = conn.execute("SELECT title FROM knowledge_base").fetchone()
        self.assertEqual(kb_count, 1)
        self.assertEqual(title, "Test Note v2")


if __name__ == "__main__":
    unittest.main()
