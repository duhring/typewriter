#!/usr/bin/env python3
"""
Google auth doctor for PKA.

Checks Calendar, Docs/Drive, and Sheets auth state, refreshes tokens when
possible, and probes a lightweight API call so failures show up before a
workflow breaks.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


PKA_ROOT = Path(__file__).resolve().parent.parent
GCAL_TOKEN = PKA_ROOT / "data" / "gcal" / "token.json"
GDOCS_TOKEN = PKA_ROOT / "data" / "gdocs" / "token.json"
GSHEETS_TOKEN = PKA_ROOT / "data" / "gsheets" / "token.json"

GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GDOCS_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]
GSHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _load_creds(token_file: Path, scopes: list[str]) -> Credentials:
    return Credentials.from_authorized_user_file(str(token_file), scopes)


def _probe_calendar(creds: Credentials) -> dict:
    service = build("calendar", "v3", credentials=creds)
    result = service.calendarList().list(maxResults=1).execute()
    return {
        "probe": "calendarList.list",
        "count": len(result.get("items", [])),
    }


def _probe_docs(creds: Credentials) -> dict:
    service = build("drive", "v3", credentials=creds)
    result = service.files().list(pageSize=1, fields="files(id,name)").execute()
    return {
        "probe": "drive.files.list",
        "count": len(result.get("files", [])),
    }


def _probe_sheets(creds: Credentials) -> dict:
    service = build("drive", "v3", credentials=creds)
    result = service.files().list(
        q="mimeType='application/vnd.google-apps.spreadsheet'",
        pageSize=1,
        fields="files(id,name)",
    ).execute()
    return {
        "probe": "drive.files.list(spreadsheets)",
        "count": len(result.get("files", [])),
    }


def check_service(
    *,
    name: str,
    token_file: Path,
    scopes: list[str],
    auth_command: str,
    probe_fn,
) -> dict:
    result = {
        "service": name,
        "token_file": str(token_file),
        "status": "ok",
        "auth_command": auth_command,
    }

    if not token_file.exists():
        result["status"] = "needs_auth"
        result["error"] = f"Token file is missing: {token_file}"
        return result

    try:
        creds = _load_creds(token_file, scopes)
    except Exception as exc:
        result["status"] = "broken_token"
        result["error"] = f"Could not load token: {exc}"
        return result

    result["expired"] = bool(creds.expired)
    result["has_refresh_token"] = bool(creds.refresh_token)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_file.write_text(creds.to_json(), encoding="utf-8")
                result["refreshed"] = True
            except Exception as exc:
                result["status"] = "refresh_failed"
                result["error"] = str(exc)
                return result
        else:
            result["status"] = "needs_auth"
            result["error"] = "Credentials are invalid and cannot be refreshed."
            return result

    try:
        result["probe_result"] = probe_fn(creds)
    except Exception as exc:
        result["status"] = "probe_failed"
        result["error"] = str(exc)
        return result

    return result


def check_calendar() -> dict:
    return check_service(
        name="calendar",
        token_file=GCAL_TOKEN,
        scopes=GCAL_SCOPES,
        auth_command="discord-bridge/venv/bin/python3 tools/gcal.py auth",
        probe_fn=_probe_calendar,
    )


def check_docs() -> dict:
    return check_service(
        name="docs",
        token_file=GDOCS_TOKEN,
        scopes=GDOCS_SCOPES,
        auth_command="discord-bridge/venv/bin/python3 tools/gdocs.py auth",
        probe_fn=_probe_docs,
    )


def check_sheets() -> dict:
    return check_service(
        name="sheets",
        token_file=GSHEETS_TOKEN,
        scopes=GSHEETS_SCOPES,
        auth_command="discord-bridge/venv/bin/python3 tools/gsheets.py auth",
        probe_fn=_probe_sheets,
    )


def run_all() -> dict:
    checks = [check_calendar(), check_docs(), check_sheets()]
    unhealthy = [check["service"] for check in checks if check["status"] != "ok"]
    return {
        "status": "ok" if not unhealthy else "attention_needed",
        "checks": checks,
        "failing_services": unhealthy,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Google auth health for PKA")
    parser.add_argument(
        "service",
        nargs="?",
        choices=["all", "calendar", "docs", "sheets"],
        default="all",
        help="Service to check (default: all)",
    )
    args = parser.parse_args()

    if args.service == "calendar":
        payload = check_calendar()
    elif args.service == "docs":
        payload = check_docs()
    elif args.service == "sheets":
        payload = check_sheets()
    else:
        payload = run_all()

    print(json.dumps(payload, indent=2))
    return 0 if payload.get("status") in {"ok", "attention_needed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
