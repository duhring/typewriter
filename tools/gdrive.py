#!/usr/bin/env python3
"""
Google Drive helper for PKA — search files by name, optionally download them.

Reuses the existing OAuth token at data/gdocs/token.json (set up via
`tools/gdocs.py auth`). No new consent flow is added here.

Scope caveat:
  Current token scope is `drive.file`, which only exposes files this
  app created or files the user explicitly opened/picked with this
  app. Files uploaded directly to Drive (e.g., via the web UI) may
  not appear in search results. If a search returns nothing for a
  file you can see in Drive, either open the file once via the Drive
  picker integration, or re-auth with broader scope.

Usage:
  discord-bridge/venv/bin/python3 tools/gdrive.py search --name "3bird"
  discord-bridge/venv/bin/python3 tools/gdrive.py search --name "for bird" --kind video
  discord-bridge/venv/bin/python3 tools/gdrive.py search --name "..." --download owners-inbox/cuecam-assets/
  discord-bridge/venv/bin/python3 tools/gdrive.py search --name "..." --download <dir> --all
  discord-bridge/venv/bin/python3 tools/gdrive.py upload --file path/to/render.mp4 --folder "PKA Reviews/yosemite-clip-1"
  discord-bridge/venv/bin/python3 tools/gdrive.py upload --file path/to/render.mp4 --folder-id <id>
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# Reuse the auth + service helpers already wired in cuecam.py
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from cuecam import get_drive_service  # noqa: E402

from googleapiclient.errors import HttpError  # noqa: E402
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload  # noqa: E402


PKA_ROOT = Path(__file__).resolve().parent.parent

KIND_MIME_PREFIXES = {
    "video": "video/",
    "image": "image/",
    "audio": "audio/",
    "pdf": "application/pdf",
    "doc": "application/vnd.google-apps.document",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "slide": "application/vnd.google-apps.presentation",
    "folder": "application/vnd.google-apps.folder",
}


def _build_query(name: str, kind: str | None) -> str:
    # Escape single quotes in the name to avoid breaking the query string.
    safe_name = name.replace("\\", "\\\\").replace("'", "\\'")
    parts = [f"name contains '{safe_name}'", "trashed = false"]
    if kind:
        prefix = KIND_MIME_PREFIXES.get(kind)
        if not prefix:
            raise ValueError(f"unknown --kind {kind!r}; choose from {sorted(KIND_MIME_PREFIXES)}")
        if prefix.endswith("/"):
            parts.append(f"mimeType contains '{prefix}'")
        else:
            parts.append(f"mimeType = '{prefix}'")
    return " and ".join(parts)


def search(name: str, *, kind: str | None = None, limit: int = 10) -> list[dict]:
    drive = get_drive_service()
    query = _build_query(name, kind)
    response = drive.files().list(
        q=query,
        pageSize=max(1, min(limit, 100)),
        fields="files(id, name, mimeType, size, modifiedTime, webViewLink, parents)",
        orderBy="modifiedTime desc",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return response.get("files", [])


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in (" ", ".", "-", "_") else "_" for c in name).strip()


def download_file(file_meta: dict, dest_dir: Path) -> Path:
    drive = get_drive_service()
    file_id = file_meta["id"]
    name = _safe_filename(file_meta.get("name") or file_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    counter = 2
    while target.exists():
        target = dest_dir / f"{Path(name).stem}-{counter}{Path(name).suffix}"
        counter += 1

    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.FileIO(str(target), "wb")
    try:
        downloader = MediaIoBaseDownload(buf, request, chunksize=4 * 1024 * 1024)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
    finally:
        buf.close()
    return target


def upload_file(local_path: Path, *, folder_id: str | None = None, drive_name: str | None = None) -> dict:
    drive = get_drive_service()
    import mimetypes
    mime, _ = mimetypes.guess_type(str(local_path))
    if mime is None:
        mime = "application/octet-stream"
    name = drive_name or local_path.name
    metadata: dict = {"name": name}
    if folder_id:
        metadata["parents"] = [folder_id]
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
    file = drive.files().create(
        body=metadata,
        media_body=media,
        fields="id, name, mimeType, size, webViewLink",
        supportsAllDrives=True,
    ).execute()
    return file


def get_or_create_folder(path: str) -> str:
    """
    Walk or create a folder path like "PKA Reviews/my-slug" in Drive.
    Returns the leaf folder's ID.
    Scope note: drive.file scope only; folders Larry creates are visible.
    """
    drive = get_drive_service()
    parts = [p.strip() for p in path.strip("/").split("/") if p.strip()]
    parent_id: str | None = None

    for part in parts:
        # Search for existing folder with this name under current parent
        q_parts = [f"name = '{part.replace(chr(39), chr(92)+chr(39))}'",
                   "mimeType = 'application/vnd.google-apps.folder'",
                   "trashed = false"]
        if parent_id:
            q_parts.append(f"'{parent_id}' in parents")
        result = drive.files().list(
            q=" and ".join(q_parts),
            fields="files(id, name)",
            pageSize=5,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files = result.get("files", [])
        if files:
            parent_id = files[0]["id"]
        else:
            # Create the folder
            body: dict = {
                "name": part,
                "mimeType": "application/vnd.google-apps.folder",
            }
            if parent_id:
                body["parents"] = [parent_id]
            folder = drive.files().create(
                body=body,
                fields="id",
                supportsAllDrives=True,
            ).execute()
            parent_id = folder["id"]

    if parent_id is None:
        raise ValueError(f"Could not resolve folder path: {path!r}")
    return parent_id


def upload_to_folder(local_path: Path, folder_path: str, *, drive_name: str | None = None) -> dict:
    """Upload a file to a named Drive folder path, creating folders as needed."""
    folder_id = get_or_create_folder(folder_path)
    return upload_file(local_path, folder_id=folder_id, drive_name=drive_name)


def cmd_upload(args) -> int:
    local = Path(args.file)
    if not local.is_absolute():
        local = PKA_ROOT / local
    if not local.exists():
        print(json.dumps({"error": f"File not found: {local}"}), file=sys.stderr)
        return 2
    try:
        if getattr(args, "folder", None):
            result = upload_to_folder(local, args.folder, drive_name=args.name or None)
        else:
            result = upload_file(local, folder_id=args.folder_id or None, drive_name=args.name or None)
    except HttpError as exc:
        print(json.dumps({"error": f"Drive API error: {exc}"}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_search(args) -> int:
    try:
        matches = search(args.name, kind=args.kind, limit=args.limit)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    except HttpError as exc:
        print(json.dumps({"error": f"Drive API error: {exc}"}), file=sys.stderr)
        return 1

    result: dict = {"query": {"name": args.name, "kind": args.kind, "limit": args.limit}, "matches": matches}

    if not matches:
        result["hint"] = (
            "No matches. With the current 'drive.file' scope, files the user uploaded directly "
            "to Drive but never opened with this app are not searchable. Open the file once via "
            "the Drive picker integration, or re-auth with broader scope."
        )

    if args.download and matches:
        dest_dir = Path(args.download)
        if not dest_dir.is_absolute():
            dest_dir = PKA_ROOT / dest_dir
        targets = matches if args.all else matches[:1]
        downloaded: list[str] = []
        errors: list[dict] = []
        for meta in targets:
            mime = meta.get("mimeType", "")
            if mime.startswith("application/vnd.google-apps."):
                errors.append({
                    "id": meta["id"],
                    "name": meta.get("name"),
                    "error": "native Google Workspace files require export, not download; not supported in v1",
                })
                continue
            try:
                local_path = download_file(meta, dest_dir)
                downloaded.append(str(local_path))
            except HttpError as exc:
                errors.append({"id": meta["id"], "name": meta.get("name"), "error": f"Drive API: {exc}"})
            except Exception as exc:
                errors.append({"id": meta["id"], "name": meta.get("name"), "error": f"{exc.__class__.__name__}: {exc}"})
        result["downloaded"] = downloaded
        if errors:
            result["download_errors"] = errors

    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Drive helper for PKA")
    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", help="Search Drive files by name")
    p_search.add_argument("--name", required=True, help="Substring to search in file names")
    p_search.add_argument(
        "--kind",
        choices=sorted(KIND_MIME_PREFIXES),
        help="Filter by mimeType family",
    )
    p_search.add_argument("--limit", type=int, default=10, help="Max results (default 10, max 100)")
    p_search.add_argument("--download", help="Local directory to download match(es) into")
    p_search.add_argument(
        "--all",
        action="store_true",
        help="With --download, materialize every match (default is first only)",
    )

    p_upload = sub.add_parser("upload", help="Upload a local file to Drive")
    p_upload.add_argument("--file", required=True, help="Local path to the file to upload")
    p_upload.add_argument("--name", help="Override the Drive filename (default: local filename)")
    p_upload.add_argument("--folder", help='Named Drive folder path, e.g. "PKA Reviews/my-slug" (created if missing)')
    p_upload.add_argument("--folder-id", help="Drive folder ID to place the file in (alternative to --folder)")

    args = parser.parse_args()
    if args.command == "search":
        return cmd_search(args)
    if args.command == "upload":
        return cmd_upload(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
