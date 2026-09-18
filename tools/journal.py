#!/usr/bin/env python3
"""
Journal CLI tool for PKA.
Usage: discord-bridge/venv/bin/python3 tools/journal.py add [options]

Commands:
  add         Create a journal entry, link contacts, and update glossary

Output: JSON to stdout. Errors to stderr.
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"


def _connect_db():
    """Connect to the PKA SQLite database."""
    if not DB_PATH.exists():
        print(json.dumps({"error": f"Database not found: {DB_PATH}"}), file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    # Satellite machines get read access only (docs/devendorf-contract.md)
    from machine_role import guard_connection
    guard_connection(conn, "journaling")
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _column_names(conn, table_name):
    if not _table_exists(conn, table_name):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _ensure_schema(conn):
    """Ensure the journal-related tables/columns exist."""
    journal_cols = _column_names(conn, "journal_entries")
    if not journal_cols:
        raise RuntimeError("journal_entries table is missing from data/pka.db")
    if "summary" not in journal_cols:
        conn.execute("ALTER TABLE journal_entries ADD COLUMN summary TEXT")
    if "energy_score" not in journal_cols:
        conn.execute("ALTER TABLE journal_entries ADD COLUMN energy_score INTEGER")

    contact_cols = _column_names(conn, "contacts")
    if not contact_cols:
        raise RuntimeError("contacts table is missing from data/pka.db")
    if "summary" not in contact_cols:
        conn.execute("ALTER TABLE contacts ADD COLUMN summary TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS glossary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            correction TEXT NOT NULL,
            context TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id INTEGER NOT NULL,
            contact_id INTEGER NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES journal_entries(id),
            FOREIGN KEY (contact_id) REFERENCES contacts(id)
        )
        """
    )
    conn.commit()


def _strip_frontmatter(text):
    """Strip the simple YAML frontmatter used by saved Discord notes."""
    if not text.startswith("---\n"):
        return text.strip()

    end = text.find("\n---\n", 4)
    if end == -1:
        return text.strip()
    return text[end + len("\n---\n"):].strip()


def _read_content(args):
    if args.content_file:
        return _strip_frontmatter(Path(args.content_file).read_text(encoding="utf-8"))
    return args.content


def _parse_triples(specs, label):
    items = []
    for spec in specs:
        parts = spec.split("|", 2)
        while len(parts) < 3:
            parts.append("")
        first = parts[0].strip()
        if not first:
            raise ValueError(f"{label} entries must start with a name/term")
        items.append(tuple(part.strip() for part in parts))
    return items


def _load_glossary(conn):
    return conn.execute(
        "SELECT term, correction FROM glossary ORDER BY length(term) DESC, term ASC"
    ).fetchall()


def _glossary_pattern(term):
    if re.fullmatch(r"[\w-]+", term):
        return re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    return re.compile(re.escape(term), re.IGNORECASE)


def _apply_glossary(text, glossary_rows):
    corrected = text
    replacements = []
    for row in glossary_rows:
        term = row["term"]
        correction = row["correction"]
        if not term or not correction or term.lower() == correction.lower():
            continue
        pattern = _glossary_pattern(term)
        corrected, count = pattern.subn(correction, corrected)
        if count:
            replacements.append(
                {"term": term, "correction": correction, "count": count}
            )
    return corrected, replacements


def _normalize_tags(tags):
    parts = [part.strip() for part in tags.split(",")]
    return ",".join(part for part in parts if part)


def _upsert_contact(conn, name, company="", summary=""):
    row = conn.execute(
        "SELECT * FROM contacts WHERE lower(name) = lower(?) ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    updates = []
    values = []

    if row:
        if company and (row["company"] or "") != company:
            updates.append("company = ?")
            values.append(company)
        if summary and (row["summary"] or "") != summary:
            updates.append("summary = ?")
            values.append(summary)
        if updates:
            if "updated_at" in row.keys():
                updates.append("updated_at = ?")
                values.append(now)
            values.append(row["id"])
            conn.execute(
                f"UPDATE contacts SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return {"id": row["id"], "name": row["name"], "status": "updated"}
        return {"id": row["id"], "name": row["name"], "status": "linked"}

    cursor = conn.execute(
        """
        INSERT INTO contacts (name, company, summary, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, company or None, summary or None, now),
    )
    return {"id": cursor.lastrowid, "name": name, "status": "created"}


def _link_contact(conn, entry_id, contact_id):
    existing = conn.execute(
        "SELECT id FROM entry_contacts WHERE entry_id = ? AND contact_id = ?",
        (entry_id, contact_id),
    ).fetchone()
    if existing:
        return {"status": "already_linked", "link_id": existing["id"]}

    cursor = conn.execute(
        "INSERT INTO entry_contacts (entry_id, contact_id) VALUES (?, ?)",
        (entry_id, contact_id),
    )
    return {"status": "linked", "link_id": cursor.lastrowid}


def _upsert_glossary(conn, term, correction, context=""):
    row = conn.execute(
        """
        SELECT *
        FROM glossary
        WHERE lower(term) = lower(?) OR lower(correction) = lower(?)
        ORDER BY id
        LIMIT 1
        """,
        (term, correction),
    ).fetchone()

    if row:
        updates = []
        values = []
        if row["term"] != term:
            updates.append("term = ?")
            values.append(term)
        if row["correction"] != correction:
            updates.append("correction = ?")
            values.append(correction)
        if context and (row["context"] or "") != context:
            updates.append("context = ?")
            values.append(context)
        if updates:
            values.append(row["id"])
            conn.execute(
                f"UPDATE glossary SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return {"id": row["id"], "term": term, "status": "updated"}
        return {"id": row["id"], "term": row["term"], "status": "exists"}

    cursor = conn.execute(
        "INSERT INTO glossary (term, correction, context) VALUES (?, ?, ?)",
        (term, correction, context or None),
    )
    return {"id": cursor.lastrowid, "term": term, "status": "created"}


def cmd_add(args):
    """Create a journal entry and update linked tables."""
    if not 1 <= args.energy <= 10:
        raise ValueError("--energy must be between 1 and 10")

    raw_content = _read_content(args)
    created_at = args.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    contact_specs = _parse_triples(args.contact, "contact")
    glossary_specs = _parse_triples(args.glossary, "glossary")

    conn = _connect_db()
    try:
        _ensure_schema(conn)
        glossary_rows = _load_glossary(conn)

        title, title_replacements = _apply_glossary(args.title, glossary_rows)
        content, content_replacements = _apply_glossary(raw_content, glossary_rows)
        summary, summary_replacements = _apply_glossary(args.summary, glossary_rows)
        tags = _normalize_tags(args.tags)

        payload = {
            "title": title,
            "content": content,
            "summary": summary,
            "mood": args.mood,
            "energy_score": args.energy,
            "tags": tags,
            "created_at": created_at,
            "applied_glossary": title_replacements + content_replacements + summary_replacements,
            "contacts": [
                {"name": name, "company": company, "summary": summary_text}
                for name, company, summary_text in contact_specs
            ],
            "glossary_updates": [
                {"term": term, "correction": correction, "context": context}
                for term, correction, context in glossary_specs
            ],
        }

        if args.dry_run:
            print(json.dumps({"dry_run": True, "entry": payload}, indent=2))
            return

        cursor = conn.execute(
            """
            INSERT INTO journal_entries (title, content, summary, mood, energy_score, tags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                content,
                summary,
                args.mood,
                args.energy,
                tags,
                created_at,
            ),
        )
        entry_id = cursor.lastrowid

        contact_results = []
        for name, company, summary_text in contact_specs:
            contact_result = _upsert_contact(conn, name, company, summary_text)
            link_result = _link_contact(conn, entry_id, contact_result["id"])
            contact_results.append(
                {
                    "id": contact_result["id"],
                    "name": contact_result["name"],
                    "contact_status": contact_result["status"],
                    "link_status": link_result["status"],
                }
            )

        glossary_results = []
        for term, correction, context in glossary_specs:
            glossary_results.append(_upsert_glossary(conn, term, correction, context))

        conn.commit()

        print(
            json.dumps(
                {
                    "status": "created",
                    "entry_id": entry_id,
                    "title": title,
                    "created_at": created_at,
                    "contacts": contact_results,
                    "glossary": glossary_results,
                    "applied_glossary": payload["applied_glossary"],
                },
                indent=2,
            )
        )
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Journal CLI for PKA")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    add_parser = sub.add_parser("add", help="Create a journal entry")
    add_parser.add_argument("--title", required=True, help="Entry title")
    add_parser.add_argument("--summary", required=True, help="Entry summary")
    content_group = add_parser.add_mutually_exclusive_group(required=True)
    content_group.add_argument("--content", help="Entry body text")
    content_group.add_argument(
        "--content-file",
        help="Path to a text or markdown file containing the entry body",
    )
    add_parser.add_argument("--mood", required=True, help="Single-word mood")
    add_parser.add_argument(
        "--energy",
        type=int,
        required=True,
        help="Energy score from 1-10",
    )
    add_parser.add_argument(
        "--tags",
        required=True,
        help="Comma-separated tags",
    )
    add_parser.add_argument(
        "--created-at",
        help="Timestamp or date to store in created_at (default: now)",
    )
    add_parser.add_argument(
        "--contact",
        action="append",
        default=[],
        help="Contact spec in the form Name|Company|Summary",
    )
    add_parser.add_argument(
        "--glossary",
        action="append",
        default=[],
        help="Glossary spec in the form Term|Correction|Context",
    )
    add_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the resolved entry without writing to the database",
    )
    add_parser.set_defaults(func=cmd_add)

    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
