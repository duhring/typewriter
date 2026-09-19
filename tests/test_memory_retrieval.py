"""Keyword memory retrieval (tools/memory_retrieval.py): FTS5 index, no model.

Offline, temp database, chunk collection stubbed. Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

import memory_retrieval as mr  # noqa: E402


def chunk(group, title, text, kind="wiki", metadata=None):
    return mr._make_chunks(source_kind=kind, source_group=group, title=title, text_content=text,
                           source_path=f"{group}.md", metadata=metadata or {"date": "2026-09-01"})[0]


CHUNKS = [
    chunk("laptop", "The laptop test", "The laptop contribution proved the workflow travels. Coffee shop, interview, deck, publish."),
    chunk("compost", "Composting", "Composting food scraps builds soil and feeds the tomatoes in the garden."),
    chunk("coders", "The coder objection", "Critics say only coders benefit from the workflow. Participation grew anyway."),
]


class MemoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "pka.db"
        self.chunks = list(CHUNKS)
        for name, value in [("DB_PATH", self.db), ("collect_chunks", lambda conn: list(self.chunks))]:
            m = patch.object(mr, name, value); m.start(); self.addCleanup(m.stop)
        import machine_role
        m = patch.object(machine_role, "guard_connection", lambda conn, op: conn); m.start(); self.addCleanup(m.stop)

    def sync(self):
        conn = sqlite3.connect(str(self.db))
        try:
            return mr.sync_memory(conn)
        finally:
            conn.close()

    def readonly(self):
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        self.addCleanup(conn.close)
        return conn


class TestSync(MemoryCase):
    def test_sync_builds_the_full_text_index_without_a_model(self):
        result = self.sync()
        self.assertEqual((result["total_chunks"], result["updated_chunks"]), (3, 3))
        conn = self.readonly()
        self.assertTrue(mr._fts_exists(conn))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_chunks_fts").fetchone()[0], 3)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_chunks WHERE embedding_json IS NOT NULL").fetchone()[0], 0)

    def test_resync_is_incremental_and_removes_stale_chunks(self):
        self.sync()
        self.assertEqual(self.sync()["unchanged_chunks"], 3)
        self.chunks[1] = chunk("compost", "Composting", "Composting was replaced by mulching this year.")
        del self.chunks[2]
        result = self.sync()
        self.assertEqual((result["updated_chunks"], result["unchanged_chunks"], result["deleted_chunks"]), (1, 1, 1))
        conn = self.readonly()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_chunks_fts").fetchone()[0], 2)
        hits = mr.recall(conn, "mulching", limit=3)
        self.assertEqual([h["source_group"] for h in hits], ["compost"])
        self.assertEqual(mr.recall(conn, "coders", limit=3), [])

    def test_existing_database_without_the_index_is_rebuilt_on_first_sync(self):
        conn = sqlite3.connect(str(self.db))
        conn.execute("""CREATE TABLE memory_chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, source_kind TEXT NOT NULL,
            source_group TEXT NOT NULL, source_key TEXT NOT NULL UNIQUE, chunk_index INTEGER NOT NULL DEFAULT 0, title TEXT,
            text_content TEXT NOT NULL, source_path TEXT, metadata_json TEXT, content_hash TEXT NOT NULL,
            embedding_json TEXT, updated_at TEXT DEFAULT (datetime('now')))""")
        c = self.chunks[0]
        conn.execute("INSERT INTO memory_chunks (source_kind, source_group, source_key, chunk_index, title, text_content, source_path, metadata_json, content_hash, embedding_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (c.source_kind, c.source_group, c.source_key, 0, c.title, c.text_content, c.source_path, c.metadata_json, c.content_hash, "[0.1, 0.2]"))
        conn.commit(); conn.close()
        result = self.sync()
        self.assertEqual(result["unchanged_chunks"], 1, "a row with a matching hash is kept, embedding or not")
        conn = self.readonly()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_chunks_fts").fetchone()[0], 3)
        self.assertEqual(mr.recall(conn, "laptop travels", limit=1)[0]["source_group"], "laptop")


class TestRecall(MemoryCase):
    def test_recall_ranks_by_keyword_relevance_and_is_read_only(self):
        self.sync()
        conn = self.readonly()
        hits = mr.recall(conn, "does the workflow travel on a laptop?", limit=2)
        self.assertEqual(hits[0]["source_group"], "laptop")
        self.assertGreater(hits[0]["score"], hits[1]["score"])
        self.assertNotIn("compost", [h["source_group"] for h in hits])

    def test_stemming_matches_word_forms(self):
        self.sync()
        hits = mr.recall(self.readonly(), "tomato composted", limit=1)
        self.assertEqual(hits[0]["source_group"], "compost")

    def test_stopword_only_query_falls_back_to_overlap_scan(self):
        self.sync()
        self.assertEqual(mr._fts_query("the and of"), "")
        self.assertEqual(mr.recall(self.readonly(), "the and of", limit=2), [])

    def test_recall_before_any_sync_is_empty_not_an_error(self):
        conn = sqlite3.connect(str(self.db))
        conn.execute("CREATE TABLE memory_chunks (id INTEGER PRIMARY KEY, source_kind TEXT, source_group TEXT, source_key TEXT, title TEXT, text_content TEXT, source_path TEXT, metadata_json TEXT, content_hash TEXT, updated_at TEXT)")
        conn.commit(); conn.close()
        self.assertEqual(mr.recall(self.readonly(), "laptop", limit=2), [])


class TestCLI(MemoryCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = mr.main(list(argv))
        return code, out.getvalue()

    def test_sync_status_recall(self):
        code, out = self.run_cli("sync")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["total_chunks"], 3)
        code, out = self.run_cli("status")
        self.assertEqual(json.loads(out)["retrieval"], "keyword (FTS5, BM25)")
        self.assertTrue(json.loads(out)["fts_index"])
        code, out = self.run_cli("recall", "--query", "laptop workflow", "--format", "json")
        self.assertEqual(json.loads(out)["hits"][0]["source_group"], "laptop")
        code, out = self.run_cli("recall", "--query", "laptop workflow")
        self.assertIn("laptop", out.lower())

    def test_no_provider_in_module(self):
        source = (PKA_ROOT / "tools" / "memory_retrieval.py").read_text(encoding="utf-8")
        for token in ("import lmstudio", "embed_texts", "LMStudioError", "cosine"):
            self.assertNotIn(token, source, token)


if __name__ == "__main__":
    unittest.main()
