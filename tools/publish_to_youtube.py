#!/usr/bin/env python3
"""
Upload a video to YouTube using a Maven package .md file for metadata.

Commands:
  auth       One-time OAuth setup (opens browser)
  doctor     Check YouTube auth health
  titles     List title options from a package file
  thumbnail  Generate thumbnail from Maven package prompt (optionally with reference images)
  upload     Upload video with metadata from package file

Examples:
  # One-time setup:
  python3 tools/publish_to_youtube.py auth

  # Preview titles from the package, pick one:
  python3 tools/publish_to_youtube.py titles --package owners-inbox/youtube/2026-04-25-slug.md

  # Generate thumbnail — asks if you have reference images to provide:
  python3 tools/publish_to_youtube.py thumbnail --latest

  # Generate thumbnail with explicit reference images (no prompt):
  python3 tools/publish_to_youtube.py thumbnail --latest --images ~/Desktop/headshot.jpg ~/Desktop/product.png

  # Generate thumbnail, skip the image prompt:
  python3 tools/publish_to_youtube.py thumbnail --latest --yes

  # Upload with explicit paths:
  python3 tools/publish_to_youtube.py upload \\
    --video "/path/to/final-edit.mov" \\
    --package owners-inbox/youtube/2026-04-25-slug.md \\
    --title 1 \\
    --privacy unlisted

  # Upload using auto-detected latest cleaned recording + latest package:
  python3 tools/publish_to_youtube.py upload --latest --title 1 --privacy unlisted
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


PKA_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_DIR = PKA_ROOT / "data" / "youtube"
TOKEN_FILE = CREDENTIALS_DIR / "token.json"
# Reuse the same Google project client secret already in place for Calendar/Docs/Sheets
CLIENT_SECRET_FILE = PKA_ROOT / "data" / "gcal" / "client_secret.json"
# Upload-only by design: this token can add videos but cannot read, modify, or
# delete anything on the channel. Widening to youtube.force-ssl would permit
# videos.update (and deletion) — only do that deliberately, and re-auth after.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

DEFAULT_CATEGORY = "28"   # Science & Technology
DEFAULT_PRIVACY = "private"
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB resumable upload chunks

RECORDINGS_DIR = Path(os.environ.get("PKA_RECORDINGS_DIR", "~/Movies/CueCam Presenter Recordings")).expanduser()
DOWNLOADS_DIR = Path("~/Downloads").expanduser()
PACKAGES_DIR = PKA_ROOT / "owners-inbox" / "youtube"
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
MIN_CONTENT_DURATION = 60  # seconds — skip audio-sync tests


def get_video_duration(path: Path) -> float | None:
    """Return video duration in seconds via ffprobe, or None if unavailable."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return None


def find_latest_video() -> Path | None:
    """
    Return the most recently modified content video over MIN_CONTENT_DURATION seconds.
    Searches the CueCam recordings folder (prefers _cleaned files) and Downloads.
    """
    candidates = []

    # Recordings folder — prefer _cleaned files but accept any content-length video
    if RECORDINGS_DIR.exists():
        for p in RECORDINGS_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and p.stem.endswith("_cleaned"):
                candidates.append(p)

    # Downloads — any video file (editor may save here under any name)
    if DOWNLOADS_DIR.exists():
        for p in DOWNLOADS_DIR.iterdir():
            if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS:
                candidates.append(p)

    # Filter out short sync-test files
    content = [p for p in candidates if (get_video_duration(p) or 0) >= MIN_CONTENT_DURATION]
    if not content:
        return None
    return max(content, key=lambda p: p.stat().st_mtime)


def find_latest_package() -> Path | None:
    """Return the most recently modified Maven package .md file."""
    if not PACKAGES_DIR.exists():
        return None
    candidates = [p for p in PACKAGES_DIR.iterdir() if p.suffix == ".md" and p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_service():
    """Return an authenticated YouTube API service, or exit with a clear message."""
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                TOKEN_FILE.write_text(creds.to_json())
            except Exception as exc:
                print(f"Token refresh failed ({exc}). Run: python3 tools/publish_to_youtube.py auth", file=sys.stderr)
                sys.exit(1)
        else:
            print("Not authenticated. Run: python3 tools/publish_to_youtube.py auth", file=sys.stderr)
            sys.exit(1)
    return build("youtube", "v3", credentials=creds)


def cmd_auth(args: argparse.Namespace) -> int:
    if not CLIENT_SECRET_FILE.exists():
        print(f"Client secret not found: {CLIENT_SECRET_FILE}", file=sys.stderr)
        print("Copy your Google OAuth client_secret.json there and retry.", file=sys.stderr)
        return 1
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json())
    print(json.dumps({"status": "ok", "token": str(TOKEN_FILE)}))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    if not TOKEN_FILE.exists():
        print(json.dumps({"status": "unauthenticated", "fix": "python3 tools/publish_to_youtube.py auth"}))
        return 1
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        if not creds.valid:
            print(json.dumps({"status": "invalid", "fix": "python3 tools/publish_to_youtube.py auth"}))
            return 1
        print(json.dumps({"status": "ok", "token": str(TOKEN_FILE), "scopes": creds.scopes or SCOPES}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "detail": str(exc), "fix": "python3 tools/publish_to_youtube.py auth"}))
        return 1


# ---------------------------------------------------------------------------
# Package parser
# ---------------------------------------------------------------------------

def strip_markdown(text: str) -> str:
    """Remove common markdown formatting for plain-text use."""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)   # bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)         # italic
    text = re.sub(r'`(.*?)`', r'\1', text)           # inline code
    return text


def parse_package(md_path: Path) -> dict:
    """
    Extract title options, description body, chapters, links, and tags
    from a Maven package .md file.

    Returns:
      {
        "titles": ["Title 1", "Title 2", ...],   # in order
        "description": "...",                     # YouTube-ready plain text
        "tags": ["tag1", "tag2", ...],
      }
    """
    text = md_path.read_text(encoding="utf-8")

    # --- Title options ---
    # Match numbered list items under "## 1. Title Options"
    title_section = re.search(
        r'##\s*1\.\s*Title Options(.*?)(?=\n##|\Z)', text, re.DOTALL | re.IGNORECASE
    )
    titles = []
    if title_section:
        # Match lines like: 1. **Label** — *Title text*  or  1. *Title text*
        for m in re.finditer(
            r'^\d+\.\s+(?:\*\*[^*]+\*\*\s*[—–-]+\s*)?\*([^*]+)\*',
            title_section.group(1),
            re.MULTILINE,
        ):
            titles.append(m.group(1).strip())

    # --- Description section ---
    desc_section = re.search(
        r'##\s*2\.\s*YouTube Description(.*?)(?=\n##\s*3\.|\Z)', text, re.DOTALL | re.IGNORECASE
    )
    description = ""
    if desc_section:
        raw = desc_section.group(1).strip()

        # Split out sub-blocks by their bold headers
        body_match = re.match(r'(.*?)(?=\*\*Chapters\*\*|\*\*Links)', raw, re.DOTALL)
        body_text = strip_markdown(body_match.group(1)).strip() if body_match else ""

        # Chapters block → YouTube timestamp format ("M:SS Title" per line)
        chapters_match = re.search(
            r'\*\*Chapters\*\*(.*?)(?=\*\*Links|\*\*Tags|\Z)', raw, re.DOTALL
        )
        chapters_text = ""
        if chapters_match:
            chapter_lines = []
            for line in chapters_match.group(1).splitlines():
                # Match: "- 0:00 — Title" or "- 0:00 - Title"
                m = re.match(r'^\s*[-•]\s*(\d+:\d+(?::\d+)?)\s*[—–-]+\s*(.+)', line)
                if m:
                    chapter_lines.append(f"{m.group(1)} {m.group(2).strip()}")
            if chapter_lines:
                chapters_text = "Chapters\n" + "\n".join(chapter_lines)

        # Links block (strip markdown bold headers, keep URLs)
        links_match = re.search(
            r'\*\*Links[^*]*\*\*(.*?)(?=\*\*Tags|\Z)', raw, re.DOTALL
        )
        links_text = ""
        if links_match:
            raw_links = strip_markdown(links_match.group(1)).strip()
            link_lines = [
                re.sub(r'^\s*[-•]\s*', '', ln).strip()
                for ln in raw_links.splitlines()
                if ln.strip() and not ln.strip().startswith('#')
            ]
            links_text = "\n".join(link_lines)

        parts = [p for p in [body_text, chapters_text, links_text] if p]
        description = "\n\n".join(parts)

    # --- Tags ---
    tags_match = re.search(r'\*\*Tags[^*]*\*\*\s*\n(.*?)(?=\n##|\Z)', text, re.DOTALL | re.IGNORECASE)
    tags = []
    if tags_match:
        raw_tags = tags_match.group(1)
        tags = [t.lstrip('#').strip() for t in re.findall(r'#\w+', raw_tags)]

    # --- Thumbnail prompt ---
    # Everything under "## 3. Thumbnail Prompt" up to the next section or EOF.
    # Returned as-is (plain markdown prose) so generate_thumbnail.py receives the
    # full rich prompt that Maven authored.
    thumb_section = re.search(
        r'##\s*3\.\s*Thumbnail Prompt(.*?)(?=\n##|\Z)', text, re.DOTALL | re.IGNORECASE
    )
    thumbnail_prompt = ""
    if thumb_section:
        thumbnail_prompt = thumb_section.group(1).strip()

    return {
        "titles": titles,
        "description": description,
        "tags": tags,
        "thumbnail_prompt": thumbnail_prompt,
    }


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_titles(args: argparse.Namespace) -> int:
    package_path = Path(args.package)
    if not package_path.exists():
        print(f"Package file not found: {package_path}", file=sys.stderr)
        return 1
    data = parse_package(package_path)
    if not data["titles"]:
        print("No title options found in package.", file=sys.stderr)
        return 1
    print("\nTitle options:\n")
    for i, title in enumerate(data["titles"], 1):
        print(f"  {i}. {title}")
    print(f"\nUse --title <1-{len(data['titles'])}> to select, or --title \"Custom Title\" for a custom string.\n")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    # Resolve paths — either explicit or auto-detected via --latest
    if args.latest:
        video_path = find_latest_video()
        if not video_path:
            print("--latest: no cleaned recordings found in recordings folder.", file=sys.stderr)
            return 1
        package_path = find_latest_package()
        if not package_path:
            print("--latest: no package files found in owners-inbox/youtube/.", file=sys.stderr)
            return 1
    else:
        if not args.video or not args.package:
            print("Provide --video and --package, or use --latest.", file=sys.stderr)
            return 1
        video_path = Path(args.video).resolve()
        if not video_path.exists():
            print(f"Video file not found: {video_path}", file=sys.stderr)
            return 1
        package_path = Path(args.package)
        if not package_path.exists():
            print(f"Package file not found: {package_path}", file=sys.stderr)
            return 1

    data = parse_package(package_path)

    # Resolve title
    title_arg = args.title
    if re.match(r'^\d+$', title_arg):
        idx = int(title_arg) - 1
        if idx < 0 or idx >= len(data["titles"]):
            print(f"Title option {title_arg} out of range (1–{len(data['titles'])}).", file=sys.stderr)
            return 1
        title = data["titles"][idx]
    else:
        title = title_arg  # custom string

    description = data["description"]
    tags = data["tags"]
    privacy = args.privacy
    category = args.category

    # Preview before upload
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Video   : {video_path.name}  ({video_path.stat().st_size / 1024**2:.1f} MB)", file=sys.stderr)
    print(f"  Title   : {title}", file=sys.stderr)
    print(f"  Privacy : {privacy}", file=sys.stderr)
    print(f"  Tags    : {', '.join(tags[:5])}{'...' if len(tags) > 5 else ''}", file=sys.stderr)
    print(f"  Package : {package_path.name}", file=sys.stderr)
    print(f"{'='*60}\n", file=sys.stderr)

    if not args.yes:
        confirm = input("Proceed with upload? [y/N] ").strip().lower()
        if confirm != 'y':
            print("Upload cancelled.", file=sys.stderr)
            return 0

    svc = get_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=CHUNK_SIZE, resumable=True)
    request = svc.videos().insert(part="snippet,status", body=body, media_body=media)

    print("Uploading…", file=sys.stderr)
    response = None
    start = time.time()
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                elapsed = time.time() - start
                print(f"  {pct}%  ({elapsed:.0f}s elapsed)", file=sys.stderr)
        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504):
                print(f"Retryable error ({exc.resp.status}), retrying…", file=sys.stderr)
                time.sleep(5)
            else:
                raise

    video_id = response["id"]
    watch_url = f"https://youtu.be/{video_id}"

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s", file=sys.stderr)

    result = {
        "status": "ok",
        "video_id": video_id,
        "url": watch_url,
        "title": title,
        "privacy": privacy,
        "package": str(package_path),
    }
    if args.thumbnail:
        thumbnail_path = Path(args.thumbnail).expanduser().resolve()
        if not thumbnail_path.exists():
            print(f"Thumbnail file not found after upload: {thumbnail_path}", file=sys.stderr)
            return 1
        thumb_media = MediaFileUpload(str(thumbnail_path), resumable=False)
        svc.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
        result["thumbnail"] = str(thumbnail_path)
    print(json.dumps(result, indent=2))
    return 0


def cmd_thumbnail(args: argparse.Namespace) -> int:
    """
    Generate a thumbnail from the Maven package's thumbnail prompt.

    Steps:
      1. Read the thumbnail prompt from the package file.
      2. Optionally collect reference image paths (--images or interactive).
      3. Delegate to generate_thumbnail.py, streaming its progress output.
      4. Print a JSON result with the saved thumbnail path.
    """
    # Resolve package
    if args.latest:
        package_path = find_latest_package()
        if not package_path:
            print("--latest: no package files found in owners-inbox/youtube/.", file=sys.stderr)
            return 1
    else:
        if not args.package:
            print("Provide --package or use --latest.", file=sys.stderr)
            return 1
        package_path = Path(args.package)
        if not package_path.exists():
            print(f"Package file not found: {package_path}", file=sys.stderr)
            return 1

    data = parse_package(package_path)
    thumbnail_prompt = data.get("thumbnail_prompt", "")
    if not thumbnail_prompt:
        print("No thumbnail prompt found in package (expected '## 3. Thumbnail Prompt' section).", file=sys.stderr)
        return 1

    # Show a short preview of the prompt
    preview = thumbnail_prompt[:300] + ("…" if len(thumbnail_prompt) > 300 else "")
    print(f"\nPackage  : {package_path.name}", file=sys.stderr)
    print(f"\nThumbnail prompt preview:\n{preview}\n", file=sys.stderr)

    # Collect reference images
    image_paths: list[Path] = []

    if args.images:
        for p in args.images:
            path = Path(p).expanduser()
            if not path.exists():
                print(f"Image not found: {path}", file=sys.stderr)
                return 1
            image_paths.append(path)
    elif not args.yes:
        print(
            "Do you have additional reference images to include in the thumbnail?\n"
            "Enter one or more file paths separated by spaces, or press Enter to skip:",
            file=sys.stderr,
        )
        user_input = input("> ").strip()
        if user_input:
            for raw in user_input.split():
                path = Path(raw.strip()).expanduser()
                if not path.exists():
                    print(f"Warning: image not found, skipping: {path}", file=sys.stderr)
                else:
                    image_paths.append(path)

    if image_paths:
        print(
            f"\nUsing {len(image_paths)} reference image(s): "
            + ", ".join(p.name for p in image_paths),
            file=sys.stderr,
        )
    else:
        print("No reference images — generating from prompt only.", file=sys.stderr)

    # Derive output slug from package filename (strip leading date)
    slug = args.slug or re.sub(r'^\d{4}-\d{2}-\d{2}-', '', package_path.stem)

    # Build subprocess command
    cmd = [
        sys.executable,
        str(PKA_ROOT / "tools" / "generate_thumbnail.py"),
        thumbnail_prompt,
        "--slug", slug,
        "--model", args.model,
    ]
    if image_paths:
        cmd += ["--images"] + [str(p) for p in image_paths]

    print(f"\nGenerating thumbnail…\n", file=sys.stderr)

    # Stream stderr (progress) live; capture stdout (the saved path)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0:
        return proc.returncode

    thumbnail_path = proc.stdout.strip()
    result = {
        "status": "ok",
        "thumbnail": thumbnail_path,
        "package": str(package_path),
        "model": args.model,
        "images_used": [str(p) for p in image_paths],
    }
    print(json.dumps(result, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload videos to YouTube using Maven package files for metadata."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="One-time OAuth setup (opens browser)")
    sub.add_parser("doctor", help="Check YouTube auth health")

    p_titles = sub.add_parser("titles", help="List title options from a package file")
    p_titles.add_argument("--package", required=True, help="Path to Maven package .md file")

    p_thumb = sub.add_parser("thumbnail", help="Generate thumbnail from Maven package prompt")
    p_thumb.add_argument("--package", help="Path to Maven package .md file")
    p_thumb.add_argument(
        "--latest", action="store_true",
        help="Auto-detect the newest package file in owners-inbox/youtube/"
    )
    p_thumb.add_argument(
        "--images", nargs="*", metavar="PATH",
        help="Reference image file paths (passed to generate_thumbnail.py --images)"
    )
    p_thumb.add_argument("--slug", help="Override output filename slug (default: derived from package name)")
    p_thumb.add_argument(
        "--model", default="gpt-image-1",
        help="Image generation model (default: gpt-image-1)"
    )
    p_thumb.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip interactive image-path prompt"
    )

    p_upload = sub.add_parser("upload", help="Upload video to YouTube")
    p_upload.add_argument("--video", help="Path to final video file")
    p_upload.add_argument("--package", help="Path to Maven package .md file")
    p_upload.add_argument(
        "--latest", action="store_true",
        help="Auto-detect most recent cleaned recording and newest package file"
    )
    p_upload.add_argument(
        "--title", required=True,
        help="Title option number (1–6) or a custom title string"
    )
    p_upload.add_argument(
        "--privacy", choices=["private", "unlisted", "public"], default=DEFAULT_PRIVACY,
        help=f"Video privacy status (default: {DEFAULT_PRIVACY})"
    )
    p_upload.add_argument(
        "--category", default=DEFAULT_CATEGORY,
        help=f"YouTube category ID (default: {DEFAULT_CATEGORY} = Science & Technology)"
    )
    p_upload.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip confirmation prompt"
    )
    p_upload.add_argument(
        "--thumbnail",
        help="Selected thumbnail image to apply after the upload succeeds",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "auth":
        return cmd_auth(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "titles":
        return cmd_titles(args)
    if args.command == "thumbnail":
        return cmd_thumbnail(args)
    if args.command == "upload":
        return cmd_upload(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
