"""Tests for the balance check handoff (tools/balance_check.py).

No network, no model service: the assistant's analysis is a response file the
tests write. Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

import balance_check  # noqa: E402
import handoff  # noqa: E402

PRIOR = (
    "# Micro markets\n\n"
    "Micro markets reward infinite variety. I have argued for years that the "
    "creator should own the workflow end to end, and that outsourcing the edit "
    "is how a voice gets lost. " * 6
)
OUTLINE = (
    "# Owning the edit\n\n"
    "- The creator should own the workflow end to end\n"
    "- Micro markets reward infinite variety\n"
    "- Outsourcing the edit is fine when the voice survives\n"
)


class BalanceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        blog = self.root / "owners-inbox" / "blog"
        blog.mkdir(parents=True)
        (blog / "micro-markets.md").write_text(PRIOR, encoding="utf-8")
        (blog / "unrelated.md").write_text("# Gardening\n\n" + "tomatoes need staking and water " * 40, encoding="utf-8")
        brand = self.root / "owners-inbox" / "brand-system.md"
        brand.write_text("# Brand\n\nPlain words. No hype.\n", encoding="utf-8")
        self.dev = self.root / "owners-inbox" / "development" / "owning-the-edit"
        self.dev.mkdir(parents=True)
        self.outline = self.dev / "outline.md"
        self.outline.write_text(OUTLINE, encoding="utf-8")

        self.patches = [
            patch.object(balance_check, "PKA_ROOT", self.root),
            patch.object(balance_check, "ARCHIVE_DIRS", (("blog", blog),)),
            patch.object(balance_check, "BRAND_SYSTEM", brand),
            patch.object(balance_check, "DEVELOPMENT_DIR", self.root / "owners-inbox" / "development"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", err):
            code = balance_check.main(list(argv))
        payload = out.getvalue() or err.getvalue()
        return code, (json.loads(payload) if payload.strip().startswith("{") else payload)

    def prepare(self, *extra):
        return self.run_cli("--slug", "owning-the-edit", "prepare", "--outline", str(self.outline), *extra)

    def respond(self, items):
        request = handoff.load_request(self.dev / f"{balance_check.STAGE}.request.json")
        request.response_path.write_text(json.dumps({
            "schema_version": 1, "request_id": request.request_id, "stage": balance_check.STAGE,
            "inputs": {role: spec["sha256"] for role, spec in request.data["inputs"].items()},
            "items": items,
        }), encoding="utf-8")
        return request

    def do_import(self):
        return self.run_cli("--slug", "owning-the-edit", "import")


class TestPrepare(BalanceCase):
    def test_lexical_scan_and_request(self):
        code, out = self.prepare()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["status"], "prepared")
        self.assertEqual(out["top_overlap"], "owners-inbox/blog/micro-markets.md")
        # The gardening post shares no vocabulary with the outline, so it is not an overlap.
        self.assertEqual(out["sources"], ["owners-inbox/blog/micro-markets.md"])
        request = handoff.load_request(self.dev / f"{balance_check.STAGE}.request.json")
        self.assertEqual(set(request.data["inputs"]), {"outline", "source_1", "brand_system"})
        payload = request.data["payload"]
        self.assertIn("own the workflow", payload["outline"])
        self.assertIn("Plain words", payload["brand_system"])
        self.assertTrue(payload["sources"][0]["excerpt"].startswith("# Micro markets"))
        self.assertIn("VERBATIM", request.data["instructions"].upper())
        report = (self.dev / "balance-check.md").read_text()
        self.assertIn("## Retread Risk", report)
        self.assertIn("micro-markets.md", report)
        self.assertIn("analysis pending", report)

    def test_lexical_only_writes_report_without_request(self):
        code, out = self.prepare("--lexical-only")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["status"], "lexical_only")
        self.assertTrue((self.dev / "balance-check.md").exists())
        self.assertFalse((self.dev / f"{balance_check.STAGE}.request.json").exists())
        self.assertIn("lexical scan only", (self.dev / "balance-check.md").read_text())

    def test_directory_defaults_to_the_outline_folder(self):
        code, out = self.run_cli("prepare", "--outline", str(self.outline))
        self.assertEqual(code, 0, out)
        self.assertEqual(Path(out["report"]), self.dev / "balance-check.md")

    def test_missing_outline(self):
        code, out = self.run_cli("prepare", "--outline", str(self.dev / "nope.md"))
        self.assertEqual(code, 1)


class TestImport(BalanceCase):
    def contradiction(self, quote="outsourcing the edit is how a voice gets lost", **extra):
        return {"id": "x1", "kind": "contradiction",
                "outline_position": "Outsourcing the edit is fine when the voice survives",
                "prior_position": "Outsourcing the edit loses the voice",
                "source": "owners-inbox/blog/micro-markets.md", "quote": quote, "severity": "high", **extra}

    def test_verified_findings_land_in_the_report(self):
        self.prepare()
        self.respond([
            self.contradiction(),
            {"id": "r1", "kind": "retread_note", "source": "owners-inbox/blog/micro-markets.md",
             "note": "Same infinite-variety ground; fresh if it names a new market."},
            {"id": "b1", "kind": "brand_drift", "note": "Bullet three hedges.", "brand_rule": "Plain words. No hype."},
        ])
        code, out = self.do_import()
        self.assertEqual(code, 0, out)
        self.assertEqual((out["contradictions"], out["retread_notes"], out["brand_drift"]), (1, 1, 1))
        report = (self.dev / "balance-check.md").read_text()
        self.assertIn("**[high]** Outline says: Outsourcing the edit is fine", report)
        self.assertIn("Quote (verified)", report)
        self.assertIn("### Retread Notes", report)
        self.assertIn("Bullet three hedges.", report)
        self.assertNotIn("analysis pending", report)
        self.assertIn("checked verbatim", report)

    def test_empty_findings_are_a_clean_report(self):
        self.prepare()
        self.respond([])
        code, out = self.do_import()
        self.assertEqual(code, 0, out)
        report = (self.dev / "balance-check.md").read_text()
        self.assertIn("None found against the top-overlap sources.", report)
        self.assertIn("On-brand against the brand-system document.", report)

    def test_unverifiable_quote_is_refused_and_report_unchanged(self):
        self.prepare()
        before = (self.dev / "balance-check.md").read_text()
        self.respond([self.contradiction(quote="a sentence that was never written")])
        code, out = self.do_import()
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "quote_not_found")
        self.assertEqual((self.dev / "balance-check.md").read_text(), before)

    def test_source_outside_the_request_is_refused(self):
        self.prepare()
        self.respond([self.contradiction(source="owners-inbox/blog/other.md")])
        code, out = self.do_import()
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "item_invalid")
        self.assertIn("owners-inbox/blog/micro-markets.md", out["allowed"])

    def test_bad_kind_and_missing_fields_are_refused(self):
        self.prepare()
        for bad in ({"id": "z", "kind": "opinion"},
                    {"id": "z", "kind": "brand_drift", "note": "x"},
                    {"id": "z", "kind": "retread_note", "source": "owners-inbox/blog/micro-markets.md", "note": " "},
                    self.contradiction(severity="catastrophic")):
            with self.subTest(bad=bad):
                self.respond([bad])
                code, out = self.do_import()
                self.assertEqual(code, 1)
                self.assertEqual(out["error"], "item_invalid")

    def test_reimport_is_a_noop(self):
        self.prepare()
        self.respond([self.contradiction()])
        self.do_import()
        code, out = self.do_import()
        self.assertEqual(out["status"], "already_imported")
        self.assertEqual(len(handoff.read_ledger(self.dev / "handoff-imports.json")), 1)

    def test_edited_outline_makes_the_response_stale(self):
        self.prepare()
        self.respond([])
        self.outline.write_text(OUTLINE + "- A fourth beat\n", encoding="utf-8")
        code, out = self.do_import()
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "stale_input")
        self.assertEqual(out["role"], "outline")

    def test_edited_source_makes_the_response_stale(self):
        self.prepare()
        self.respond([self.contradiction()])
        (self.root / "owners-inbox" / "blog" / "micro-markets.md").write_text(PRIOR + "\nAddendum.\n", encoding="utf-8")
        code, out = self.do_import()
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "stale_input")
        self.assertEqual(out["role"], "source_1")

    def test_import_before_prepare(self):
        code, out = self.do_import()
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "file_missing")


if __name__ == "__main__":
    unittest.main()
