#!/usr/bin/env python3
"""Watch a video (URL or local file) and capture a frame-aware brief into PKA.

This is PKA's wrapper around scene-change frame extraction. It downloads a
video, samples frames *on shot changes* (not on a timer), pulls the transcript
from free captions (Whisper-turbo fallback), and writes a durable markdown
brief to ``owners-inbox/transcripts/watched-<slug>.md`` that is indexed through
``pka_index`` like every other PKA deliverable.

It deliberately uses ONLY PKA's durable spine: files, the markdown brief, and
the SQLite index. It does not install any plugin, SessionStart hook, or
``obsidian://`` write path — those belong to the upstream skill, not PKA.

Scene-detection / hero-frame logic adapted from ``taoufik123-collab/claude-watch``
(MIT licensed, © Bradley Bonanno's original ``claude-video``), reviewed and
pinned at commit 7871c7e. Only the stdlib-only frame functions were lifted;
transcript + indexing run through PKA's own tooling.

Usage:
    watch_video_capture.py <url-or-path> [--for maven|reed|wiki|hyperframes]
        [--title T] [--start T] [--end T] [--max-frames N] [--resolution W]
        [--scene-threshold F] [--keep-video] [--no-index]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


PKA_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = PKA_ROOT / "owners-inbox" / "transcripts"
VENV_PYTHON = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"
VENV_YT_DLP = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "yt-dlp"
ATTRIBUTION_PIN = "7871c7e"

# Whisper binary: prefer system PATH, fall back to discord-bridge venv, then
# top-level venv (mirrors tools/fetch-transcript.py so we share one transcription contract).
WHISPER_BIN = (
    shutil.which("whisper")
    or (str(PKA_ROOT / "discord-bridge" / "venv" / "bin" / "whisper")
        if (PKA_ROOT / "discord-bridge" / "venv" / "bin" / "whisper").exists() else None)
    or (str(PKA_ROOT / "venv" / "bin" / "whisper")
        if (PKA_ROOT / "venv" / "bin" / "whisper").exists() else None)
)
WHISPER_MODEL = "turbo"

MAX_FPS = 2.0
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}

# Per-consumer tuning. Category stays `transcript` (the artifact IS a watched
# brief); the downstream consumer is encoded in tags so the health digest and
# Dreamer can route it. `focus_hook` denses up the first 10s for thumbnail /
# hook work.
MODE_CONFIG: dict[str, dict] = {
    "general": {"resolution": 512, "tags": ["video-watch", "scene-frames"]},
    "maven": {"resolution": 768, "focus_hook": True,
              "tags": ["video-watch", "scene-frames", "for-maven", "thumbnail-grounding"]},
    "reed": {"resolution": 640,
             "tags": ["video-watch", "scene-frames", "for-reed", "external-talk"]},
    "wiki": {"resolution": 640,
             "tags": ["video-watch", "scene-frames", "for-wiki", "compiled-source"]},
    "hyperframes": {"resolution": 768,
                    "tags": ["video-watch", "scene-frames", "for-hyperframes",
                             "anchor-candidates"]},
}


# ---------------------------------------------------------------------------
# Lifted frame engine (stdlib-only) — claude-watch @ 7871c7e, MIT
# ---------------------------------------------------------------------------

def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise SystemExit(f"{binary} is not installed. Install with: brew install {binary}")


def get_metadata(video_path: str) -> dict:
    _require("ffprobe")
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(Path(video_path).resolve())],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or vstream.get("duration") or 0)
    return {
        "duration_seconds": duration,
        "width": vstream.get("width"),
        "height": vstream.get("height"),
        "has_audio": astream is not None,
    }


def _clamp_fps(fps: float, duration: float, max_frames: int) -> tuple[float, int]:
    fps = min(fps, MAX_FPS)
    return fps, min(max_frames, max(1, int(round(fps * duration))))


def auto_fps(duration: float, max_frames: int = 100) -> tuple[float, int]:
    if duration <= 0:
        return 1.0, 1
    if duration <= 30:
        target = min(max_frames, max(12, int(round(duration))))
    elif duration <= 60:
        target = min(max_frames, 40)
    elif duration <= 180:
        target = min(max_frames, 60)
    elif duration <= 600:
        target = min(max_frames, 80)
    else:
        target = max_frames
    return _clamp_fps(target / duration, duration, max_frames)


def _extract_uniform(video_path: str, out_dir: Path, fps: float,
                     resolution: int, max_frames: int,
                     start: float | None, end: float | None) -> list[dict]:
    _require("ffmpeg")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(Path(video_path).resolve()),
            "-vf", f"fps={fps},scale={resolution}:-2",
            "-frames:v", str(max_frames), "-q:v", "4",
            str(out_dir / "frame_%04d.jpg")]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg frame extraction failed: {result.stderr.strip()}")
    offset = start or 0.0
    frames = sorted(out_dir.glob("frame_*.jpg"))
    return [{"index": i, "timestamp_seconds": round(offset + (i / fps if fps > 0 else 0.0), 2),
             "path": str(p), "source": "uniform"} for i, p in enumerate(frames)]


def extract_scene_change(video_path: str, out_dir: Path, *, scene_threshold: float = 0.3,
                         resolution: int = 512, max_frames: int = 100,
                         uniform_fallback_min: int = 10,
                         start: float | None = None, end: float | None = None) -> list[dict]:
    """One frame per detected shot via ffmpeg ``select='gt(scene,T)'``.

    0.3 is a permissive cut detector (hard cuts + most dissolves, not motion).
    Always emits frame 0 (the filter only fires on *changes*). Static videos
    (screen recordings, talking heads) yield too few cuts and fall back to
    uniform sampling — sparse frames beat almost no frames.
    """
    _require("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    for existing in out_dir.glob("frame_*.jpg"):
        existing.unlink()

    select_expr = f"eq(n\\,0)+gt(scene\\,{scene_threshold})"
    vf = f"select='{select_expr}',metadata=mode=print:file=-,scale={resolution}:-2"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", str(Path(video_path).resolve()), "-vf", vf, "-vsync", "vfr",
            "-frames:v", str(max_frames), "-q:v", "4", str(out_dir / "frame_%04d.jpg")]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg scene-change extraction failed: {result.stderr.strip()}")

    pts_times: list[float] = []
    for stream in (result.stdout, result.stderr):
        for line in stream.splitlines():
            if "pts_time" in line:
                for tok in line.split():
                    for sep in (":", "="):
                        if tok.startswith(f"pts_time{sep}"):
                            try:
                                pts_times.append(float(tok.split(sep, 1)[1]))
                            except ValueError:
                                pass

    frames = sorted(out_dir.glob("frame_*.jpg"))
    if len(frames) < uniform_fallback_min:
        for f in frames:
            f.unlink()
        meta = get_metadata(video_path)
        eff_start = start or 0.0
        eff_end = end if end is not None else meta["duration_seconds"]
        fps, _ = auto_fps(max(0.1, eff_end - eff_start), max_frames=max_frames)
        return _extract_uniform(video_path, out_dir, fps, resolution, max_frames, start, end)

    offset = start or 0.0
    if len(pts_times) < len(frames):
        pts_times += [0.0] * (len(frames) - len(pts_times))
    return [{"index": i, "timestamp_seconds": round(offset + pts_times[i], 2),
             "path": str(p), "source": "scene-change"} for i, p in enumerate(frames)]


def select_hero_frames(frames: list[dict], *, hook_end: float = 10.0,
                       max_hero: int = 5, min_hero: int = 3) -> list[dict]:
    """Pick 3-5 deterministic 'hero' frames to embed in the brief."""
    if not frames:
        return []
    chosen: list[int] = []

    def _add(idx: int) -> None:
        if 0 <= idx < len(frames) and idx not in chosen:
            chosen.append(idx)

    for i, f in enumerate(frames):
        if f["timestamp_seconds"] <= hook_end:
            _add(i)
            break
    if len(chosen) < min_hero:
        gap = max(1, len(frames) // max_hero)
        for i in range(0, len(frames), gap):
            _add(i)
            if len(chosen) >= max_hero:
                break
    return [frames[i] for i in sorted(set(chosen))[:max_hero]]


# ---------------------------------------------------------------------------
# Source resolution + transcript (PKA-native)
# ---------------------------------------------------------------------------

def is_url(source: str) -> bool:
    if source.startswith("-"):
        return False
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def download_url(url: str, work_dir: Path) -> dict:
    yt_dlp_bin = str(VENV_YT_DLP) if VENV_YT_DLP.exists() else "yt-dlp"
    if yt_dlp_bin == "yt-dlp":
        _require("yt-dlp")
    cmd = [yt_dlp_bin, "-N", "8",
           "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
           "--merge-output-format", "mp4",
           "--write-info-json", "--write-subs", "--write-auto-subs",
           "--sub-langs", "en,en-US,en-GB,en-orig", "--sub-format", "vtt",
           "--convert-subs", "vtt", "--no-playlist", "--ignore-errors",
           "-o", str(work_dir / "video.%(ext)s"), "--", url]
    subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
    video = next((p for ext in (".mp4", ".mkv", ".webm", ".mov")
                  for p in work_dir.glob(f"video*{ext}")), None)
    if video is None:
        raise SystemExit(f"yt-dlp did not produce a video file in {work_dir}")
    subs = sorted(work_dir.glob("video*.vtt"))
    subtitle = next((c for c in subs if ".en" in c.name), subs[0] if subs else None)
    info: dict = {"url": url}
    info_json = work_dir / "video.info.json"
    if info_json.exists():
        try:
            raw = json.loads(info_json.read_text(encoding="utf-8"))
            info = {"title": raw.get("title"),
                    "uploader": raw.get("uploader") or raw.get("channel"),
                    "url": raw.get("webpage_url") or url}
        except Exception:
            pass
    return {"video_path": str(video), "subtitle_path": str(subtitle) if subtitle else None,
            "info": info}


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    return {"video_path": str(p), "subtitle_path": None,
            "info": {"title": p.stem, "url": str(p)}}


def _parse_vtt(vtt_path: Path) -> list[dict]:
    """Parse a VTT into [{start_seconds, text}], collapsing rolling-caption dupes.

    Handles both HH:MM:SS.mmm and MM:SS.mmm timestamp formats (Whisper outputs
    the shorter form for videos under one hour).
    """
    cues: list[dict] = []
    # Matches HH:MM:SS.mmm or MM:SS.mmm (with . or , as decimal separator)
    ts_re = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->")
    cur_start: float | None = None
    cur_lines: list[str] = []

    def _flush():
        nonlocal cur_start, cur_lines
        if cur_start is not None and cur_lines:
            text = " ".join(cur_lines).strip()
            if text and (not cues or cues[-1]["text"] != text):
                cues.append({"start_seconds": cur_start, "text": text})
        cur_start, cur_lines = None, []

    for raw in vtt_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        m = ts_re.match(line)
        if m:
            _flush()
            h_str, mm_str, ss_str, ms_str = m.groups()
            h = int(h_str) if h_str is not None else 0
            mm, ss, ms = int(mm_str), int(ss_str), int(ms_str)
            cur_start = h * 3600 + mm * 60 + ss + ms / 1000.0
        elif line and not line.isdigit() and line != "WEBVTT" and "-->" not in line:
            clean = re.sub(r"<[^>]+>", "", line)
            if clean and clean not in cur_lines:
                cur_lines.append(clean)
    _flush()
    return cues


def _whisper_fallback(video_path: str, work_dir: Path) -> list[dict]:
    """Transcribe locally with Whisper-turbo when no captions exist. Best-effort."""
    if not WHISPER_BIN:
        return []
    try:
        subprocess.run([WHISPER_BIN, video_path, "--model", WHISPER_MODEL,
                        "--output_format", "vtt", "--output_dir", str(work_dir)],
                       stdout=sys.stderr, stderr=sys.stderr, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"[watch] Whisper fallback failed ({exc}); proceeding frames-only.",
              file=sys.stderr)
        return []
    produced = sorted(work_dir.glob("*.vtt"))
    return _parse_vtt(produced[0]) if produced else []


def get_transcript(source: dict, work_dir: Path, *, allow_whisper: bool = True) -> tuple[list[dict], str]:
    sub = source.get("subtitle_path")
    if sub and Path(sub).exists():
        cues = _parse_vtt(Path(sub))
        if cues:
            return cues, "captions"
    if allow_whisper:
        cues = _whisper_fallback(source["video_path"], work_dir)
        if cues:
            return cues, "whisper-turbo"
    return [], "none"


# ---------------------------------------------------------------------------
# Brief assembly
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "video")[:60]


def _fmt_ts(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _nearest_cue(cues: list[dict], ts: float) -> str:
    if not cues:
        return ""
    best = min(cues, key=lambda c: abs(c["start_seconds"] - ts))
    return best["text"]


def build_brief(*, title: str, mode: str, source_url: str, meta: dict,
                frames: list[dict], hero: list[dict], cues: list[dict],
                transcript_kind: str, frames_rel: str) -> str:
    cfg = MODE_CONFIG[mode]
    scene_count = sum(1 for f in frames if f.get("source") == "scene-change")
    frame_kind = "scene-change" if scene_count else "uniform-sampled"
    lines: list[str] = []
    lines.append(f"# Watched: {title}\n")
    lines.append(f"- **Source:** {source_url}")
    lines.append(f"- **Duration:** {_fmt_ts(meta['duration_seconds'])}")
    lines.append(f"- **Captured:** {date.today().isoformat()} · `watch_video_capture` ({mode} mode)")
    lines.append(f"- **Frames:** {len(frames)} {frame_kind} · {len(hero)} hero frames embedded")
    lines.append(f"- **Transcript:** {transcript_kind}")
    lines.append("")
    lines.append(f"> Scene-detection engine lifted from taoufik123-collab/claude-watch "
                 f"(MIT, © Bradley Bonanno), pinned `{ATTRIBUTION_PIN}`. "
                 f"Output routed through PKA's index — no plugin/hook/Obsidian path used.\n")

    lines.append("## Key Frames\n")
    for f in hero:
        cap = _nearest_cue(cues, f["timestamp_seconds"]) if cues else ""
        cap = (f" — {cap[:80]}" if cap else "")
        rel = f"{frames_rel}/{Path(f['path']).name}"
        lines.append(f"**[{_fmt_ts(f['timestamp_seconds'])}]**{cap}\n")
        lines.append(f"![frame at {_fmt_ts(f['timestamp_seconds'])}]({rel})\n")

    # Mode-specific section
    if mode == "maven":
        lines.append("## Hook & Visual Beats (0:00–0:10)\n")
        hook = [f for f in frames if f["timestamp_seconds"] <= 10.0][:6]
        for f in hook:
            rel = f"{frames_rel}/{Path(f['path']).name}"
            lines.append(f"- **[{_fmt_ts(f['timestamp_seconds'])}]** "
                         f"{_nearest_cue(cues, f['timestamp_seconds'])[:90]} · `{rel}`")
        lines.append("\n_Hero frames double as thumbnail-grounding references for "
                     "`generate_thumbnail.py` (gpt-image-1)._\n")
    elif mode == "hyperframes":
        lines.append("## Anchor Candidates\n")
        lines.append("Verbatim cue text nearest each shot change — anchor *candidates*, "
                     "2–4 word fragments to be hand-trimmed.\n")
        lines.append("| Timestamp | Nearest verbatim cue | Frame |")
        lines.append("| --- | --- | --- |")
        for f in frames:
            rel = f"{frames_rel}/{Path(f['path']).name}"
            cue = _nearest_cue(cues, f["timestamp_seconds"]).replace("|", "/")
            lines.append(f"| {_fmt_ts(f['timestamp_seconds'])} | {cue[:70]} | `{rel}` |")
        lines.append("\n> ⚠️ VTT is segment-level. HyperFrames *rendering* still needs "
                     "word-level Whisper from the existing local pipeline — these are "
                     "anchor-candidate suggestions only.\n")
    elif mode in ("reed", "wiki"):
        label = ("Visual Moments for Narrative" if mode == "reed"
                 else "Concepts & Source Frames (for Dreamer wiki compile)")
        lines.append(f"## {label}\n")
        for f in hero:
            rel = f"{frames_rel}/{Path(f['path']).name}"
            lines.append(f"- **[{_fmt_ts(f['timestamp_seconds'])}]** "
                         f"{_nearest_cue(cues, f['timestamp_seconds'])[:110]} · `{rel}`")
        lines.append("")

    if cues:
        lines.append("## Transcript\n")
        for c in cues:
            lines.append(f"**[{_fmt_ts(c['start_seconds'])}]** {c['text']}")
        lines.append("")
    else:
        lines.append("## Transcript\n\n_No captions available and Whisper fallback "
                     "did not run — this is a frames-only capture._\n")

    lines.append(f"## Full Frame Index\n\nAll {len(frames)} frames in `{frames_rel}/`.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Indexing (PKA canonical path; satellite-safe; best-effort)
# ---------------------------------------------------------------------------

def index_brief(path: Path, *, title: str, mode: str, source_url: str) -> None:
    interp = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    tags = ",".join(MODE_CONFIG[mode]["tags"])
    cmd = [interp, str(PKA_ROOT / "tools" / "pka_index.py"), "index-markdown",
           "--file", str(path), "--category", "transcript", "--title", title,
           "--tags", tags, "--source-url", source_url,
           "--summary", f"Frame-aware video capture ({mode} mode): {title}"]
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"[watch] Brief saved but could not be indexed ({exc}).", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Watch a video and capture a frame-aware PKA brief.")
    parser.add_argument("source", help="Video URL or local file path")
    parser.add_argument("--for", dest="mode", default="general",
                        choices=sorted(MODE_CONFIG), help="Downstream consumer profile")
    parser.add_argument("--title", help="Override title")
    parser.add_argument("--start", help="Focus start (SS, MM:SS, HH:MM:SS)")
    parser.add_argument("--end", help="Focus end (SS, MM:SS, HH:MM:SS)")
    parser.add_argument("--max-frames", type=int, default=100)
    parser.add_argument("--resolution", type=int, help="Frame width override")
    parser.add_argument("--scene-threshold", type=float, default=0.3)
    parser.add_argument("--keep-video", action="store_true", help="Keep the downloaded video")
    parser.add_argument("--no-whisper", action="store_true",
                        help="Captions only — skip the local Whisper fallback")
    parser.add_argument("--no-index", action="store_true", help="Skip pka_index")
    args = parser.parse_args()

    cfg = MODE_CONFIG[args.mode]
    resolution = args.resolution or cfg["resolution"]

    def _parse_t(v):
        if not v:
            return None
        parts = str(v).split(":")
        parts = [float(p) for p in parts]
        return sum(p * 60 ** (len(parts) - 1 - i) for i, p in enumerate(parts))

    start = _parse_t(args.start)
    end = _parse_t(args.end)

    work_dir = Path(tempfile.mkdtemp(prefix="pka-watch-"))
    try:
        source = (download_url(args.source, work_dir) if is_url(args.source)
                  else resolve_local(args.source))
        meta = get_metadata(source["video_path"])
        title = args.title or source["info"].get("title") or Path(source["video_path"]).stem
        slug = _slugify(title)

        frames_dir = TRANSCRIPTS_DIR / f"watched-{slug}" / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        frames = extract_scene_change(
            source["video_path"], frames_dir,
            scene_threshold=args.scene_threshold, resolution=resolution,
            max_frames=args.max_frames, start=start, end=end)
        hero = select_hero_frames(frames)
        cues, transcript_kind = get_transcript(source, work_dir, allow_whisper=not args.no_whisper)

        brief = build_brief(
            title=title, mode=args.mode, source_url=source["info"].get("url", args.source),
            meta=meta, frames=frames, hero=hero, cues=cues,
            transcript_kind=transcript_kind, frames_rel=f"watched-{slug}/frames")

        brief_path = TRANSCRIPTS_DIR / f"watched-{slug}.md"
        brief_path.write_text(brief, encoding="utf-8")
        print(f"Saved brief: {brief_path}", file=sys.stderr)
        print(f"  {len(frames)} frames ({transcript_kind} transcript, {len(cues)} cues) "
              f"→ {frames_dir}", file=sys.stderr)

        if not args.no_index:
            index_brief(brief_path, title=title, mode=args.mode,
                        source_url=source["info"].get("url", args.source))

        print(str(brief_path))
        return 0
    finally:
        if not args.keep_video:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
