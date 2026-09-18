#!/usr/bin/env python3
"""
Manage governed owner records in the PKA database.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


PKA_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PKA_ROOT / "data" / "pka.db"

RECORD_CLASSES = {
    "pka-operational-artifact",
    "financial-transaction",
    "financial-statement",
    "tax",
    "property",
    "utility",
    "correspondence",
    "contact",
    "meeting",
    "medical",
    "legal",
    "reference",
}

VERIFICATION_STATUSES = {
    "unreviewed",
    "previewed",
    "confirmed",
    "imported",
}

RETENTION_CLASSES = {
    "manual-review",
    "current-year",
    "7-years",
    "permanent",
    "until-superseded",
}

RECORD_STATES = {
    "active",
    "reference",
    "archived",
    "hold",
}

SENSITIVITY_LEVELS = {
    "normal",
    "confidential",
    "restricted",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    # Satellite machines get read access only (docs/devendorf-contract.md)
    from machine_role import guard_connection
    guard_connection(conn, "record registration")
    conn.row_factory = sqlite3.Row
    return conn


def _slugify(text: str, fallback: str = "record") -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:120] or fallback


def _normalize_choice(value: str, allowed: set[str], label: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"Invalid {label}: {value!r}. Allowed: {allowed_text}")
    return normalized


def _normalize_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        return cleaned
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", cleaned):
        return cleaned[:10]
    raise ValueError(f"Expected YYYY-MM-DD for date values, got: {value}")


def _record_key(
    *,
    record_class: str,
    record_series: str,
    event_date: Optional[str],
    source_path: Optional[str],
    title: str,
) -> str:
    parts = [record_class, record_series]
    if event_date:
        parts.append(event_date)
    parts.append(source_path or title)
    return "::".join(_slugify(part, "item") for part in parts)


def ensure_schema(conn: sqlite3.Connection | None = None) -> dict:
    owns_connection = conn is None
    conn = conn or _connect()
    try:
        conn.execute(
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
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_class ON records(record_class)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_series ON records(record_series)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_state ON records(record_state)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_event_date ON records(event_date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_file_id ON records(file_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_kb_id ON records(knowledge_base_id)"
        )
        conn.commit()
        return {"status": "ok", "table": "records"}
    finally:
        if owns_connection:
            conn.close()


def upsert_record(
    *,
    title: str,
    record_class: str,
    record_series: str,
    event_date: Optional[str] = None,
    subject: Optional[str] = None,
    verification_status: str = "unreviewed",
    retention_class: str = "manual-review",
    record_state: str = "active",
    sensitivity: str = "normal",
    file_id: Optional[int] = None,
    knowledge_base_id: Optional[int] = None,
    source_path: Optional[str] = None,
    notes: Optional[str] = None,
    reviewed_at: Optional[str] = None,
    disposition_due_at: Optional[str] = None,
    record_key: Optional[str] = None,
) -> dict:
    normalized_class = _normalize_choice(record_class, RECORD_CLASSES, "record class")
    normalized_verification = _normalize_choice(
        verification_status, VERIFICATION_STATUSES, "verification status"
    )
    normalized_retention = _normalize_choice(
        retention_class, RETENTION_CLASSES, "retention class"
    )
    normalized_state = _normalize_choice(record_state, RECORD_STATES, "record state")
    normalized_sensitivity = _normalize_choice(
        sensitivity, SENSITIVITY_LEVELS, "sensitivity"
    )
    normalized_event_date = _normalize_date(event_date)
    normalized_reviewed_at = _normalize_date(reviewed_at)
    normalized_disposition_due = _normalize_date(disposition_due_at)
    normalized_series = (record_series or "").strip()
    if not normalized_series:
        raise ValueError("record_series is required")

    actual_key = (record_key or "").strip() or _record_key(
        record_class=normalized_class,
        record_series=normalized_series,
        event_date=normalized_event_date,
        source_path=source_path,
        title=title,
    )
    now = datetime.now().isoformat(timespec="seconds")

    with _connect() as conn:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT id FROM records WHERE record_key = ?",
            (actual_key,),
        ).fetchone()
        payload = (
            title,
            normalized_class,
            normalized_series,
            normalized_event_date,
            subject,
            normalized_verification,
            normalized_retention,
            normalized_state,
            normalized_sensitivity,
            file_id,
            knowledge_base_id,
            source_path,
            notes,
            normalized_reviewed_at,
            normalized_disposition_due,
            now,
        )
        if row:
            conn.execute(
                """
                UPDATE records
                SET title = ?, record_class = ?, record_series = ?, event_date = ?, subject = ?,
                    verification_status = ?, retention_class = ?, record_state = ?, sensitivity = ?,
                    file_id = ?, knowledge_base_id = ?, source_path = ?, notes = ?, reviewed_at = ?,
                    disposition_due_at = ?, updated_at = ?
                WHERE id = ?
                """,
                payload + (row[0],),
            )
            return {"id": int(row[0]), "record_key": actual_key, "status": "updated"}

        cursor = conn.execute(
            """
            INSERT INTO records (
                record_key, title, record_class, record_series, event_date, subject,
                verification_status, retention_class, record_state, sensitivity,
                file_id, knowledge_base_id, source_path, notes, reviewed_at,
                disposition_due_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actual_key,
                title,
                normalized_class,
                normalized_series,
                normalized_event_date,
                subject,
                normalized_verification,
                normalized_retention,
                normalized_state,
                normalized_sensitivity,
                file_id,
                knowledge_base_id,
                source_path,
                notes,
                normalized_reviewed_at,
                normalized_disposition_due,
                now,
                now,
            ),
        )
        return {"id": int(cursor.lastrowid), "record_key": actual_key, "status": "created"}


def show_record(*, record_id: Optional[int] = None, record_key: Optional[str] = None) -> dict:
    if not record_id and not record_key:
        raise ValueError("Provide record_id or record_key")
    with _connect() as conn:
        ensure_schema(conn)
        if record_id:
            row = conn.execute(
                "SELECT * FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM records WHERE record_key = ?",
                (record_key,),
            ).fetchone()
    if not row:
        raise FileNotFoundError("Record not found")
    return dict(row)


def list_records(
    *,
    record_class: Optional[str] = None,
    record_series: Optional[str] = None,
    record_state: Optional[str] = None,
    limit: int = 25,
) -> dict:
    where: list[str] = []
    params: list[object] = []
    if record_class:
        where.append("record_class = ?")
        params.append(_normalize_choice(record_class, RECORD_CLASSES, "record class"))
    if record_series:
        where.append("record_series = ?")
        params.append(record_series.strip())
    if record_state:
        where.append("record_state = ?")
        params.append(_normalize_choice(record_state, RECORD_STATES, "record state"))

    query = (
        "SELECT id, record_key, title, record_class, record_series, event_date, "
        "verification_status, retention_class, record_state, sensitivity, source_path "
        "FROM records"
    )
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY COALESCE(event_date, created_at) DESC, id DESC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        ensure_schema(conn)
        rows = conn.execute(query, params).fetchall()

    return {
        "count": len(rows),
        "records": [
            {
                "id": row[0],
                "record_key": row[1],
                "title": row[2],
                "record_class": row[3],
                "record_series": row[4],
                "event_date": row[5],
                "verification_status": row[6],
                "retention_class": row[7],
                "record_state": row[8],
                "sensitivity": row[9],
                "source_path": row[10],
            }
            for row in rows
        ],
    }


def vocab() -> dict:
    return {
        "record_class": sorted(RECORD_CLASSES),
        "verification_status": sorted(VERIFICATION_STATUSES),
        "retention_class": sorted(RETENTION_CLASSES),
        "record_state": sorted(RECORD_STATES),
        "sensitivity": sorted(SENSITIVITY_LEVELS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage governed PKA records")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ensure-schema", help="Create the records table and indexes if missing")
    sub.add_parser("vocab", help="Show allowed controlled vocabulary values")

    p_upsert = sub.add_parser("upsert", help="Create or update a governed record")
    p_upsert.add_argument("--title", required=True, help="Record title")
    p_upsert.add_argument("--record-class", required=True, help="Controlled record class")
    p_upsert.add_argument("--series", required=True, help="Stable record series name")
    p_upsert.add_argument("--event-date", help="Record event date (YYYY-MM-DD)")
    p_upsert.add_argument("--subject", help="Primary person, payee, property, or subject")
    p_upsert.add_argument(
        "--verification-status",
        default="unreviewed",
        help="Controlled verification status",
    )
    p_upsert.add_argument(
        "--retention-class",
        default="manual-review",
        help="Controlled retention class",
    )
    p_upsert.add_argument(
        "--state",
        default="active",
        help="Controlled record state",
    )
    p_upsert.add_argument(
        "--sensitivity",
        default="normal",
        help="Controlled sensitivity level",
    )
    p_upsert.add_argument("--file-id", type=int, help="Optional files.id reference")
    p_upsert.add_argument("--kb-id", type=int, help="Optional knowledge_base.id reference")
    p_upsert.add_argument("--source-path", help="Stable source path for the record")
    p_upsert.add_argument("--notes", help="Optional notes")
    p_upsert.add_argument("--reviewed-at", help="Review date (YYYY-MM-DD)")
    p_upsert.add_argument("--disposition-due-at", help="Disposition review date (YYYY-MM-DD)")
    p_upsert.add_argument("--record-key", help="Optional explicit record key")

    p_show = sub.add_parser("show", help="Show one governed record")
    p_show.add_argument("--id", type=int, help="Record id")
    p_show.add_argument("--record-key", help="Record key")

    p_list = sub.add_parser("list", help="List governed records")
    p_list.add_argument("--record-class", help="Filter by record class")
    p_list.add_argument("--series", help="Filter by record series")
    p_list.add_argument("--state", help="Filter by record state")
    p_list.add_argument("--limit", type=int, default=25, help="Max records to show")

    args = parser.parse_args()
    if args.command == "ensure-schema":
        print(json.dumps(ensure_schema(), indent=2))
        return 0
    if args.command == "vocab":
        print(json.dumps(vocab(), indent=2))
        return 0
    if args.command == "upsert":
        print(
            json.dumps(
                upsert_record(
                    title=args.title,
                    record_class=args.record_class,
                    record_series=args.series,
                    event_date=args.event_date,
                    subject=args.subject,
                    verification_status=args.verification_status,
                    retention_class=args.retention_class,
                    record_state=args.state,
                    sensitivity=args.sensitivity,
                    file_id=args.file_id,
                    knowledge_base_id=args.kb_id,
                    source_path=args.source_path,
                    notes=args.notes,
                    reviewed_at=args.reviewed_at,
                    disposition_due_at=args.disposition_due_at,
                    record_key=args.record_key,
                ),
                indent=2,
            )
        )
        return 0
    if args.command == "show":
        print(
            json.dumps(
                show_record(record_id=args.id, record_key=args.record_key),
                indent=2,
            )
        )
        return 0
    if args.command == "list":
        print(
            json.dumps(
                list_records(
                    record_class=args.record_class,
                    record_series=args.series,
                    record_state=args.state,
                    limit=args.limit,
                ),
                indent=2,
            )
        )
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
