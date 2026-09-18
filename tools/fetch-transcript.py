#!/usr/bin/env python3
"""
Fetch a transcript from a YouTube URL/ID or a local video file.

YouTube:  python3 fetch-transcript.py <url-or-video-id>
Local:    python3 fetch-transcript.py /path/to/video.mov [--title "My Video"]

For local files, Whisper is used to transcribe before upload — so Maven can
package the video before it ever hits YouTube.
"""

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from youtube_transcript_api import YouTubeTranscriptApi

from extract_structured import build_transcript_extract_artifact
from pka_index import index_transcript_file


PKA_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = PKA_ROOT / "owners-inbox" / "transcripts"
MEDIA_EXTENSIONS = {
    # Video
    ".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm",
    # Audio
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff",
}

# Whisper binary: prefer system PATH, then the venvs we actually ship, then a
# user-level install. Each candidate is checked for existence — an earlier
# version chained these with `or` around str(), which is always truthy, so a
# missing venv path won every time and the later fallbacks were dead code.
def _whisper_command() -> list[str] | None:
    """Return argv prefix for invoking Whisper, or None if unavailable.

    Prefer `python -m whisper` over the console script. Generated console
    scripts hardcode their venv interpreter in a shebang, so renaming a venv can
    leave an executable-looking script that fails with a confusing ENOENT.
    Running the module with the active interpreter avoids that relocation
    dependency even when the console launcher later becomes stale.
    """
    if importlib.util.find_spec("whisper") is not None:
        return [sys.executable, "-m", "whisper"]
    found = shutil.which("whisper")
    if found:
        return [found]
    for path in (
        PKA_ROOT / "discord-bridge" / "venv" / "bin" / "whisper",
        PKA_ROOT / "venv" / "bin" / "whisper",
    ):
        if path.exists():
            return [str(path)]
    return None


WHISPER_CMD = _whisper_command()
# Use turbo model (matches HyperFrames default); falls back gracefully if unavailable
WHISPER_MODEL = "turbo"


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------

def extract_video_id(url_or_id: str) -> str:
    """Extract video ID from various YouTube URL formats."""
    if re.match(r'^[\w-]{11}$', url_or_id):
        return url_or_id
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([\w-]{11})',
        r'youtube\.com/shorts/([\w-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from: {url_or_id}")


def fetch_transcript(video_id: str) -> list:
    """Fetch transcript entries for a YouTube video."""
    ytt_api = YouTubeTranscriptApi()
    transcript = ytt_api.fetch(video_id)
    return transcript.snippets


def canonical_video_url(video_id: str) -> str:
    return f"https://youtube.com/watch?v={video_id}"


def fetch_video_title(video_id: str) -> str:
    url = f"https://www.youtube.com/oembed?{urlencode({'url': canonical_video_url(video_id), 'format': 'json'})}"
    with urlopen(url, timeout=10) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError(f"No title returned for video {video_id}")
    return unescape(title)


def format_youtube_as_markdown(snippets, video_id: str, title: str) -> str:
    """Format transcript snippets as markdown with clickable YouTube timestamps."""
    lines = [
        f"# {title}",
        f"**Video:** https://youtube.com/watch?v={video_id}",
        "",
        "---",
        "",
    ]
    chunk_duration = 30
    current_chunk_start = 0
    current_texts = []

    for snippet in snippets:
        start = snippet.start
        if start >= current_chunk_start + chunk_duration and current_texts:
            ts = format_timestamp(current_chunk_start)
            t = int(current_chunk_start)
            lines.append(f"**[{ts}](https://youtube.com/watch?v={video_id}&t={t})**")
            lines.append(" ".join(current_texts))
            lines.append("")
            current_chunk_start = start
            current_texts = []
        current_texts.append(snippet.text)

    if current_texts:
        ts = format_timestamp(current_chunk_start)
        t = int(current_chunk_start)
        lines.append(f"**[{ts}](https://youtube.com/watch?v={video_id}&t={t})**")
        lines.append(" ".join(current_texts))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Local video helpers
# ---------------------------------------------------------------------------

def is_local_video(arg: str) -> bool:
    """Return True if arg looks like a local media file path (video or audio)."""
    p = Path(arg)
    return p.suffix.lower() in MEDIA_EXTENSIONS and (p.exists() or p.is_absolute())


def transcribe_local_video(video_path: Path) -> list:
    """
    Run Whisper on a local video file and return snippet-like objects
    compatible with the YouTube transcript format.
    """
    if not WHISPER_CMD:
        raise RuntimeError(
            "Whisper not found. Install with: pip3 install openai-whisper"
        )

    print(f"Transcribing {video_path.name} with Whisper ({WHISPER_MODEL})…", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            [
                *WHISPER_CMD,
                str(video_path),
                "--model", WHISPER_MODEL,
                "--language", "en",
                "--output_dir", tmpdir,
                "--output_format", "json",
                "--fp16", "False",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # turbo may not be available; retry with base
            print(f"Warning: {WHISPER_MODEL} model failed, retrying with base…", file=sys.stderr)
            result = subprocess.run(
                [
                    *WHISPER_CMD,
                    str(video_path),
                    "--model", "base",
                    "--language", "en",
                    "--output_dir", tmpdir,
                    "--output_format", "json",
                    "--fp16", "False",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

        json_files = list(Path(tmpdir).glob("*.json"))
        if not json_files:
            raise RuntimeError("Whisper produced no JSON output")
        data = json.loads(json_files[0].read_text(encoding="utf-8"))

    snippets = []
    for segment in data.get("segments", []):
        text = segment.get("text", "").strip()
        if text:
            snippets.append(SimpleNamespace(start=float(segment["start"]), text=text))

    return snippets


def format_local_as_markdown(snippets, title: str, source_path: Path) -> str:
    """Format local Whisper snippets as markdown with plain timestamps."""
    lines = [
        f"# {title}",
        f"**Source:** {source_path}",
        "",
        "---",
        "",
    ]
    chunk_duration = 30
    current_chunk_start = 0.0
    current_texts = []

    for snippet in snippets:
        start = snippet.start
        if start >= current_chunk_start + chunk_duration and current_texts:
            lines.append(f"**[{format_timestamp(current_chunk_start)}]**")
            lines.append(" ".join(current_texts))
            lines.append("")
            current_chunk_start = start
            current_texts = []
        current_texts.append(snippet.text)

    if current_texts:
        lines.append(f"**[{format_timestamp(current_chunk_start)}]**")
        lines.append(" ".join(current_texts))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def slugify(text: str, fallback: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"[^\w\s-]", "", lowered)
    lowered = re.sub(r"[\s_]+", "-", lowered).strip("-")
    return lowered[:80] or fallback


def build_transcript_path(title: str, slug_fallback: str) -> Path:
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(title, slug_fallback)
    base = f"{date.today().isoformat()}-{slug}"
    path = TRANSCRIPTS_DIR / f"{base}.md"
    version = 2
    while path.exists():
        path = TRANSCRIPTS_DIR / f"{base}-v{version}.md"
        version += 1
    return path


def save_transcript(markdown: str, title: str, slug_fallback: str) -> Path:
    path = build_transcript_path(title, slug_fallback)
    path.write_text(markdown, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Fetch a transcript from YouTube or transcribe a local video file."
    )
    parser.add_argument("source", help="YouTube URL/video-ID or local video file path")
    parser.add_argument("--title", help="Override title (useful for local files)")
    args = parser.parse_args()

    source = args.source

    # ---- Local video file ----
    if is_local_video(source):
        video_path = Path(source).resolve()
        if not video_path.exists():
            print(f"Error: file not found: {video_path}", file=sys.stderr)
            sys.exit(1)

        title = args.title or video_path.stem

        try:
            snippets = transcribe_local_video(video_path)
        except Exception as e:
            print(f"Error transcribing video: {e}", file=sys.stderr)
            sys.exit(1)

        markdown = format_local_as_markdown(snippets, title, video_path)
        transcript_path = save_transcript(markdown, title, video_path.stem)

        # Report the saved path unconditionally — on satellites indexing
        # correctly refuses, and the caller still needs the file location.
        print(f"Saved transcript: {transcript_path}", file=sys.stderr)
        try:
            index_transcript_file(
                transcript_path,
                title=title,
                source_url=str(video_path),
                video_id=None,
            )
        except Exception as exc:
            print(f"Warning: transcript saved but could not be indexed ({exc}).", file=sys.stderr)

        try:
            build_transcript_extract_artifact(
                transcript_path,
                title=title,
                source_url=str(video_path),
            )
            print("Saved structured transcript extract.", file=sys.stderr)
        except Exception as exc:
            print(f"Warning: transcript extract could not be created ({exc}).", file=sys.stderr)

        print(markdown)
        return

    # ---- YouTube URL / video ID ----
    try:
        video_id = extract_video_id(source)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        snippets = fetch_transcript(video_id)
    except Exception as e:
        print(f"Error fetching transcript: {e}", file=sys.stderr)
        sys.exit(1)

    title = args.title or f"YouTube Transcript {video_id}"
    try:
        title = args.title or fetch_video_title(video_id)
    except (URLError, ValueError, TimeoutError) as exc:
        print(f"Warning: could not fetch video title ({exc}); using fallback title.", file=sys.stderr)
    except Exception as exc:
        print(f"Warning: unexpected title lookup issue ({exc}); using fallback title.", file=sys.stderr)

    markdown = format_youtube_as_markdown(snippets, video_id, title)
    transcript_path = save_transcript(markdown, title, video_id)

    # Report the saved path unconditionally — on satellites indexing
    # correctly refuses, and the caller still needs the file location.
    print(f"Saved transcript: {transcript_path}", file=sys.stderr)
    try:
        index_transcript_file(
            transcript_path,
            title=title,
            source_url=canonical_video_url(video_id),
            video_id=video_id,
        )
    except Exception as exc:
        print(f"Warning: transcript saved but could not be indexed ({exc}).", file=sys.stderr)

    try:
        build_transcript_extract_artifact(
            transcript_path,
            title=title,
            source_url=canonical_video_url(video_id),
        )
        print("Saved structured transcript extract.", file=sys.stderr)
    except Exception as exc:
        print(f"Warning: transcript extract could not be created ({exc}).", file=sys.stderr)

    print(markdown)


if __name__ == "__main__":
    main()
