#!/usr/bin/env python3
"""
Transcribe a local audio/video file using OpenAI Whisper and save it to
owners-inbox/transcripts/ in the same format as fetch-transcript.py.

Usage:
    python3 transcribe.py <file> [title] [--model base|small|medium|large]

Examples:
    python3 transcribe.py team-inbox/Gabe.mov
    python3 transcribe.py team-inbox/Gabe.mov "Gabe Interview" --model small
"""

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = PKA_ROOT / "owners-inbox" / "transcripts"

# Try to import whisper module; fall back to subprocess
try:
    import whisper as _whisper
    HAS_WHISPER_MODULE = True
except ImportError:
    HAS_WHISPER_MODULE = False


def slugify(text: str, fallback: str = "transcript") -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"[^\w\s-]", "", lowered)
    lowered = re.sub(r"[\s_]+", "-", lowered).strip("-")
    return lowered[:80] or fallback


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def transcribe_with_module(file_path: str, model_name: str = "base") -> list:
    """Use openai-whisper Python module directly."""
    model = _whisper.load_model(model_name)
    result = model.transcribe(file_path, verbose=False)
    return result.get("segments", [])


def transcribe_with_subprocess(file_path: str, model_name: str = "base") -> list:
    """Fall back to calling the whisper CLI and parsing JSON output."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        result = subprocess.run(
            ["whisper", file_path, "--model", model_name,
             "--output_format", "json", "--output_dir", tmpdir],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"whisper subprocess failed:\n{result.stderr}")
        stem = Path(file_path).stem
        json_file = Path(tmpdir) / f"{stem}.json"
        if not json_file.exists():
            candidates = list(Path(tmpdir).glob("*.json"))
            if not candidates:
                raise RuntimeError("No JSON output found from whisper")
            json_file = candidates[0]
        data = json.loads(json_file.read_text())
        return data.get("segments", [])


def format_as_markdown(segments: list, title: str, source_path: str) -> str:
    lines = [
        f"# {title}",
        f"**Source:** {source_path}",
        "",
        "---",
        "",
    ]

    chunk_duration = 30
    current_chunk_start = None
    current_texts = []

    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if not text:
            continue

        if current_chunk_start is None:
            current_chunk_start = start

        if start >= current_chunk_start + chunk_duration and current_texts:
            ts = format_timestamp(current_chunk_start)
            lines.append(f"**[{ts}]**")
            lines.append(" ".join(current_texts))
            lines.append("")
            current_chunk_start = start
            current_texts = []

        current_texts.append(text)

    if current_texts:
        ts = format_timestamp(current_chunk_start or 0)
        lines.append(f"**[{ts}]**")
        lines.append(" ".join(current_texts))
        lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: python3 transcribe.py <file> [title] [--model base|small|medium|large]",
            file=sys.stderr,
        )
        sys.exit(1)

    file_path = sys.argv[1]
    if not Path(file_path).exists():
        # Try relative to PKA root
        alt = PKA_ROOT / file_path
        if alt.exists():
            file_path = str(alt)
        else:
            print(f"Error: file not found: {file_path}", file=sys.stderr)
            sys.exit(1)

    title = None
    model_name = "base"
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--model" and i + 1 < len(args):
            model_name = args[i + 1]
            i += 2
        elif not title and not args[i].startswith("--"):
            title = args[i]
            i += 1
        else:
            i += 1

    if not title:
        stem = Path(file_path).stem
        title = stem.replace("-", " ").replace("_", " ").title()

    print(f"Transcribing: {file_path}", file=sys.stderr)
    print(f"Title: {title}", file=sys.stderr)
    print(f"Model: {model_name}", file=sys.stderr)

    if HAS_WHISPER_MODULE:
        print("Using whisper Python module...", file=sys.stderr)
        segments = transcribe_with_module(file_path, model_name)
    else:
        print("whisper module not in venv — trying subprocess...", file=sys.stderr)
        segments = transcribe_with_subprocess(file_path, model_name)

    markdown = format_as_markdown(segments, title, file_path)

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify(title, Path(file_path).stem)
    base = f"{date.today().isoformat()}-{slug}"
    out_path = TRANSCRIPTS_DIR / f"{base}.md"
    version = 2
    while out_path.exists():
        out_path = TRANSCRIPTS_DIR / f"{base}-v{version}.md"
        version += 1

    out_path.write_text(markdown, encoding="utf-8")
    print(f"Saved: {out_path}", file=sys.stderr)

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from pka_index import index_markdown_artifact
        stem = Path(file_path).stem
        index_markdown_artifact(
            out_path,
            title=f"{title} — Local Transcript",
            category="transcript",
            tags=["transcript", "local", stem],
            summary=f"Local audio/video transcript for: {title}. Source: {file_path}",
        )
        print("Indexed in PKA.", file=sys.stderr)
    except Exception as exc:
        print(f"Warning: saved but could not index ({exc}).", file=sys.stderr)

    print(markdown)


if __name__ == "__main__":
    main()
