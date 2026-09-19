"""Tests for the handoff contract (tools/handoff.py).

The contract is proven by what imports refuse as much as by what they apply.
No network, no model service. Run from the repo root:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

import handoff  # noqa: E402


class HandoffCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.interview = self.dir / "interview.md"
        self.interview.write_text("Q: Why?\nA: Because the coffee break test works.\n", encoding="utf-8")
        self.applied: list[list[dict]] = []

    def prepare(self, **overrides):
        kwargs = dict(
            stage="challenge.extract-claims",
            directory=self.dir,
            inputs={"interview": self.interview},
            payload={"windows": [[0, 40]]},
            slug="pilot",
        )
        kwargs.update(overrides)
        return handoff.prepare(**kwargs)

    def response_for(self, request: handoff.Request, items: list[dict], **overrides) -> Path:
        data = {
            "schema_version": 1,
            "request_id": request.request_id,
            "stage": request.data["stage"],
            "inputs": {role: spec["sha256"] for role, spec in request.data["inputs"].items()},
            "items": items,
        }
        data.update(overrides)
        request.response_path.write_text(json.dumps(data), encoding="utf-8")
        return request.response_path

    def apply(self, items):
        self.applied.append(items)
        return {"count": len(items)}

    def do_import(self, request, **kwargs):
        return handoff.import_response(request.path, apply=self.apply, **kwargs)

    def assert_rejected(self, code, request, **kwargs):
        with self.assertRaises(handoff.HandoffError) as ctx:
            self.do_import(request, **kwargs)
        self.assertEqual(ctx.exception.code, code, str(ctx.exception))
        self.assertEqual(self.applied, [], "a rejected response must apply nothing")
        self.assertFalse(handoff.ledger_path_for(request.path).exists(), "a rejection is not recorded as an import")


class TestPrepare(HandoffCase):
    def test_request_records_input_hashes_and_schema(self):
        request = self.prepare()
        data = json.loads(request.path.read_text())
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["stage"], "challenge.extract-claims")
        self.assertEqual(data["inputs"]["interview"]["sha256"], handoff.sha256_file(self.interview))
        self.assertEqual(data["payload"], {"windows": [[0, 40]]})
        self.assertIsNone(data["expects"])
        self.assertEqual(request.response_path.name, "challenge.extract-claims.response.json")

    def test_request_id_is_deterministic_over_inputs_and_payload(self):
        first = self.prepare().request_id
        second = self.prepare().request_id
        self.assertEqual(first, second)
        self.interview.write_text("changed", encoding="utf-8")
        self.assertNotEqual(first, self.prepare().request_id)
        self.assertNotEqual(first, self.prepare(payload={"windows": []}).request_id)

    def test_expected_ids_are_recorded(self):
        request = self.prepare(expects_ids=["c1", "c2"])
        self.assertEqual(request.data["expects"], {"ids": ["c1", "c2"]})

    def test_missing_input_is_refused(self):
        with self.assertRaises(handoff.HandoffError) as ctx:
            self.prepare(inputs={"interview": self.dir / "nope.md"})
        self.assertEqual(ctx.exception.code, "file_missing")


class TestImportAppliesOnce(HandoffCase):
    def test_valid_response_is_applied_and_recorded(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1", "text": "the break works"}])
        result = self.do_import(request)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.result, {"count": 1})
        self.assertEqual(self.applied, [[{"id": "c1", "text": "the break works"}]])
        ledger = handoff.read_ledger(handoff.ledger_path_for(request.path))
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["request_id"], request.request_id)
        self.assertEqual(ledger[0]["response_sha256"], result.response_sha256)

    def test_reimporting_the_same_response_is_a_noop(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}])
        first = self.do_import(request)
        second = self.do_import(request)
        self.assertEqual(second.status, "already_imported")
        self.assertEqual(second.response_sha256, first.response_sha256)
        self.assertEqual(len(self.applied), 1, "apply must run exactly once")
        self.assertEqual(len(handoff.read_ledger(handoff.ledger_path_for(request.path))), 1)

    def test_a_revised_response_to_the_same_request_is_applied_again(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1", "verdict": "weak"}])
        self.do_import(request)
        self.response_for(request, [{"id": "c1", "verdict": "strong"}])
        result = self.do_import(request)
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(self.applied), 2)

    def test_item_validator_sees_every_item(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1", "quote": "coffee break test"}, {"id": "c2", "quote": "Because"}])
        source = self.interview.read_text()
        seen = []

        def validator(item):
            seen.append(item["id"])
            handoff.require_quote(source, item)

        self.do_import(request, item_validator=validator)
        self.assertEqual(seen, ["c1", "c2"])


class TestImportRefuses(HandoffCase):
    def test_stale_input_when_file_changes_after_request(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}])
        self.interview.write_text("A revised interview.\n", encoding="utf-8")
        self.assert_rejected("stale_input", request)

    def test_stale_input_when_response_echoes_a_different_hash(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}], inputs={"interview": "0" * 64})
        self.assert_rejected("stale_input", request)

    def test_stale_input_when_response_omits_the_echo(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}], inputs={})
        self.assert_rejected("stale_input", request)

    def test_unknown_request_id(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}], request_id="deadbeefdeadbeef")
        self.assert_rejected("unknown_request", request)

    def test_wrong_stage(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}], stage="challenge.context-review")
        self.assert_rejected("stage_mismatch", request)

    def test_wrong_schema_version(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}], schema_version=2)
        self.assert_rejected("schema_version", request)

    def test_duplicate_ids(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}, {"id": "c1"}])
        self.assert_rejected("duplicate_ids", request)

    def test_items_without_ids(self):
        request = self.prepare()
        self.response_for(request, [{"text": "no id"}])
        self.assert_rejected("bad_items", request)

    def test_missing_expected_ids(self):
        request = self.prepare(expects_ids=["c1", "c2"])
        self.response_for(request, [{"id": "c1"}])
        self.assert_rejected("missing_ids", request)

    def test_unknown_ids_when_ids_are_expected(self):
        request = self.prepare(expects_ids=["c1"])
        self.response_for(request, [{"id": "c1"}, {"id": "c9"}])
        self.assert_rejected("unknown_ids", request)

    def test_quote_not_in_source(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1", "quote": "words that were never said"}])
        source = self.interview.read_text()
        self.assert_rejected("quote_not_found", request, item_validator=lambda item: handoff.require_quote(source, item))

    def test_missing_response_file(self):
        request = self.prepare()
        self.assert_rejected("file_missing", request)

    def test_malformed_response_file(self):
        request = self.prepare()
        request.response_path.write_text("{not json", encoding="utf-8")
        self.assert_rejected("bad_json", request)


class TestSequentialStages(HandoffCase):
    """A later stage cannot land before the stage it depends on, and a change
    to the earlier stage's output makes the later request stale."""

    def chain(self):
        package = self.dir / "package.md"
        package.write_text("## Chapters\n0:00 Intro\n", encoding="utf-8")
        first = self.prepare(stage="pipeline.package", inputs={"transcript": self.interview})
        second = self.prepare(
            stage="pipeline.chapter-review",
            inputs={"transcript": self.interview, "package": package},
            depends_on=[first],
            expects_ids=["ch0"],
        )
        return first, second, package

    def test_dependency_is_recorded(self):
        first, second, _ = self.chain()
        self.assertEqual(second.data["depends_on"], [{"stage": "pipeline.package", "request_id": first.request_id}])

    def test_later_stage_refused_until_earlier_stage_is_imported(self):
        first, second, _ = self.chain()
        self.response_for(second, [{"id": "ch0", "ok": True}])
        self.assert_rejected("missing_stage", second)
        self.response_for(first, [{"id": "pkg"}])
        self.do_import(first)
        result = self.do_import(second)
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(self.applied), 2)

    def test_changed_package_after_review_request_is_stale(self):
        first, second, package = self.chain()
        self.response_for(first, [{"id": "pkg"}])
        self.do_import(first)
        self.applied.clear()
        self.response_for(second, [{"id": "ch0", "ok": True}])
        package.write_text("## Chapters\n0:00 Intro\n1:30 Revised\n", encoding="utf-8")
        with self.assertRaises(handoff.HandoffError) as ctx:
            self.do_import(second)
        self.assertEqual(ctx.exception.code, "stale_input")
        self.assertEqual(ctx.exception.details.get("role"), "package")
        self.assertEqual(self.applied, [])

    def test_dependency_on_a_different_run_does_not_count(self):
        first, second, _ = self.chain()
        other = self.prepare(stage="pipeline.package", inputs={"transcript": self.interview}, payload={"other": 1})
        self.response_for(other, [{"id": "pkg"}])
        self.do_import(other)
        self.applied.clear()
        self.response_for(second, [{"id": "ch0"}])
        self.assert_rejected_keeping_ledger("missing_stage", second)

    def assert_rejected_keeping_ledger(self, code, request):
        with self.assertRaises(handoff.HandoffError) as ctx:
            self.do_import(request)
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(self.applied, [])

    def test_cli_validate_reports_missing_stage(self):
        _, second, _ = self.chain()
        self.response_for(second, [{"id": "ch0"}])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = handoff.main(["validate", "--request", str(second.path)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err.getvalue())["error"], "missing_stage")


class TestApplyFailure(HandoffCase):
    def test_failed_apply_is_not_recorded_and_can_be_retried(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}])
        calls = []

        def flaky(items):
            calls.append(items)
            if len(calls) == 1:
                raise RuntimeError("disk full")
            return "ok"

        with self.assertRaises(RuntimeError):
            handoff.import_response(request.path, apply=flaky)
        self.assertEqual(handoff.read_ledger(handoff.ledger_path_for(request.path)), [])
        result = handoff.import_response(request.path, apply=flaky)
        self.assertEqual(result.status, "applied")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(handoff.read_ledger(handoff.ledger_path_for(request.path))), 1)


class TestQuoteMatching(unittest.TestCase):
    def test_exact_and_whitespace_tolerant_match(self):
        source = "the coffee\n  break test works"
        self.assertEqual(handoff.find_quote(source, "coffee\n  break"), (4, 18))
        self.assertIsNotNone(handoff.find_quote(source, "coffee break test"))
        self.assertIsNone(handoff.find_quote(source, "tea break"))
        self.assertIsNone(handoff.find_quote(source, "   "))


class TestApprovalsUntouched(unittest.TestCase):
    """Importing an assistant response never creates, changes, or removes an owner approval."""

    def setUp(self):
        import video_project

        self.vp = video_project
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = {
            "PROJECTS_DIR": video_project.PROJECTS_DIR,
            "_index_overview": video_project._index_overview,
            "index_markdown_artifact": video_project.index_markdown_artifact,
            "forget_markdown_artifact": video_project.forget_markdown_artifact,
        }
        video_project.PROJECTS_DIR = self.root / "projects"
        video_project._index_overview = lambda *a, **k: {"knowledge_base_id": 1, "file_id": 1}
        video_project.index_markdown_artifact = lambda *a, **k: {"knowledge_base_id": 2, "file_id": 2}
        video_project.forget_markdown_artifact = lambda path: None
        self.addCleanup(self._restore)
        video_project.create_project(title="Handoff Pilot", slug="handoff-pilot", summary="approvals stay put")

    def _restore(self):
        for key, value in self.original.items():
            setattr(self.vp, key, value)

    def test_module_has_no_approval_surface(self):
        source = (PKA_ROOT / "tools" / "handoff.py").read_text(encoding="utf-8")
        self.assertNotIn("approve_gate", source)
        self.assertNotIn("import video_project", source)
        self.assertNotIn('["approvals"]', source)

    def test_import_that_attaches_an_artifact_leaves_approvals_unchanged(self):
        interview = self.root / "interview.md"
        interview.write_text("A: the position is clear.\n", encoding="utf-8")
        request = handoff.prepare(
            stage="challenge.extract-claims", directory=self.root, inputs={"interview": interview}, slug="handoff-pilot"
        )
        request.response_path.write_text(json.dumps({
            "schema_version": 1,
            "request_id": request.request_id,
            "stage": request.data["stage"],
            "inputs": {"interview": request.data["inputs"]["interview"]["sha256"]},
            "items": [{"id": "c1", "quote": "the position is clear"}],
        }), encoding="utf-8")

        before = copy.deepcopy(self.vp.load_project("handoff-pilot"))
        self.assertEqual(before.get("approvals", {}), {})

        def apply(items):
            register = self.root / "claims.json"
            register.write_text(json.dumps({"claims": items}), encoding="utf-8")
            return self.vp.attach_artifact(slug="handoff-pilot", kind="claims_register", raw_path=str(register))

        first = handoff.import_response(request.path, apply=apply)
        second = handoff.import_response(request.path, apply=apply)
        after = self.vp.load_project("handoff-pilot")

        self.assertEqual(first.status, "applied")
        self.assertEqual(second.status, "already_imported")
        self.assertIsNotNone(self.vp.current_artifact(after, "claims_register"))
        self.assertEqual(after.get("approvals", {}), before.get("approvals", {}))
        self.assertEqual(after["state"], before["state"])
        for gate in self.vp.GATE_ARTIFACTS:
            self.assertFalse(self.vp.approval_is_current(after, gate), gate)


class TestApprovalGoesStale(TestApprovalsUntouched):
    """An owner approval is bound to the exact bytes approved. The final-article
    gate (T10) inherits this; the blog gate proves the mechanism today."""

    def approve_blog(self, content: str) -> Path:
        draft = self.root / "draft.md"
        draft.write_text(content, encoding="utf-8")
        self.vp.attach_artifact(slug="handoff-pilot", kind="blog_draft", raw_path=str(draft))
        project = self.vp.load_project("handoff-pilot")
        project["state"] = "blog-review"
        self.vp.save_project(project)
        self.vp.approve_gate(slug="handoff-pilot", gate="blog", approved_by="owner")
        self.assertTrue(self.vp.approval_is_current(self.vp.load_project("handoff-pilot"), "blog"))
        return draft

    def test_in_place_edit_invalidates_the_approval(self):
        draft = self.approve_blog("approved text\n")
        draft.write_text("approved text, then edited after approval\n", encoding="utf-8")
        self.assertFalse(self.vp.approval_is_current(self.vp.load_project("handoff-pilot"), "blog"))

    def test_reattaching_a_changed_file_invalidates_the_approval(self):
        draft = self.approve_blog("approved text\n")
        draft.write_text("second version\n", encoding="utf-8")
        self.vp.attach_artifact(slug="handoff-pilot", kind="blog_draft", raw_path=str(draft))
        self.assertFalse(self.vp.approval_is_current(self.vp.load_project("handoff-pilot"), "blog"))

    def test_deleted_file_invalidates_the_approval(self):
        draft = self.approve_blog("approved text\n")
        draft.unlink()
        self.assertFalse(self.vp.approval_is_current(self.vp.load_project("handoff-pilot"), "blog"))

    def test_restoring_the_approved_bytes_restores_the_approval(self):
        draft = self.approve_blog("approved text\n")
        draft.write_text("tampered\n", encoding="utf-8")
        self.assertFalse(self.vp.approval_is_current(self.vp.load_project("handoff-pilot"), "blog"))
        draft.write_text("approved text\n", encoding="utf-8")
        self.assertTrue(self.vp.approval_is_current(self.vp.load_project("handoff-pilot"), "blog"))

    def test_import_after_approval_does_not_refresh_a_stale_approval(self):
        draft = self.approve_blog("approved text\n")
        draft.write_text("edited\n", encoding="utf-8")
        request = handoff.prepare(stage="article.review", directory=self.root, inputs={"draft": draft})
        request.response_path.write_text(json.dumps({
            "schema_version": 1, "request_id": request.request_id, "stage": "article.review",
            "inputs": {"draft": request.data["inputs"]["draft"]["sha256"]}, "items": [{"id": "r1"}],
        }), encoding="utf-8")
        handoff.import_response(request.path, apply=lambda items: None)
        self.assertFalse(self.vp.approval_is_current(self.vp.load_project("handoff-pilot"), "blog"))


class TestCLI(HandoffCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = handoff.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_validate_reports_valid_and_import_status(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}])
        code, out, _ = self.run_cli("validate", "--request", str(request.path))
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "valid")
        self.assertFalse(payload["already_imported"])
        self.do_import(request)
        code, out, _ = self.run_cli("validate", "--request", str(request.path))
        self.assertTrue(json.loads(out)["already_imported"])

    def test_validate_reports_the_rule_that_failed(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}, {"id": "c1"}])
        code, _, err = self.run_cli("validate", "--request", str(request.path))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(err)["error"], "duplicate_ids")

    def test_ledger_lists_imports(self):
        request = self.prepare()
        self.response_for(request, [{"id": "c1"}])
        self.do_import(request)
        code, out, _ = self.run_cli("ledger", str(self.dir))
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["imports"]), 1)


if __name__ == "__main__":
    unittest.main()
