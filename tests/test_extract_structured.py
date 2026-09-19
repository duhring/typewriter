"""Structured extracts as assistant handoffs (tools/extract_structured.py),
and fetch-transcript no longer analyzing what it fetches.

No network, no model service, no real index. Run from the repo root:

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

import extract_structured as es  # noqa: E402
import handoff  # noqa: E402

TRANSCRIPT = """# The Laptop Test

**Video:** https://youtu.be/abc123

00:00 Intro: why this matters
01:30 The laptop contribution proved the workflow travels.
04:10 Critics say only coders benefit, and I disagree.
"""


class ExtractCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.transcript = self.root / "2026-09-18-laptop-test.md"
        self.transcript.write_text(TRANSCRIPT, encoding="utf-8")
        self.out = self.root / "transcript-extracts"
        self.meet = self.root / "meeting-extracts"
        self.indexed = []
        for name, value in [("TRANSCRIPT_EXTRACTS_DIR", self.out), ("MEETING_EXTRACTS_DIR", self.meet),
                            ("PKA_ROOT", self.root),
                            ("index_markdown_artifact", lambda path, **kw: (self.indexed.append((path, kw)) or {"knowledge_base_id": 7, "file_id": 8})),
                            ("_update_meeting_row", lambda *a, **k: self.indexed.append(("meeting-row", a)))]:
            m = patch.object(es, name, value); m.start(); self.addCleanup(m.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", err):
            code = es.main(list(argv))
        payload = out.getvalue() or err.getvalue()
        return code, (json.loads(payload) if payload.strip().startswith("{") else payload)

    def respond(self, directory, stage, item):
        request = handoff.load_request(directory / f"{stage}.request.json")
        request.response_path.write_text(json.dumps({
            "schema_version": 1, "request_id": request.request_id, "stage": stage,
            "inputs": {k: v["sha256"] for k, v in request.data["inputs"].items()},
            "items": [item] if item is not None else [],
        }), encoding="utf-8")

    def extract_item(self, **overrides):
        item = {"id": "extract", "summary": "Why the workflow travels.",
                "timestamp_highlights": [{"timestamp": "01:30", "label": "The laptop test"}],
                "key_points": ["The workflow travels", "The workflow travels"],
                "reusable_claims": ["Anyone can use the system"], "questions_raised": [],
                "action_ideas": ["Write the laptop post"], "tools_mentioned": ["CueCam"],
                "recommended_tags": ["workflow", "laptop"]}
        item.update(overrides)
        return item


class TestTranscriptExtract(ExtractCase):
    def test_prepare_writes_request_with_text_and_defaults(self):
        code, out = self.run_cli("transcript", "prepare", "--file", str(self.transcript))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["title"], "The Laptop Test")
        request = handoff.load_request(Path(out["request"]))
        self.assertEqual(Path(out["dir"]), self.out / "requests" / self.transcript.stem)
        self.assertEqual(request.data["expects"], {"ids": ["extract"]})
        self.assertEqual(request.data["payload"]["source_url"], "https://youtu.be/abc123")
        self.assertIn("01:30", request.data["payload"]["text"])
        self.assertIn("checked on import", request.data["instructions"])

    def test_import_writes_and_indexes_the_extract(self):
        _, out = self.run_cli("transcript", "prepare", "--file", str(self.transcript))
        directory = Path(out["dir"])
        self.respond(directory, es.STAGE_TRANSCRIPT, self.extract_item())
        code, result = self.run_cli("transcript", "import", "--dir", str(directory))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["key_points"], ["The workflow travels"], "duplicates are collapsed as before")
        body = Path(result["path"]).read_text()
        self.assertEqual(Path(result["path"]).name, "2026-09-18-the-laptop-test.md")
        self.assertIn("# The Laptop Test — Structured Transcript Extract", body)
        self.assertIn("- 01:30 — The laptop test", body)
        self.assertIn("## Reusable Claims\n\n- Anyone can use the system", body)
        self.assertEqual(self.indexed[0][1]["category"], "transcript-extract")
        self.assertIn("laptop", self.indexed[0][1]["tags"])

    def test_timestamp_not_in_transcript_is_refused(self):
        _, out = self.run_cli("transcript", "prepare", "--file", str(self.transcript))
        directory = Path(out["dir"])
        self.respond(directory, es.STAGE_TRANSCRIPT,
                     self.extract_item(timestamp_highlights=[{"timestamp": "12:34", "label": "invented"}]))
        code, result = self.run_cli("transcript", "import", "--dir", str(directory))
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "timestamp_not_found")
        self.assertFalse(self.out.exists() and any(self.out.glob("*.md")))
        self.assertEqual(self.indexed, [])

    def test_malformed_items_are_refused(self):
        _, out = self.run_cli("transcript", "prepare", "--file", str(self.transcript))
        directory = Path(out["dir"])
        for bad in (self.extract_item(summary=None), self.extract_item(key_points="not a list"),
                    self.extract_item(id="notes"), None):
            with self.subTest(bad=bad):
                self.respond(directory, es.STAGE_TRANSCRIPT, bad)
                code, result = self.run_cli("transcript", "import", "--dir", str(directory))
                self.assertEqual(code, 1, result)
                self.assertIn(result["error"], ("item_invalid", "unknown_ids", "missing_ids"))

    def test_reimport_is_a_noop_and_edited_transcript_is_stale(self):
        _, out = self.run_cli("transcript", "prepare", "--file", str(self.transcript))
        directory = Path(out["dir"])
        self.respond(directory, es.STAGE_TRANSCRIPT, self.extract_item())
        self.run_cli("transcript", "import", "--dir", str(directory))
        code, result = self.run_cli("transcript", "import", "--dir", str(directory))
        self.assertEqual(result["status"], "already_imported")
        self.assertEqual(len(list(self.out.glob("*.md"))), 1)
        self.transcript.write_text(TRANSCRIPT + "06:00 Addendum\n", encoding="utf-8")
        self.respond(directory, es.STAGE_TRANSCRIPT, self.extract_item(summary="revised"))
        code, result = self.run_cli("transcript", "import", "--dir", str(directory))
        self.assertEqual(result["error"], "stale_input")

    def test_import_needs_dir_and_prepare_needs_file(self):
        code, result = self.run_cli("transcript", "import")
        self.assertEqual(code, 1)
        code, result = self.run_cli("transcript", "prepare")
        self.assertEqual(code, 1)


class TestMeetingExtract(ExtractCase):
    def test_prepare_and_import_with_meeting_row_update(self):
        notes = self.root / "standup.md"
        notes.write_text("Decided: ship Friday. Open: who writes the post?\n", encoding="utf-8")
        code, out = self.run_cli("meeting", "prepare", "--file", str(notes), "--title", "Standup",
                                 "--date", "2026-09-18", "--attendees", "John, Vera", "--meeting-id", "42")
        self.assertEqual(code, 0, out)
        directory = Path(out["dir"])
        self.assertEqual(directory, self.meet / "requests" / "standup")
        self.respond(directory, es.STAGE_MEETING, {
            "id": "extract", "summary": "Ship Friday.", "decisions": ["Ship Friday"], "action_items": [],
            "open_questions": ["Who writes the post?"], "follow_up_topics": [], "tools_mentioned": [],
            "recommended_tags": ["standup"]})
        code, result = self.run_cli("meeting", "import", "--dir", str(directory))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["meeting_id"], 42)
        body = Path(result["path"]).read_text()
        self.assertIn("**Attendees:** John, Vera", body)
        self.assertIn("## Decisions\n\n- Ship Friday", body)
        self.assertEqual(self.indexed[0][1]["category"], "meeting-extract")
        self.assertEqual(self.indexed[1][0], "meeting-row")

    def test_inline_text_has_no_file_input(self):
        code, out = self.run_cli("meeting", "prepare", "--text", "We agreed on nothing.", "--title", "Chat")
        self.assertEqual(code, 0, out)
        request = handoff.load_request(Path(out["request"]))
        self.assertEqual(request.data["inputs"], {})
        self.assertEqual(request.data["payload"]["text"], "We agreed on nothing.")


class TestFetchIsFetchOnly(unittest.TestCase):
    def test_fetch_transcript_no_longer_extracts(self):
        source = (PKA_ROOT / "tools" / "fetch-transcript.py").read_text(encoding="utf-8")
        self.assertNotIn("build_transcript_extract_artifact", source)
        self.assertNotIn("from extract_structured import", source)
        self.assertIn("extract_structured transcript prepare", source)

    def test_no_model_provider_in_extract_tool(self):
        source = (PKA_ROOT / "tools" / "extract_structured.py").read_text(encoding="utf-8")
        for token in ("import llm", "import lmstudio", "llm.chat", "_prompt_json"):
            self.assertNotIn(token, source, token)

    def test_wrapper_uses_the_running_interpreter(self):
        source = (PKA_ROOT / "tools" / "fetch_transcript.py").read_text(encoding="utf-8")
        self.assertNotIn("discord-bridge/venv", source)
        self.assertIn("sys.executable", source)


if __name__ == "__main__":
    unittest.main()
