#!/usr/bin/env python3
"""
Send a message to the PKA Discord #larry channel.

Usage:
    python3 tools/discord_send.py "Hello from Larry!"
    echo "Multi-line message" | python3 tools/discord_send.py
    python3 tools/discord_send.py --file owners-inbox/briefing.md
    python3 tools/discord_send.py "Here's the PDF." --attach owners-inbox/report.pdf
"""

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import time

import requests
from dotenv import load_dotenv

# Load .env from discord-bridge/
ENV_PATH = Path(__file__).resolve().parent.parent / "discord-bridge" / ".env"
load_dotenv(ENV_PATH)

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("LARRY_CHANNEL_ID")

DISCORD_API = "https://discord.com/api/v10"
MAX_CHUNK = 2000
MAX_ATTACHMENTS = 10


def _require_config(channel_id: str | None = None) -> tuple[str, str]:
    """Return Discord bot config or exit with a clear error."""
    if not BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set in discord-bridge/.env", file=sys.stderr)
        sys.exit(1)
    resolved_channel_id = channel_id or CHANNEL_ID
    if not resolved_channel_id:
        print("ERROR: LARRY_CHANNEL_ID not set in discord-bridge/.env", file=sys.stderr)
        sys.exit(1)
    return BOT_TOKEN, resolved_channel_id


def _post_with_retry(url: str, *, headers: dict, timeout: int = 30, max_retries: int = 4, **kwargs) -> requests.Response:
    """POST with automatic retry on Discord 429 rate-limit responses."""
    for attempt in range(max_retries):
        resp = requests.post(url, headers=headers, timeout=timeout, **kwargs)
        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get("retry_after", 5))
            except Exception:
                retry_after = 5.0
            retry_after = min(retry_after, 30.0)  # cap at 30s
            print(f"Discord rate limited — retrying in {retry_after}s (attempt {attempt+1}/{max_retries})", file=sys.stderr)
            time.sleep(retry_after)
            continue
        return resp
    # Final attempt after all retries exhausted
    return requests.post(url, headers=headers, timeout=timeout, **kwargs)


def _chunk_text(text: str) -> list[str]:
    """Split text into Discord-sized chunks at newline boundaries."""
    if not text.strip():
        return [""]

    chunks = []
    while len(text) > MAX_CHUNK:
        split_at = text.rfind("\n", 0, MAX_CHUNK)
        if split_at == -1:
            split_at = MAX_CHUNK
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    chunks.append(text)
    return chunks


def _resolved_attachment_sources(paths: list[str]) -> list[Path]:
    """Return normalized attachment source paths after checking they exist."""
    attachments = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        else:
            path = path.resolve()
        if not path.exists():
            print(f"ERROR: Attachment not found: {path}", file=sys.stderr)
            sys.exit(1)
        if not (path.is_file() or path.is_dir()):
            print(f"ERROR: Attachment must be a file or directory: {path}", file=sys.stderr)
            sys.exit(1)
        attachments.append(path)

    if len(attachments) > MAX_ATTACHMENTS:
        print(
            f"ERROR: Discord supports at most {MAX_ATTACHMENTS} attachments per message",
            file=sys.stderr,
        )
        sys.exit(1)

    return attachments


def _zip_directory(source_dir: Path) -> tuple[Path, Path]:
    """Zip a directory into a temp file and return (zip_path, temp_dir)."""
    temp_dir = Path(tempfile.mkdtemp(prefix="pka-discord-send-"))
    zip_path = temp_dir / f"{source_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Add directory entries too so empty folders inside bundles survive unzip.
        root_info = zipfile.ZipInfo(f"{source_dir.name}/")
        root_info.external_attr = 0o755 << 16
        zf.writestr(root_info, "")
        for child in sorted(source_dir.rglob("*")):
            arcname = source_dir.name / child.relative_to(source_dir)
            if child.is_dir():
                dir_info = zipfile.ZipInfo(f"{arcname.as_posix()}/")
                dir_info.external_attr = 0o755 << 16
                zf.writestr(dir_info, "")
                continue
            zf.write(child, arcname=str(arcname))
    return zip_path, temp_dir


def _prepare_attachments(paths: list[Path]) -> list[dict]:
    """Prepare files for upload, auto-zipping directories like .cuecam bundles."""
    prepared = []
    for path in paths:
        if path.is_dir():
            upload_path, temp_dir = _zip_directory(path)
            prepared.append(
                {
                    "source_path": path,
                    "upload_path": upload_path,
                    "cleanup_dir": temp_dir,
                    "kind": "directory",
                }
            )
        else:
            prepared.append(
                {
                    "source_path": path,
                    "upload_path": path,
                    "cleanup_dir": None,
                    "kind": "file",
                }
            )
    return prepared


def _cleanup_prepared_attachments(prepared: list[dict]) -> None:
    """Remove any temporary archives created for directory attachments."""
    for item in prepared:
        cleanup_dir = item.get("cleanup_dir")
        if not cleanup_dir:
            continue
        try:
            for child in cleanup_dir.iterdir():
                child.unlink()
            cleanup_dir.rmdir()
        except Exception:
            pass


def send_message(
    text: str,
    attachments: list[Path] | None = None,
    dry_run: bool = False,
    channel_id: str | None = None,
) -> None:
    """Send text and optional attachments to the Discord channel."""
    bot_token, channel_id = _require_config(channel_id)
    attachments = attachments or []
    prepared_attachments = _prepare_attachments(attachments)

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    chunks = _chunk_text(text)

    if dry_run:
        print(
            json.dumps(
                {
                    "channel_id": channel_id,
                    "chunks": chunks,
                    "attachments": [
                        {
                            "source_path": str(item["source_path"]),
                            "upload_path": str(item["upload_path"]),
                            "kind": item["kind"],
                            "size_bytes": item["upload_path"].stat().st_size,
                        }
                        for item in prepared_attachments
                    ],
                },
                indent=2,
            )
        )
        _cleanup_prepared_attachments(prepared_attachments)
        return

    if attachments:
        files = []
        handles = []
        try:
            first_chunk = chunks[0] if chunks else ""
            payload = {"content": first_chunk}
            for i, item in enumerate(prepared_attachments):
                upload_path = item["upload_path"]
                mime_type = mimetypes.guess_type(upload_path.name)[0] or "application/octet-stream"
                handle = upload_path.open("rb")
                handles.append(handle)
                files.append((f"files[{i}]", (upload_path.name, handle, mime_type)))

            multipart_headers = {"Authorization": f"Bot {bot_token}"}
            resp = _post_with_retry(
                url,
                headers=multipart_headers,
                data={"payload_json": json.dumps(payload)},
                files=files,
                timeout=30,
            )
            if not resp.ok:
                print(f"ERROR: Discord API {resp.status_code}: {resp.text}", file=sys.stderr)
                sys.exit(1)
        finally:
            for handle in handles:
                handle.close()
            _cleanup_prepared_attachments(prepared_attachments)

        remaining_chunks = chunks[1:]
    else:
        remaining_chunks = chunks

    for chunk in remaining_chunks:
        if not chunk.strip():
            continue
        resp = _post_with_retry(url, headers=headers, json={"content": chunk}, timeout=10)
        if not resp.ok:
            print(f"ERROR: Discord API {resp.status_code}: {resp.text}", file=sys.stderr)
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Send a message to PKA Discord channel")
    parser.add_argument("message", nargs="?", help="Message text")
    parser.add_argument("--file", help="Read message from file")
    parser.add_argument(
        "--attach",
        action="append",
        default=[],
        help="Attach a local file to the Discord message; may be repeated",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the payload that would be sent without sending it",
    )
    parser.add_argument(
        "--channel-id",
        help="Explicit destination channel ID (defaults to LARRY_CHANNEL_ID)",
    )
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.message:
        text = args.message
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""

    attachments = _resolved_attachment_sources(args.attach)
    if not text.strip() and not attachments:
        parser.print_help()
        sys.exit(1)

    send_message(
        text.strip(),
        attachments=attachments,
        dry_run=args.dry_run,
        channel_id=args.channel_id,
    )
    if args.dry_run:
        print("Dry run complete.")
    else:
        print("Sent.")


if __name__ == "__main__":
    main()
