#!/usr/bin/env python3
"""
PKA database bootstrap — create the full local schema for a federated peer.

Under federation (docs/federation.md) each machine owns its own data/pka.db.
This tool creates every table the PKA tools expect, so a fresh peer (or a
fresh clone) can run all DB-backed tools immediately.

The schema is reconstructed from the column usage in the tool code (INSERT /
UPDATE / SELECT statements in pka_index.py, journal.py, records.py,
gcal.py, task_record.py, memory_retrieval.py, wiki_compile.py). The four
tables that already carry CREATE TABLE IF NOT EXISTS in their owning tools
(glossary, entry_contacts, records, memory_chunks) are also created here so
the health check passes without first running each tool individually.

Usage:
    python3 tools/pka_db.py init          # create schema (idempotent)
    python3 tools/pka_db.py tables        # list tables + row counts
    python3 tools/pka_db.py health        # required-tables check vs pka_health

Idempotent: safe to re-run. Never drops or alters existing tables.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"

# Tables pka_health.py requires (see REQUIRED_DB_TABLES). Keeping this in sync
# is the definition of "the health check passes."
REQUIRED_TABLES = {
    "contacts", "files", "journal_entries", "knowledge_base",
    "meetings", "projects",
}

# Full schema. CREATE TABLE IF NOT EXISTS everywhere so re-runs are safe and
# existing data is never touched.
SCHEMA_SQL = [
    # --- The six required tables (reconstructed from tool column usage) ---
    """
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        company TEXT,
        role TEXT,
        notes TEXT,
        summary TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        filepath TEXT NOT NULL UNIQUE,
        category TEXT,
        description TEXT,
        file_type TEXT,
        ocr_text TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        content TEXT,
        summary TEXT,
        mood TEXT,
        energy_score TEXT,
        tags TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT,
        category TEXT,
        tags TEXT,
        source_file TEXT,
        source_url TEXT,
        confidence TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meetings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        date TEXT,
        attendees TEXT,
        notes TEXT,
        action_items TEXT,
        calendar_event_id TEXT,
        calendar_id TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        status TEXT,
        priority TEXT,
        start_date TEXT,
        due_date TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- kb_links (wiki_compile.py: source_id/target_id -> knowledge_base) ---
    """
    CREATE TABLE IF NOT EXISTS kb_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        relationship TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (source_id) REFERENCES knowledge_base(id),
        FOREIGN KEY (target_id) REFERENCES knowledge_base(id)
    )
    """,
    # --- glossary + entry_contacts (owned by journal.py; created here too) ---
    """
    CREATE TABLE IF NOT EXISTS glossary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        term TEXT NOT NULL,
        correction TEXT NOT NULL,
        context TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entry_contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL,
        contact_id INTEGER NOT NULL,
        FOREIGN KEY (entry_id) REFERENCES journal_entries(id),
        FOREIGN KEY (contact_id) REFERENCES contacts(id)
    )
    """,
    # --- records (owned by records.py; created here too) ---
    """
    CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        record_class TEXT NOT NULL,
        record_series TEXT NOT NULL,
        event_date TEXT,
        subject TEXT,
        verification_status TEXT NOT NULL DEFAULT 'unreviewed',
        retention_class TEXT NOT NULL DEFAULT 'manual-review',
        record_state TEXT NOT NULL DEFAULT 'active',
        sensitivity TEXT NOT NULL DEFAULT 'normal',
        file_id INTEGER,
        knowledge_base_id INTEGER,
        source_path TEXT,
        notes TEXT,
        reviewed_at TEXT,
        disposition_due_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (file_id) REFERENCES files(id),
        FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base(id)
    )
    """,
    # --- memory_chunks (owned by memory_retrieval.py; created here too) ---
    """
    CREATE TABLE IF NOT EXISTS memory_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_kind TEXT NOT NULL,
        source_group TEXT NOT NULL,
        source_key TEXT NOT NULL UNIQUE,
        chunk_index INTEGER NOT NULL DEFAULT 0,
        title TEXT,
        text_content TEXT NOT NULL,
        source_path TEXT,
        metadata_json TEXT,
        content_hash TEXT NOT NULL,
        embedding_json TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # --- Indexes (match those created by owning tools) ---
    "CREATE INDEX IF NOT EXISTS idx_files_filepath ON files(filepath)",
    "CREATE INDEX IF NOT EXISTS idx_kb_source_file ON knowledge_base(source_file)",
    "CREATE INDEX IF NOT EXISTS idx_kb_category ON knowledge_base(category)",
    "CREATE INDEX IF NOT EXISTS idx_meetings_cal_event ON meetings(calendar_event_id)",
    "CREATE INDEX IF NOT EXISTS idx_records_class ON records(record_class)",
    "CREATE INDEX IF NOT EXISTS idx_records_series ON records(record_series)",
    "CREATE INDEX IF NOT EXISTS idx_records_state ON records(record_state)",
    "CREATE INDEX IF NOT EXISTS idx_records_event_date ON records(event_date)",
    "CREATE INDEX IF NOT EXISTS idx_records_file_id ON records(file_id)",
    "CREATE INDEX IF NOT EXISTS idx_records_kb_id ON records(knowledge_base_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chunks_group ON memory_chunks(source_group)",
    "CREATE INDEX IF NOT EXISTS idx_memory_chunks_kind ON memory_chunks(source_kind)",
    "CREATE INDEX IF NOT EXISTS idx_kb_links_source ON kb_links(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_kb_links_target ON kb_links(target_id)",
]


def init_schema(db_path: Path = DB_PATH) -> dict:
    """Create all tables and indexes. Idempotent. Returns a summary dict."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        before = _table_set(conn)
        for stmt in SCHEMA_SQL:
            conn.execute(stmt)
        conn.commit()
        after = _table_set(conn)
        created = sorted(after - before)
        return {
            "db_path": str(db_path),
            "tables_before": sorted(before),
            "tables_after": sorted(after),
            "newly_created": created,
            "table_count": len(after),
        }
    finally:
        conn.close()


def _table_set(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def list_tables(db_path: Path = DB_PATH) -> int:
    if not db_path.exists():
        print(f"{db_path} does not exist. Run: python3 tools/pka_db.py init", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    try:
        tables = sorted(_table_set(conn))
        print(f"Tables in {db_path} ({len(tables)}):")
        for t in tables:
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:24s} {n:>6d} rows")
        missing = sorted(REQUIRED_TABLES - set(tables))
        if missing:
            print(f"\nMissing required tables: {missing}", file=sys.stderr)
            return 1
        print("\nAll required tables present.")
    finally:
        conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PKA database bootstrap")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the full schema (idempotent)")
    sub.add_parser("tables", help="list tables and row counts")
    args = parser.parse_args()

    if args.cmd == "init":
        result = init_schema()
        print(f"Schema ready at {result['db_path']}")
        print(f"  tables: {result['table_count']}")
        if result["newly_created"]:
            print(f"  newly created: {result['newly_created']}")
        else:
            print("  (all tables already existed — no changes)")
        missing = sorted(REQUIRED_TABLES - set(result["tables_after"]))
        if missing:
            print(f"  WARNING: still missing required tables: {missing}", file=sys.stderr)
            return 1
        print("  all required tables present.")
        return 0
    if args.cmd == "tables":
        return list_tables()
    return 1


if __name__ == "__main__":
    sys.exit(main())
