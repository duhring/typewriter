#!/usr/bin/env python3
"""
Google Docs CLI tool for PKA.
Usage: discord-bridge/venv/bin/python3 tools/gdocs.py <command> [options]

Commands:
  auth        One-time OAuth setup (opens browser)
  doctor      Check Docs/Drive auth health
  create      Create a new Google Doc (from text, markdown, or file)
  list        List docs created by this app
  get         Get document content by ID
  search      Search docs in Drive
  update      Update a document's content
  share       Share a doc with someone

Output: JSON to stdout. Errors to stderr.
"""

import argparse
import io
import json
import re
import sys
import os
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from auth_doctor import check_docs

PKA_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = PKA_ROOT / "data" / "gdocs"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"
PKA_FOLDER_FILE = CREDENTIALS_DIR / "pka_folder_id.txt"
# Reuse the same client secret from gcal
CLIENT_SECRET_FILE = PKA_ROOT / "data" / "gcal" / "client_secret.json"
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]


def get_pka_folder_id():
    """Return the saved PKA Drive folder ID, or None."""
    if PKA_FOLDER_FILE.exists():
        return PKA_FOLDER_FILE.read_text().strip() or None
    return None


def get_drive_service():
    """Build and return an authenticated Drive API service."""
    creds = _get_creds()
    return build("drive", "v3", credentials=creds)


def get_docs_service():
    """Build and return an authenticated Docs API service."""
    creds = _get_creds()
    return build("docs", "v1", credentials=creds)


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
                print("Token expired and refresh failed. Run: tools/gdocs.py auth", file=sys.stderr)
                sys.exit(1)
            TOKEN_FILE.write_text(creds.to_json())
        else:
            print("Not authenticated. Run: tools/gdocs.py auth", file=sys.stderr)
            sys.exit(1)
    return creds


def md_to_docs_requests(markdown_text):
    """Convert markdown text to Google Docs API batchUpdate requests.

    Handles: headings (#, ##, ###), bold (**text**), italic (*text*),
    bullet lists (- item), numbered lists (1. item), links [text](url).
    Returns a list of requests to insert formatted text.
    """
    requests = []
    lines = markdown_text.split("\n")
    current_index = 1  # Google Docs content starts at index 1

    for line in lines:
        # Determine heading level
        heading_match = re.match(r'^(#{1,3})\s+(.*)', line)
        bold_pattern = re.compile(r'\*\*(.*?)\*\*')
        italic_pattern = re.compile(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)')

        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            named_style = {1: "HEADING_1", 2: "HEADING_2", 3: "HEADING_3"}[level]

            # Insert text
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": text + "\n"
                }
            })
            # Apply heading style
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": current_index, "endIndex": current_index + len(text) + 1},
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType"
                }
            })
            current_index += len(text) + 1

        elif line.startswith("- ") or line.startswith("* "):
            text = line[2:].strip()
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": text + "\n"
                }
            })
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": current_index, "endIndex": current_index + len(text) + 1},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"
                }
            })
            current_index += len(text) + 1

        elif re.match(r'^\d+\.\s', line):
            text = re.sub(r'^\d+\.\s', '', line).strip()
            requests.append({
                "insertText": {
                    "location": {"index": current_index},
                    "text": text + "\n"
                }
            })
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": current_index, "endIndex": current_index + len(text) + 1},
                    "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN"
                }
            })
            current_index += len(text) + 1

        else:
            # Regular paragraph
            clean_text = line if line.strip() else ""
            if clean_text:
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": clean_text + "\n"
                    }
                })

                # Apply bold formatting
                offset = 0
                for match in bold_pattern.finditer(clean_text):
                    bold_text = match.group(1)
                    start = current_index + match.start() - offset
                    # Adjust for removed ** markers
                    requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": start, "endIndex": start + len(bold_text)},
                            "textStyle": {"bold": True},
                            "fields": "bold"
                        }
                    })

                current_index += len(clean_text) + 1
            else:
                requests.append({
                    "insertText": {
                        "location": {"index": current_index},
                        "text": "\n"
                    }
                })
                current_index += 1

    return requests


def simple_insert(text):
    """Create a simple text insert at the beginning of a doc."""
    # Strip markdown formatting for a clean insert
    clean = text
    clean = re.sub(r'\*\*(.*?)\*\*', r'\1', clean)  # Remove bold markers
    clean = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'\1', clean)  # Remove italic markers
    clean = re.sub(r'^#{1,3}\s+', '', clean, flags=re.MULTILINE)  # Remove heading markers

    return [{"insertText": {"location": {"index": 1}, "text": clean}}]


def strip_frontmatter(text):
    """Remove a simple YAML frontmatter block when present."""
    if not text.startswith("---\n"):
        return text

    lines = text.splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            remainder = "\n".join(lines[index + 1 :]).lstrip("\n")
            return remainder
    return text


# ── Commands ─────────────────────────────────────────────────────────

def cmd_auth(args):
    """Run the one-time OAuth flow."""
    if not CLIENT_SECRET_FILE.exists():
        print(json.dumps({
            "error": f"Missing {CLIENT_SECRET_FILE}",
            "help": "Reuses the same client_secret.json from data/gcal/. Run gcal.py auth first if you haven't."
        }))
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    print(json.dumps({"status": "authenticated", "message": "Google Docs/Drive connected successfully!"}))


def cmd_doctor(args):
    """Check Docs/Drive auth health and a lightweight API probe."""
    print(json.dumps(check_docs(), indent=2))


def cmd_create(args):
    """Create a new Google Doc."""
    drive = get_drive_service()
    docs = get_docs_service()

    # Get content
    if args.file:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = PKA_ROOT / file_path
        if not file_path.exists():
            print(json.dumps({"error": f"File not found: {file_path}"}), file=sys.stderr)
            sys.exit(1)
        content = file_path.read_text(encoding="utf-8")
    elif args.content:
        content = args.content
    else:
        # Read from stdin
        content = sys.stdin.read()

    content = strip_frontmatter(content)

    # Create empty doc
    doc_metadata = {"name": args.title}

    # Use explicit folder, else fall back to saved PKA folder
    folder_id = args.folder_id or get_pka_folder_id()
    if folder_id:
        doc_metadata["parents"] = [folder_id]

    doc_file = drive.files().create(
        body={**doc_metadata, "mimeType": "application/vnd.google-apps.document"},
        fields="id,name,webViewLink"
    ).execute()

    doc_id = doc_file["id"]
    doc_url = doc_file.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")

    # Insert content
    if content.strip():
        try:
            insert_requests = md_to_docs_requests(content)
            if insert_requests:
                docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": insert_requests}
                ).execute()
        except Exception:
            # Fallback to simple insert if markdown parsing fails
            try:
                docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": simple_insert(content)}
                ).execute()
            except Exception as e:
                print(json.dumps({
                    "status": "created_empty",
                    "warning": f"Doc created but content insert failed: {str(e)}",
                    "doc_id": doc_id,
                    "title": args.title,
                    "url": doc_url,
                }), indent=2)
                return

    print(json.dumps({
        "status": "created",
        "doc_id": doc_id,
        "title": args.title,
        "url": doc_url,
    }, indent=2))


def cmd_list(args):
    """List Google Docs created by this app."""
    drive = get_drive_service()

    query = "mimeType='application/vnd.google-apps.document'"
    if args.folder_id:
        query += f" and '{args.folder_id}' in parents"

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
            "url": f.get("webViewLink", f"https://docs.google.com/document/d/{f['id']}/edit"),
        })

    print(json.dumps({"documents": docs, "count": len(docs)}, indent=2))


def cmd_get(args):
    """Get the content of a Google Doc."""
    docs = get_docs_service()

    doc = docs.documents().get(documentId=args.doc_id).execute()

    # Extract text content
    content_parts = []
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            for elem in element["paragraph"].get("elements", []):
                if "textRun" in elem:
                    content_parts.append(elem["textRun"]["content"])

    text = "".join(content_parts)

    print(json.dumps({
        "doc_id": args.doc_id,
        "title": doc.get("title", ""),
        "content": text,
        "url": f"https://docs.google.com/document/d/{args.doc_id}/edit",
    }, indent=2))


def cmd_search(args):
    """Search for Google Docs in Drive."""
    drive = get_drive_service()

    query = f"mimeType='application/vnd.google-apps.document' and fullText contains '{args.query}'"

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
            "created": f.get("createdTime", ""),
            "modified": f.get("modifiedTime", ""),
            "url": f.get("webViewLink", f"https://docs.google.com/document/d/{f['id']}/edit"),
        })

    print(json.dumps({"query": args.query, "documents": docs, "count": len(docs)}, indent=2))


def cmd_update(args):
    """Update a Google Doc's content (replaces all content)."""
    docs = get_docs_service()

    # Get current doc to find content length
    doc = docs.documents().get(documentId=args.doc_id).execute()

    # Find the end index of existing content
    body_content = doc.get("body", {}).get("content", [])
    end_index = 1
    for element in body_content:
        if "endIndex" in element:
            end_index = element["endIndex"]

    requests = []

    # Delete existing content (if any beyond the initial newline)
    if end_index > 2:
        requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_index - 1}
            }
        })

    # Get new content
    if args.file:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = PKA_ROOT / file_path
        content = file_path.read_text(encoding="utf-8")
    elif args.content:
        content = args.content
    else:
        content = sys.stdin.read()

    content = strip_frontmatter(content)

    insert_requests = []
    if content.strip():
        try:
            insert_requests = md_to_docs_requests(content)
        except Exception:
            insert_requests = []

    if insert_requests:
        requests.extend(insert_requests)
    else:
        requests.append({
            "insertText": {
                "location": {"index": 1},
                "text": content
            }
        })

    if requests:
        docs.documents().batchUpdate(
            documentId=args.doc_id,
            body={"requests": requests}
        ).execute()

    print(json.dumps({
        "status": "updated",
        "doc_id": args.doc_id,
        "url": f"https://docs.google.com/document/d/{args.doc_id}/edit",
    }, indent=2))


def cmd_share(args):
    """Share a Google Doc with someone."""
    drive = get_drive_service()

    permission = {
        "type": "user",
        "role": args.role or "writer",
        "emailAddress": args.email,
    }

    result = drive.permissions().create(
        fileId=args.doc_id,
        body=permission,
        sendNotificationEmail=not args.no_notify,
    ).execute()

    print(json.dumps({
        "status": "shared",
        "doc_id": args.doc_id,
        "shared_with": args.email,
        "role": args.role or "writer",
        "url": f"https://docs.google.com/document/d/{args.doc_id}/edit",
    }, indent=2))


def cmd_create_pdf(args):
    """Create a Google Doc from a file, export it as PDF, upload PDF to Drive."""
    drive = get_drive_service()
    docs = get_docs_service()

    # Resolve file path
    file_path = Path(args.file)
    if not file_path.is_absolute():
        file_path = PKA_ROOT / file_path
    if not file_path.exists():
        print(json.dumps({"error": f"File not found: {file_path}"}), file=sys.stderr)
        sys.exit(1)

    content = file_path.read_text(encoding="utf-8")
    content = strip_frontmatter(content)

    pdf_name = args.pdf_name or (file_path.stem + ".pdf")
    doc_title = args.title or file_path.stem.replace("-", " ").title()

    folder_id = args.folder_id or get_pka_folder_id()

    # Step 1: Create a temporary Google Doc
    doc_metadata = {"name": doc_title, "mimeType": "application/vnd.google-apps.document"}
    if folder_id:
        doc_metadata["parents"] = [folder_id]

    doc_file = drive.files().create(
        body=doc_metadata,
        fields="id,name,webViewLink"
    ).execute()
    doc_id = doc_file["id"]

    # Insert content
    if content.strip():
        try:
            insert_requests = md_to_docs_requests(content)
            if insert_requests:
                docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": insert_requests}
                ).execute()
        except Exception:
            try:
                docs.documents().batchUpdate(
                    documentId=doc_id,
                    body={"requests": simple_insert(content)}
                ).execute()
            except Exception as e:
                print(json.dumps({"error": f"Content insert failed: {e}"}), file=sys.stderr)

    # Step 2: Export as PDF
    pdf_bytes = drive.files().export(
        fileId=doc_id,
        mimeType="application/pdf"
    ).execute()

    # Step 3: Upload PDF to Drive
    pdf_metadata = {"name": pdf_name}
    if folder_id:
        pdf_metadata["parents"] = [folder_id]

    pdf_file = drive.files().create(
        body=pdf_metadata,
        media_body=MediaIoBaseUpload(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            resumable=False,
        ),
        fields="id,name,webViewLink"
    ).execute()

    # Optionally delete the intermediate Google Doc
    if not args.keep_doc:
        drive.files().delete(fileId=doc_id).execute()
        doc_id = None

    print(json.dumps({
        "status": "created",
        "pdf_id": pdf_file["id"],
        "pdf_name": pdf_file["name"],
        "pdf_url": pdf_file.get("webViewLink", ""),
        "doc_id": doc_id,
    }, indent=2))


def cmd_mkdir(args):
    """Create a folder in Google Drive."""
    drive = get_drive_service()

    metadata = {
        "name": args.name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if args.parent_id:
        metadata["parents"] = [args.parent_id]

    folder = drive.files().create(
        body=metadata,
        fields="id,name,webViewLink"
    ).execute()

    folder_id = folder["id"]
    folder_url = folder.get("webViewLink", f"https://drive.google.com/drive/folders/{folder_id}")

    result = {
        "status": "created",
        "folder_id": folder_id,
        "name": args.name,
        "url": folder_url,
    }

    # Save as the PKA default folder if requested
    if args.save_as_pka:
        CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
        PKA_FOLDER_FILE.write_text(folder_id)
        result["saved_as_pka_default"] = True

    print(json.dumps(result, indent=2))


def cmd_move(args):
    """Move a Drive file into a folder."""
    drive = get_drive_service()

    # Get current parents
    file_meta = drive.files().get(fileId=args.file_id, fields="parents").execute()
    current_parents = ",".join(file_meta.get("parents", []))

    drive.files().update(
        fileId=args.file_id,
        addParents=args.folder_id,
        removeParents=current_parents,
        fields="id,name,parents,webViewLink",
    ).execute()

    print(json.dumps({
        "status": "moved",
        "file_id": args.file_id,
        "folder_id": args.folder_id,
        "url": f"https://drive.google.com/drive/folders/{args.folder_id}",
    }, indent=2))


def cmd_folders(args):
    """List folders in Google Drive (created by this app)."""
    drive = get_drive_service()

    query = "mimeType='application/vnd.google-apps.folder'"
    if args.parent_id:
        query += f" and '{args.parent_id}' in parents"

    result = drive.files().list(
        q=query,
        fields="files(id,name,createdTime,webViewLink)",
        orderBy="name",
        pageSize=50,
    ).execute()

    pka_folder_id = get_pka_folder_id()
    folders = []
    for f in result.get("files", []):
        entry = {
            "id": f["id"],
            "name": f["name"],
            "created": f.get("createdTime", ""),
            "url": f.get("webViewLink", f"https://drive.google.com/drive/folders/{f['id']}"),
        }
        if f["id"] == pka_folder_id:
            entry["pka_default"] = True
        folders.append(entry)

    print(json.dumps({"folders": folders, "count": len(folders), "pka_folder_id": pka_folder_id}, indent=2))


# ── CLI Setup ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Google Docs CLI for PKA")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # auth
    sub.add_parser("auth", help="One-time OAuth setup")
    sub.add_parser("doctor", help="Check Docs/Drive auth health")

    # create
    p_create = sub.add_parser("create", help="Create a new Google Doc")
    p_create.add_argument("--title", required=True, help="Document title")
    p_create.add_argument("--content", help="Markdown/text content (or use --file)")
    p_create.add_argument("--file", help="Path to a local .md or .txt file")
    p_create.add_argument("--folder-id", help="Google Drive folder ID to create in")

    # list
    p_list = sub.add_parser("list", help="List docs created by this app")
    p_list.add_argument("--folder-id", help="List docs in a specific folder")
    p_list.add_argument("--limit", type=int, help="Max results (default: 20)")

    # get
    p_get = sub.add_parser("get", help="Get document content")
    p_get.add_argument("doc_id", help="Google Doc ID")

    # search
    p_search = sub.add_parser("search", help="Search docs in Drive")
    p_search.add_argument("query", help="Search query")

    # update
    p_update = sub.add_parser("update", help="Update document content")
    p_update.add_argument("doc_id", help="Google Doc ID")
    p_update.add_argument("--content", help="New content (or use --file)")
    p_update.add_argument("--file", help="Path to file with new content")

    # share
    p_share = sub.add_parser("share", help="Share a doc with someone")
    p_share.add_argument("doc_id", help="Google Doc ID")
    p_share.add_argument("--email", required=True, help="Email to share with")
    p_share.add_argument("--role", choices=["reader", "writer", "commenter"], default="writer", help="Permission role")
    p_share.add_argument("--no-notify", action="store_true", help="Don't send notification email")

    # mkdir
    p_mkdir = sub.add_parser("mkdir", help="Create a folder in Google Drive")
    p_mkdir.add_argument("name", help="Folder name")
    p_mkdir.add_argument("--parent-id", help="Parent folder ID (omit for Drive root)")
    p_mkdir.add_argument("--save-as-pka", action="store_true", help="Save folder ID as PKA default (auto-used by create commands)")

    # move
    p_move = sub.add_parser("move", help="Move a file into a folder")
    p_move.add_argument("file_id", help="Drive file ID to move")
    p_move.add_argument("folder_id", help="Destination folder ID")

    # folders
    p_folders = sub.add_parser("folders", help="List Drive folders created by this app")
    p_folders.add_argument("--parent-id", help="List only folders inside this parent folder")

    # create-pdf
    p_cpdf = sub.add_parser("create-pdf", help="Create Google Doc from file, export as PDF, upload PDF to Drive")
    p_cpdf.add_argument("--file", required=True, help="Path to local .md or .txt file")
    p_cpdf.add_argument("--title", help="Google Doc title (default: derived from filename)")
    p_cpdf.add_argument("--pdf-name", help="PDF filename on Drive (default: <slug>.pdf)")
    p_cpdf.add_argument("--folder-id", help="Drive folder ID (default: saved PKA folder)")
    p_cpdf.add_argument("--keep-doc", action="store_true", help="Keep the intermediate Google Doc")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "auth": cmd_auth,
        "doctor": cmd_doctor,
        "create": cmd_create,
        "list": cmd_list,
        "get": cmd_get,
        "search": cmd_search,
        "update": cmd_update,
        "share": cmd_share,
        "mkdir": cmd_mkdir,
        "move": cmd_move,
        "folders": cmd_folders,
        "create-pdf": cmd_create_pdf,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
