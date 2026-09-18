#!/usr/bin/env python3
"""
One-off: create Google Doc from blog post, export as PDF, upload PDF to PKA Drive folder.
Usage: discord-bridge/venv/bin/python3 tools/_export_blog_pdf.py <markdown_file>
"""

import io
import json
import sys
from pathlib import Path

# Reuse gdocs auth infrastructure
tools_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(tools_dir))
PKA_ROOT = tools_dir.parent
CREDENTIALS_DIR = PKA_ROOT / "data" / "gdocs"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"
PKA_FOLDER_FILE = CREDENTIALS_DIR / "pka_folder_id.txt"
CLIENT_SECRET_FILE = PKA_ROOT / "data" / "gcal" / "client_secret.json"
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


def get_creds():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())
    if not creds or not creds.valid:
        raise RuntimeError("Auth token missing or invalid. Run: discord-bridge/venv/bin/python3 tools/gdocs.py auth")
    return creds


def md_to_plain(md_text):
    """Strip markdown to plain text for Google Docs upload (preserves structure)."""
    return md_text  # Docs API accepts plain text; headers become paragraphs


def main():
    if len(sys.argv) < 2:
        print("Usage: _export_blog_pdf.py <markdown_file>", file=sys.stderr)
        sys.exit(1)

    md_path = Path(sys.argv[1])
    if not md_path.exists():
        print(f"File not found: {md_path}", file=sys.stderr)
        sys.exit(1)

    md_text = md_path.read_text(encoding="utf-8")
    title = md_path.stem.replace("-", " ").title()
    pdf_name = md_path.stem + ".pdf"

    pka_folder_id = None
    if PKA_FOLDER_FILE.exists():
        pka_folder_id = PKA_FOLDER_FILE.read_text().strip() or None

    creds = get_creds()
    drive = build("drive", "v3", credentials=creds)

    # Step 1: Upload markdown as a Google Doc (import as Docs format)
    print(f"Creating Google Doc: {title}", file=sys.stderr)
    file_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if pka_folder_id:
        file_metadata["parents"] = [pka_folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(md_text.encode("utf-8")),
        mimetype="text/plain",
        resumable=False,
    )
    doc_file = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()

    doc_id = doc_file["id"]
    doc_url = doc_file.get("webViewLink", "")
    print(f"Google Doc created: {doc_id}", file=sys.stderr)

    # Step 2: Export the Google Doc as PDF
    print("Exporting as PDF...", file=sys.stderr)
    pdf_bytes = drive.files().export(fileId=doc_id, mimeType="application/pdf").execute()

    # Step 3: Upload the PDF to Drive (PKA folder if set)
    print(f"Uploading PDF: {pdf_name}", file=sys.stderr)
    pdf_metadata = {"name": pdf_name}
    if pka_folder_id:
        pdf_metadata["parents"] = [pka_folder_id]

    pdf_media = MediaIoBaseUpload(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        resumable=False,
    )
    pdf_file = drive.files().create(
        body=pdf_metadata,
        media_body=pdf_media,
        fields="id,name,webViewLink",
    ).execute()

    print(json.dumps({
        "doc_id": doc_id,
        "doc_url": doc_url,
        "pdf_id": pdf_file["id"],
        "pdf_name": pdf_file["name"],
        "pdf_url": pdf_file.get("webViewLink", ""),
    }))


if __name__ == "__main__":
    main()
