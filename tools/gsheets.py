#!/usr/bin/env python3
"""
Google Sheets CLI tool for PKA.
Usage: discord-bridge/venv/bin/python3 tools/gsheets.py <command> [options]

Commands:
  auth        One-time OAuth setup (opens browser)
  doctor      Check Sheets auth health
  create      Create a new spreadsheet
  read        Read data from a sheet
  write       Write data to a sheet
  append      Append rows to a sheet
  list        List spreadsheets created by this app
  get         Get spreadsheet metadata
  search      Search spreadsheets in Drive

Output: JSON to stdout. Errors to stderr.
"""

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from auth_doctor import check_sheets

PKA_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = PKA_ROOT / "data" / "gsheets"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"
CLIENT_SECRET_FILE = PKA_ROOT / "data" / "gcal" / "client_secret.json"
PKA_FOLDER_FILE = PKA_ROOT / "data" / "gdocs" / "pka_folder_id.txt"


def get_pka_folder_id():
    """Return the saved PKA Drive folder ID, or None."""
    if PKA_FOLDER_FILE.exists():
        return PKA_FOLDER_FILE.read_text().strip() or None
    return None
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _get_creds():
    """Load or refresh credentials."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                print("Token expired and refresh failed. Run: tools/gsheets.py auth", file=sys.stderr)
                sys.exit(1)
            TOKEN_FILE.write_text(creds.to_json())
        else:
            print("Not authenticated. Run: tools/gsheets.py auth", file=sys.stderr)
            sys.exit(1)
    return creds


def get_sheets_service():
    return build("sheets", "v4", credentials=_get_creds())


def get_drive_service():
    return build("drive", "v3", credentials=_get_creds())


# ── Commands ─────────────────────────────────────────────────────────

def cmd_auth(args):
    """Run the one-time OAuth flow."""
    if not CLIENT_SECRET_FILE.exists():
        print(json.dumps({
            "error": f"Missing {CLIENT_SECRET_FILE}",
            "help": "Reuses the same client_secret.json from data/gcal/."
        }))
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    print(json.dumps({"status": "authenticated", "message": "Google Sheets connected successfully!"}))


def cmd_doctor(args):
    """Check Sheets auth health and a lightweight API probe."""
    print(json.dumps(check_sheets(), indent=2))


def cmd_create(args):
    """Create a new spreadsheet."""
    sheets = get_sheets_service()

    body = {"properties": {"title": args.title}}

    # Add sheet names if specified
    if args.sheets:
        sheet_names = [s.strip() for s in args.sheets.split(",")]
        body["sheets"] = [{"properties": {"title": name}} for name in sheet_names]

    result = sheets.spreadsheets().create(body=body, fields="spreadsheetId,spreadsheetUrl,sheets").execute()

    spreadsheet_id = result["spreadsheetId"]
    sheet_list = [s["properties"]["title"] for s in result.get("sheets", [])]

    # If initial data from CSV file
    if args.file:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = PKA_ROOT / file_path
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            reader = csv.reader(io.StringIO(content))
            rows = list(reader)
            if rows:
                sheet_range = f"{sheet_list[0]}!A1"
                sheets.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=sheet_range,
                    valueInputOption="USER_ENTERED",
                    body={"values": rows}
                ).execute()

    # Move into PKA folder if one is configured (or explicitly specified)
    folder_id = getattr(args, "folder_id", None) or get_pka_folder_id()
    if folder_id:
        drive = get_drive_service()
        file_meta = drive.files().get(fileId=spreadsheet_id, fields="parents").execute()
        current_parents = ",".join(file_meta.get("parents", []))
        drive.files().update(
            fileId=spreadsheet_id,
            addParents=folder_id,
            removeParents=current_parents,
            fields="id",
        ).execute()

    print(json.dumps({
        "status": "created",
        "spreadsheet_id": spreadsheet_id,
        "url": result["spreadsheetUrl"],
        "title": args.title,
        "sheets": sheet_list,
        **({"folder_id": folder_id} if folder_id else {}),
    }, indent=2))


def cmd_read(args):
    """Read data from a sheet."""
    sheets = get_sheets_service()

    range_str = args.range or "Sheet1"
    result = sheets.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id,
        range=range_str,
    ).execute()

    values = result.get("values", [])

    print(json.dumps({
        "spreadsheet_id": args.spreadsheet_id,
        "range": range_str,
        "rows": len(values),
        "data": values,
    }, indent=2))


def cmd_write(args):
    """Write data to a sheet."""
    sheets = get_sheets_service()

    # Parse data from JSON string or file
    if args.file:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = PKA_ROOT / file_path
        content = file_path.read_text(encoding="utf-8")
        if file_path.suffix == ".csv":
            reader = csv.reader(io.StringIO(content))
            values = list(reader)
        else:
            values = json.loads(content)
    elif args.data:
        values = json.loads(args.data)
    else:
        values = json.loads(sys.stdin.read())

    range_str = args.range or "Sheet1!A1"
    result = sheets.spreadsheets().values().update(
        spreadsheetId=args.spreadsheet_id,
        range=range_str,
        valueInputOption="USER_ENTERED",
        body={"values": values}
    ).execute()

    print(json.dumps({
        "status": "written",
        "spreadsheet_id": args.spreadsheet_id,
        "range": result.get("updatedRange", range_str),
        "rows_updated": result.get("updatedRows", 0),
        "cells_updated": result.get("updatedCells", 0),
    }, indent=2))


def cmd_append(args):
    """Append rows to a sheet."""
    sheets = get_sheets_service()

    if args.data:
        values = json.loads(args.data)
    elif args.row:
        values = [args.row.split(",")]
    else:
        values = json.loads(sys.stdin.read())

    range_str = args.range or "Sheet1"
    result = sheets.spreadsheets().values().append(
        spreadsheetId=args.spreadsheet_id,
        range=range_str,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": values}
    ).execute()

    updates = result.get("updates", {})
    print(json.dumps({
        "status": "appended",
        "spreadsheet_id": args.spreadsheet_id,
        "range": updates.get("updatedRange", range_str),
        "rows_appended": updates.get("updatedRows", 0),
    }, indent=2))


def cmd_list(args):
    """List spreadsheets."""
    drive = get_drive_service()

    query = "mimeType='application/vnd.google-apps.spreadsheet'"
    result = drive.files().list(
        q=query,
        fields="files(id,name,createdTime,modifiedTime,webViewLink)",
        orderBy="modifiedTime desc",
        pageSize=args.limit or 20,
    ).execute()

    docs = []
    for f in result.get("files", []):
        docs.append({
            "id": f["id"],
            "title": f["name"],
            "created": f.get("createdTime", ""),
            "modified": f.get("modifiedTime", ""),
            "url": f.get("webViewLink", ""),
        })

    print(json.dumps({"spreadsheets": docs, "count": len(docs)}, indent=2))


def cmd_get(args):
    """Get spreadsheet metadata."""
    sheets = get_sheets_service()

    result = sheets.spreadsheets().get(spreadsheetId=args.spreadsheet_id).execute()

    sheet_list = []
    for s in result.get("sheets", []):
        props = s.get("properties", {})
        grid = props.get("gridProperties", {})
        sheet_list.append({
            "title": props.get("title", ""),
            "index": props.get("index", 0),
            "rows": grid.get("rowCount", 0),
            "columns": grid.get("columnCount", 0),
        })

    print(json.dumps({
        "spreadsheet_id": args.spreadsheet_id,
        "title": result.get("properties", {}).get("title", ""),
        "url": result.get("spreadsheetUrl", ""),
        "sheets": sheet_list,
    }, indent=2))


def cmd_search(args):
    """Search for spreadsheets."""
    drive = get_drive_service()

    query = f"mimeType='application/vnd.google-apps.spreadsheet' and fullText contains '{args.query}'"
    result = drive.files().list(
        q=query,
        fields="files(id,name,createdTime,modifiedTime,webViewLink)",
        orderBy="relevance desc",
        pageSize=20,
    ).execute()

    docs = []
    for f in result.get("files", []):
        docs.append({
            "id": f["id"],
            "title": f["name"],
            "modified": f.get("modifiedTime", ""),
            "url": f.get("webViewLink", ""),
        })

    print(json.dumps({"query": args.query, "spreadsheets": docs, "count": len(docs)}, indent=2))


# ── CLI Setup ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Google Sheets CLI for PKA")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # auth
    sub.add_parser("auth", help="One-time OAuth setup")
    sub.add_parser("doctor", help="Check Sheets auth health")

    # create
    p_create = sub.add_parser("create", help="Create a new spreadsheet")
    p_create.add_argument("--title", required=True, help="Spreadsheet title")
    p_create.add_argument("--sheets", help="Comma-separated sheet names (default: Sheet1)")
    p_create.add_argument("--file", help="CSV file to populate initial data")

    # read
    p_read = sub.add_parser("read", help="Read data from a sheet")
    p_read.add_argument("spreadsheet_id", help="Spreadsheet ID")
    p_read.add_argument("--range", help="Range like 'Sheet1!A1:D10' (default: all of Sheet1)")

    # write
    p_write = sub.add_parser("write", help="Write data to a sheet")
    p_write.add_argument("spreadsheet_id", help="Spreadsheet ID")
    p_write.add_argument("--range", help="Range like 'Sheet1!A1'")
    p_write.add_argument("--data", help="JSON array of arrays: [[\"a\",\"b\"],[\"c\",\"d\"]]")
    p_write.add_argument("--file", help="CSV or JSON file with data")

    # append
    p_append = sub.add_parser("append", help="Append rows to a sheet")
    p_append.add_argument("spreadsheet_id", help="Spreadsheet ID")
    p_append.add_argument("--range", help="Sheet range (default: Sheet1)")
    p_append.add_argument("--data", help="JSON array of arrays to append")
    p_append.add_argument("--row", help="Single comma-separated row to append")

    # list
    p_list = sub.add_parser("list", help="List spreadsheets")
    p_list.add_argument("--limit", type=int, help="Max results (default: 20)")

    # get
    p_get = sub.add_parser("get", help="Get spreadsheet metadata")
    p_get.add_argument("spreadsheet_id", help="Spreadsheet ID")

    # search
    p_search = sub.add_parser("search", help="Search spreadsheets")
    p_search.add_argument("query", help="Search query")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "auth": cmd_auth,
        "doctor": cmd_doctor,
        "create": cmd_create,
        "read": cmd_read,
        "write": cmd_write,
        "append": cmd_append,
        "list": cmd_list,
        "get": cmd_get,
        "search": cmd_search,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
