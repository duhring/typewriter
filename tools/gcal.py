#!/usr/bin/env python3
"""
Google Calendar CLI tool for PKA.
Usage: discord-bridge/venv/bin/python3 tools/gcal.py <command> [options]

Commands:
  auth        One-time OAuth setup (opens browser)
  doctor      Check Calendar auth health
  list        List events (default: today)
  create      Create a new event
  get         Get event details by ID
  update      Update an existing event
  delete      Delete an event
  search      Full-text search across events
  calendars   List all available calendars
  free        Find free time slots

Output: JSON to stdout. Errors to stderr.
"""

import argparse
import json
import sys
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import subprocess
from dateutil import parser as dateparser
from dateutil.tz import tzlocal
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from auth_doctor import check_calendar

PKA_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = PKA_ROOT / "data" / "gcal"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"
CLIENT_SECRET_FILE = CREDENTIALS_DIR / "client_secret.json"
DB_PATH = PKA_ROOT / "data" / "pka.db"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

LOCAL_TZ = tzlocal()

def _get_iana_timezone():
    """Return the IANA timezone name for the local system."""
    try:
        link = subprocess.check_output(["readlink", "/etc/localtime"], stderr=subprocess.DEVNULL).decode().strip()
        if "zoneinfo/" in link:
            return link.split("zoneinfo/")[-1]
    except Exception:
        pass
    return "UTC"

LOCAL_TZ_NAME = _get_iana_timezone()


def get_service():
    """Build and return an authenticated Calendar API service."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                print("Token expired and refresh failed. Run: tools/gcal.py auth", file=sys.stderr)
                sys.exit(1)
        else:
            print("Not authenticated. Run: tools/gcal.py auth", file=sys.stderr)
            sys.exit(1)
        # Save refreshed token
        TOKEN_FILE.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def parse_datetime(dt_str):
    """Parse a datetime string, adding local timezone if naive."""
    dt = dateparser.parse(dt_str)
    if dt is None:
        raise ValueError(f"Could not parse datetime: {dt_str}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def format_event(event, calendar_id=None):
    """Format a Google Calendar event into a clean dict."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id", ""),
        "calendarId": calendar_id or "",
        "title": event.get("summary", "(no title)"),
        "start": start.get("dateTime", start.get("date", "")),
        "end": end.get("dateTime", end.get("date", "")),
        "location": event.get("location", ""),
        "description": event.get("description", ""),
        "attendees": [a.get("email", "") for a in event.get("attendees", [])],
        "status": event.get("status", ""),
        "htmlLink": event.get("htmlLink", ""),
    }


def _connect_db():
    """Open the meeting mirror only on the canonical writer."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    from machine_role import guard_connection, require_writer

    require_writer("calendar meeting sync")
    return guard_connection(
        sqlite3.connect(str(DB_PATH)),
        "calendar meeting sync",
    )


def _ensure_meetings_sync_schema(conn):
    """Add calendar sync columns to meetings if they do not exist yet."""
    has_meetings = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meetings'"
    ).fetchone()
    if not has_meetings:
        raise RuntimeError("meetings table is missing from data/pka.db")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(meetings)").fetchall()}
    if "calendar_event_id" not in columns:
        conn.execute("ALTER TABLE meetings ADD COLUMN calendar_event_id TEXT")
    if "calendar_id" not in columns:
        conn.execute("ALTER TABLE meetings ADD COLUMN calendar_id TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_meetings_calendar_event_id ON meetings(calendar_event_id)"
    )
    conn.commit()


def _meeting_notes_from_event(event):
    """Build meeting notes from the calendar event fields we can store."""
    notes = []
    if event.get("description"):
        notes.append(event["description"].strip())
    if event.get("location"):
        notes.append(f"Location: {event['location']}")
    if event.get("htmlLink"):
        notes.append(f"Calendar: {event['htmlLink']}")
    return "\n\n".join(notes)


def _sync_meeting_record(event):
    """Create or update the mirrored meetings row for a calendar event."""
    conn = _connect_db()
    try:
        _ensure_meetings_sync_schema(conn)
        attendees = ", ".join(event.get("attendees", []))
        notes = _meeting_notes_from_event(event)
        existing = conn.execute(
            "SELECT id FROM meetings WHERE calendar_event_id = ?",
            (event["id"],),
        ).fetchone()

        if existing:
            meeting_id = existing[0]
            conn.execute(
                """
                UPDATE meetings
                SET title = ?, date = ?, attendees = ?, notes = ?, calendar_id = ?
                WHERE id = ?
                """,
                (
                    event["title"],
                    event["start"],
                    attendees,
                    notes,
                    event.get("calendarId", ""),
                    meeting_id,
                ),
            )
            status = "updated"
        else:
            cursor = conn.execute(
                """
                INSERT INTO meetings (title, date, attendees, notes, calendar_event_id, calendar_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event["title"],
                    event["start"],
                    attendees,
                    notes,
                    event["id"],
                    event.get("calendarId", ""),
                ),
            )
            meeting_id = cursor.lastrowid
            status = "created"

        conn.commit()
        return {"status": status, "meeting_id": meeting_id}
    finally:
        conn.close()


def _delete_meeting_record(event_id):
    """Remove the mirrored meetings row for a calendar event if one exists."""
    conn = _connect_db()
    try:
        _ensure_meetings_sync_schema(conn)
        existing = conn.execute(
            "SELECT id FROM meetings WHERE calendar_event_id = ?",
            (event_id,),
        ).fetchone()
        if not existing:
            return {"status": "not_found"}

        conn.execute("DELETE FROM meetings WHERE id = ?", (existing[0],))
        conn.commit()
        return {"status": "deleted", "meeting_id": existing[0]}
    finally:
        conn.close()


def _safe_sync_meeting_record(event):
    """Sync meeting state without failing the calendar operation."""
    try:
        return _sync_meeting_record(event)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _safe_delete_meeting_record(event_id):
    """Delete meeting state without failing the calendar operation."""
    try:
        return _delete_meeting_record(event_id)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _find_event(service, event_id, calendar_id=None):
    """Find an event in the requested calendar or across accessible calendars."""
    cal_ids = [calendar_id] if calendar_id else get_all_calendar_ids(service)
    last_error = None

    for cal_id in cal_ids:
        try:
            event = service.events().get(calendarId=cal_id, eventId=event_id).execute()
            return cal_id, event
        except Exception as exc:
            last_error = exc

    if calendar_id:
        raise RuntimeError(f"Could not find event {event_id} in calendar {calendar_id}") from last_error
    raise RuntimeError(f"Could not find event {event_id} in any accessible calendar") from last_error


# ── Commands ─────────────────────────────────────────────────────────

def cmd_auth(args):
    """Run the one-time OAuth flow."""
    if not CLIENT_SECRET_FILE.exists():
        print(json.dumps({
            "error": f"Missing {CLIENT_SECRET_FILE}",
            "help": "Download OAuth client credentials from Google Cloud Console and save as data/gcal/client_secret.json. See tools/GCAL-SETUP.md for instructions."
        }))
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    print(json.dumps({"status": "authenticated", "message": "Google Calendar connected successfully!"}))


def cmd_doctor(args):
    """Check Calendar auth health and a lightweight API probe."""
    print(json.dumps(check_calendar(), indent=2))


def get_all_calendar_ids(service):
    """Return list of all accessible calendar IDs."""
    result = service.calendarList().list().execute()
    return [cal["id"] for cal in result.get("items", [])]


def cmd_list(args):
    """List calendar events."""
    service = get_service()

    if args.date_from:
        time_min = parse_datetime(args.date_from)
    else:
        time_min = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    if args.date_to:
        time_max = parse_datetime(args.date_to)
        if time_max.hour == 0 and time_max.minute == 0:
            time_max = time_max.replace(hour=23, minute=59, second=59)
    else:
        time_max = time_min + timedelta(days=1)

    cal_ids = [args.calendar] if args.calendar else get_all_calendar_ids(service)

    all_events = []
    seen_ids = set()
    for cal_id in cal_ids:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
            for e in result.get("items", []):
                if e.get("id") not in seen_ids:
                    seen_ids.add(e.get("id"))
                    all_events.append(format_event(e, calendar_id=cal_id))
        except Exception:
            pass  # skip calendars we can't read

    all_events.sort(key=lambda e: e["start"])
    print(json.dumps({"events": all_events, "count": len(all_events)}, indent=2))


def cmd_create(args):
    """Create a calendar event."""
    service = get_service()

    start_dt = parse_datetime(args.start)
    end_dt = parse_datetime(args.end)

    event_body = {
        "summary": args.title,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": LOCAL_TZ_NAME},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": LOCAL_TZ_NAME},
    }
    if args.description:
        event_body["description"] = args.description
    if args.location:
        event_body["location"] = args.location
    if args.attendees:
        event_body["attendees"] = [{"email": e.strip()} for e in args.attendees.split(",")]
    if args.recurrence:
        event_body["recurrence"] = [args.recurrence]

    cal_id = args.calendar or "primary"
    created = service.events().insert(calendarId=cal_id, body=event_body).execute()
    formatted_event = format_event(created, calendar_id=cal_id)
    print(json.dumps({
        "status": "created",
        "event": formatted_event,
        "meeting_sync": _safe_sync_meeting_record(formatted_event),
    }, indent=2))


def cmd_get(args):
    """Get a specific event by ID."""
    service = get_service()
    cal_id, event = _find_event(service, args.event_id, calendar_id=args.calendar)
    print(json.dumps({"event": format_event(event, calendar_id=cal_id)}, indent=2))


def cmd_update(args):
    """Update an existing event."""
    service = get_service()
    cal_id, event = _find_event(service, args.event_id, calendar_id=args.calendar)

    if args.title:
        event["summary"] = args.title
    if args.start:
        dt = parse_datetime(args.start)
        event["start"] = {"dateTime": dt.isoformat(), "timeZone": LOCAL_TZ_NAME}
    if args.end:
        dt = parse_datetime(args.end)
        event["end"] = {"dateTime": dt.isoformat(), "timeZone": LOCAL_TZ_NAME}
    if args.description:
        event["description"] = args.description
    if args.location:
        event["location"] = args.location

    updated = service.events().update(
        calendarId=cal_id, eventId=args.event_id, body=event
    ).execute()
    formatted_event = format_event(updated, calendar_id=cal_id)
    print(json.dumps({
        "status": "updated",
        "event": formatted_event,
        "meeting_sync": _safe_sync_meeting_record(formatted_event),
    }, indent=2))


def cmd_delete(args):
    """Delete an event."""
    service = get_service()
    cal_id, _ = _find_event(service, args.event_id, calendar_id=args.calendar)
    service.events().delete(calendarId=cal_id, eventId=args.event_id).execute()
    print(json.dumps({
        "status": "deleted",
        "event_id": args.event_id,
        "calendarId": cal_id,
        "meeting_sync": _safe_delete_meeting_record(args.event_id),
    }))


def cmd_search(args):
    """Full-text search across calendar events."""
    service = get_service()

    if args.date_from:
        time_min = parse_datetime(args.date_from).isoformat()
    else:
        time_min = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    if args.date_to:
        time_max = parse_datetime(args.date_to)
        if time_max.hour == 0 and time_max.minute == 0:
            time_max = time_max.replace(hour=23, minute=59, second=59)
        time_max = time_max.isoformat()
    else:
        time_max = (datetime.now(LOCAL_TZ) + timedelta(days=90)).isoformat()

    cal_ids = [args.calendar] if args.calendar else get_all_calendar_ids(service)

    all_events = []
    seen_ids = set()
    for cal_id in cal_ids:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                q=args.query,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
            for e in result.get("items", []):
                if e.get("id") not in seen_ids:
                    seen_ids.add(e.get("id"))
                    all_events.append(format_event(e, calendar_id=cal_id))
        except Exception:
            pass  # skip calendars we can't read

    all_events.sort(key=lambda e: e["start"])
    print(json.dumps({"query": args.query, "events": all_events, "count": len(all_events)}, indent=2))


def cmd_calendars(args):
    """List all available calendars."""
    service = get_service()
    result = service.calendarList().list().execute()
    calendars = []
    for cal in result.get("items", []):
        calendars.append({
            "id": cal.get("id", ""),
            "name": cal.get("summary", ""),
            "primary": cal.get("primary", False),
            "accessRole": cal.get("accessRole", ""),
            "timeZone": cal.get("timeZone", ""),
        })
    print(json.dumps({"calendars": calendars, "count": len(calendars)}, indent=2))


def cmd_free(args):
    """Find free time slots in a date range."""
    service = get_service()

    if args.date_from:
        time_min = parse_datetime(args.date_from)
    else:
        time_min = datetime.now(LOCAL_TZ).replace(hour=8, minute=0, second=0, microsecond=0)

    if args.date_to:
        time_max = parse_datetime(args.date_to)
        if time_max.hour == 0 and time_max.minute == 0:
            time_max = time_max.replace(hour=18, minute=0, second=0)
    else:
        time_max = time_min.replace(hour=18, minute=0, second=0)

    # Get events in range across all calendars
    cal_ids = get_all_calendar_ids(service)
    busy = []
    seen_ids = set()
    for cal_id in cal_ids:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            for event in result.get("items", []):
                if event.get("id") in seen_ids:
                    continue
                seen_ids.add(event.get("id"))
                start = event.get("start", {})
                end = event.get("end", {})
                s = start.get("dateTime", start.get("date"))
                e = end.get("dateTime", end.get("date"))
                if s and e:
                    busy.append((dateparser.parse(s), dateparser.parse(e)))
        except Exception:
            pass  # skip calendars we can't read

    # Compute free slots
    free_slots = []
    current = time_min
    for bstart, bend in sorted(busy):
        if current < bstart:
            free_slots.append({
                "start": current.isoformat(),
                "end": bstart.isoformat(),
                "duration_minutes": int((bstart - current).total_seconds() / 60),
            })
        if bend > current:
            current = bend
    if current < time_max:
        free_slots.append({
            "start": current.isoformat(),
            "end": time_max.isoformat(),
            "duration_minutes": int((time_max - current).total_seconds() / 60),
        })

    min_duration = args.duration or 0
    if min_duration:
        free_slots = [s for s in free_slots if s["duration_minutes"] >= min_duration]

    print(json.dumps({
        "range": {"from": time_min.isoformat(), "to": time_max.isoformat()},
        "free_slots": free_slots,
        "count": len(free_slots),
    }, indent=2))


# ── CLI Setup ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Google Calendar CLI for PKA")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # auth
    sub.add_parser("auth", help="One-time OAuth setup")
    sub.add_parser("doctor", help="Check Calendar auth health")

    # list
    p_list = sub.add_parser("list", help="List events")
    p_list.add_argument("--from", dest="date_from", help="Start date (default: today)")
    p_list.add_argument("--to", dest="date_to", help="End date")
    p_list.add_argument("--calendar", help="Calendar ID (default: primary)")

    # create
    p_create = sub.add_parser("create", help="Create an event")
    p_create.add_argument("--title", required=True, help="Event title")
    p_create.add_argument("--start", required=True, help="Start datetime")
    p_create.add_argument("--end", required=True, help="End datetime")
    p_create.add_argument("--description", help="Event description")
    p_create.add_argument("--location", help="Event location")
    p_create.add_argument("--attendees", help="Comma-separated emails")
    p_create.add_argument("--recurrence", help="RRULE string for recurring events (e.g. RRULE:FREQ=WEEKLY;BYDAY=WE)")
    p_create.add_argument("--calendar", help="Calendar ID (default: primary)")

    # get
    p_get = sub.add_parser("get", help="Get event details")
    p_get.add_argument("event_id", help="Event ID")
    p_get.add_argument("--calendar", help="Calendar ID (optional; searches all calendars if omitted)")

    # update
    p_update = sub.add_parser("update", help="Update an event")
    p_update.add_argument("event_id", help="Event ID")
    p_update.add_argument("--title", help="New title")
    p_update.add_argument("--start", help="New start datetime")
    p_update.add_argument("--end", help="New end datetime")
    p_update.add_argument("--description", help="New description")
    p_update.add_argument("--location", help="New location")
    p_update.add_argument("--calendar", help="Calendar ID (optional; searches all calendars if omitted)")

    # delete
    p_del = sub.add_parser("delete", help="Delete an event")
    p_del.add_argument("event_id", help="Event ID")
    p_del.add_argument("--calendar", help="Calendar ID (optional; searches all calendars if omitted)")

    # search
    p_search = sub.add_parser("search", help="Search events")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--from", dest="date_from", help="Start date")
    p_search.add_argument("--to", dest="date_to", help="End date")
    p_search.add_argument("--calendar", help="Calendar ID (default: search all calendars)")

    # calendars
    sub.add_parser("calendars", help="List all calendars")

    # free
    p_free = sub.add_parser("free", help="Find free time slots")
    p_free.add_argument("--from", dest="date_from", help="Start datetime (default: today 8am)")
    p_free.add_argument("--to", dest="date_to", help="End datetime (default: today 6pm)")
    p_free.add_argument("--duration", type=int, help="Minimum slot duration in minutes")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "auth": cmd_auth,
        "doctor": cmd_doctor,
        "list": cmd_list,
        "create": cmd_create,
        "get": cmd_get,
        "update": cmd_update,
        "delete": cmd_delete,
        "search": cmd_search,
        "calendars": cmd_calendars,
        "free": cmd_free,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
