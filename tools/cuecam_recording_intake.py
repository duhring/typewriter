#!/usr/bin/env python3
"""
Track and clean future CueCam Presenter recordings.

This is the first step of the one-command video pipeline: watch the standard
CueCam recordings folder, ignore the existing archive after baseline init, and
send new stable recordings through tools/clean_video.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse


PKA_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECORDINGS_DIR = Path(os.environ.get("PKA_RECORDINGS_DIR", "~/Movies/CueCam Presenter Recordings")).expanduser()
STATE_PATH = PKA_ROOT / "data" / "cuecam_recording_intake.json"
PRESENTATIONS_DIR = PKA_ROOT / "owners-inbox" / "presentations"
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
MIN_DURATION_SECONDS = 60  # files shorter than this are audio-sync tests, not content
DEFAULT_SIDECAR_WINDOW_DAYS = 7
HYPERFRAMES_NEXT_STEP = (
    "See docs/larry/cuecam.md → 'HyperFrames Render Workflow'. "
    "Run the Codex video-studio render with the three artifacts."
)
TRANSCRIPT_READY_NEXT_STEP = (
    "See docs/larry/cuecam.md → 'Post-Production HyperFrames Analysis Flow'. "
    "Run Larry post-production analysis to map overlays from the transcript."
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_key(path: Path) -> str:
    return str(path.resolve())


def is_cleaned_output(path: Path) -> bool:
    return path.stem.endswith("_cleaned")


def iter_recordings(recordings_dir: Path) -> list[Path]:
    if not recordings_dir.exists():
        raise FileNotFoundError(f"Recordings folder not found: {recordings_dir}")
    files = [
        path
        for path in recordings_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTENSIONS
        and not is_cleaned_output(path)
    ]
    return sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "version": 1,
            "created_at": utc_now(),
            "recordings_dir": str(DEFAULT_RECORDINGS_DIR),
            "known": {},
            "processed": {},
        }
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get_video_duration(path: Path) -> float | None:
    """Return video duration in seconds via ffprobe, or None if unavailable."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return None


def probe_display_matrix_rotation(path: Path) -> int | None:
    """
    Return the Display Matrix rotation value from a video's side data, or None.

    ffprobe reports this as an integer degrees value (e.g. -180, 90, -90).
    A value of -180 means the clip was shot on the front (selfie) camera and
    the pixels are upside-down relative to what the viewer expects.
    A missing or 0 value means the pixels are already correct.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries", "stream_side_data=rotation",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return None
        for side_data in streams[0].get("side_data_list", []):
            if "rotation" in side_data:
                return int(side_data["rotation"])
    except (subprocess.TimeoutExpired, ValueError, KeyError, json.JSONDecodeError, FileNotFoundError):
        pass
    return None


def normalize_clip_rotation(src: Path, dest: Path) -> dict:
    """
    Probe src for a Display Matrix rotation tag. Apply corrections per the rule:
      - rotation == -180 → re-encode with hflip,vflip filter + clear rotation metadata
      - any other value  → re-encode with no filter, clear rotation metadata only
      - no tag / 0       → re-encode with no filter (ensures clean mp4 for concat)

    Always writes a clean MP4 to dest. Returns a dict describing what was done.
    """
    rotation = probe_display_matrix_rotation(src)
    print(f"  [{src.name}] Display Matrix rotation: {rotation}", file=sys.stderr)

    vf_filter = "hflip,vflip" if rotation == -180 else "null"
    action = "hflip+vflip (front-camera -180°)" if rotation == -180 else "passthrough (no rotation needed)"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", vf_filter,
        "-metadata:s:v:0", "rotate=0",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(dest),
    ]
    print(f"  [{src.name}] Normalizing rotation → {dest.name} ({action})...", file=sys.stderr)
    subprocess.run(cmd, check=True, capture_output=False)
    return {
        "source": str(src),
        "dest": str(dest),
        "detected_rotation": rotation,
        "action": action,
    }


def file_record(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "filename": path.name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "mtime_iso": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
    }


def find_candidates(
    state: dict[str, Any],
    recordings_dir: Path,
    *,
    min_age_seconds: int,
    min_duration_seconds: int = MIN_DURATION_SECONDS,
    include_known: bool = False,
) -> tuple[list[Path], list[dict[str, Any]]]:
    """Return (candidates, skipped_short) where skipped_short are files under min_duration_seconds."""
    now_ts = datetime.now().timestamp()
    known = state.get("known", {})
    processed = state.get("processed", {})
    skipped_keys = {r["path"] for r in state.get("skipped_short", [])}
    candidates = []
    skipped_short = []
    for path in iter_recordings(recordings_dir):
        key = file_key(path)
        if key in processed:
            continue
        if key in skipped_keys:
            continue
        age = now_ts - path.stat().st_mtime
        if age < min_age_seconds:
            continue
        if not include_known and key in known:
            continue
        duration = get_video_duration(path)
        if duration is not None and duration < min_duration_seconds:
            skipped_short.append({**file_record(path), "duration_seconds": round(duration, 1)})
            continue
        candidates.append(path)
    return candidates, skipped_short


def cmd_init(args: argparse.Namespace) -> int:
    recordings_dir = Path(args.recordings_dir).expanduser()
    state = load_state()
    state["recordings_dir"] = str(recordings_dir)
    state["baseline_at"] = utc_now()
    known = state.setdefault("known", {})
    added = 0
    for path in iter_recordings(recordings_dir):
        key = file_key(path)
        if key not in known:
            known[key] = file_record(path)
            added += 1
    save_state(state)
    print(json.dumps({"status": "ok", "baseline_added": added, "known_total": len(known), "state": str(STATE_PATH)}, indent=2))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    recordings_dir = Path(args.recordings_dir).expanduser()
    state = load_state()
    candidates, skipped_short = find_candidates(
        state,
        recordings_dir,
        min_age_seconds=args.min_age_seconds,
        min_duration_seconds=args.min_duration_seconds,
        include_known=args.include_known,
    )
    payload = {
        "status": "ok",
        "candidate_count": len(candidates),
        "candidates": [file_record(path) for path in candidates[: args.limit]],
        "skipped_short_count": len(skipped_short),
        "skipped_short": skipped_short,
        "state": str(STATE_PATH),
    }
    print(json.dumps(payload, indent=2))
    return 0


def cleaned_output_path(path: Path, output_dir: Path | None) -> Path:
    filename = f"{path.stem}_cleaned{path.suffix}"
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / filename
    return path.with_name(filename)


def words_export_path(cleaned_path: Path) -> Path:
    return cleaned_path.with_name(f"{cleaned_path.stem}.words.json")


def find_sidecar_candidates(recording_path: Path, window_days: int) -> list[Path]:
    """Return *.hyperframes.md sidecars whose mtime is within ±window_days of recording mtime,
    sorted by closeness in time (smallest delta first)."""
    if not PRESENTATIONS_DIR.exists():
        return []
    try:
        rec_mtime = recording_path.stat().st_mtime
    except FileNotFoundError:
        return []
    window_seconds = window_days * 86400
    candidates: list[Path] = []
    for sidecar in PRESENTATIONS_DIR.glob("*.hyperframes.md"):
        try:
            delta = abs(sidecar.stat().st_mtime - rec_mtime)
        except FileNotFoundError:
            continue
        if delta <= window_seconds:
            candidates.append(sidecar)
    return sorted(candidates, key=lambda p: abs(p.stat().st_mtime - rec_mtime))


def run_clean_video(
    path: Path,
    output_path: Path,
    args: argparse.Namespace,
    export_words: Path | None = None,
) -> None:
    cmd = [
        sys.executable,
        str(PKA_ROOT / "tools" / "clean_video.py"),
        str(path),
        "--output",
        str(output_path),
        "--model",
        args.model,
        "--silence-db",
        str(args.silence_db),
        "--min-silence",
        str(args.min_silence),
        "--pad",
        str(args.pad),
    ]
    if args.no_stumbles:
        cmd.append("--no-stumbles")
    if args.dry_run:
        cmd.append("--dry-run")
    if export_words is not None:
        cmd.extend(["--export-words", str(export_words)])
    subprocess.run(cmd, check=True)


def relpath_for_state(path: Path) -> str:
    """Express a path as PKA-relative when possible; else absolute."""
    try:
        return str(path.resolve().relative_to(PKA_ROOT))
    except ValueError:
        return str(path.resolve())


def print_hyperframes_next_step(
    recording: Path,
    cleaned: Path,
    transcript: Path,
    sidecar: Path | None,
    candidates: list[Path],
) -> None:
    """Emit a structured next-step block to stderr so it shows up alongside clean_video output."""
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    if sidecar is not None:
        print(f"HyperFrames render pending for: {recording.name}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        transcript_label = str(transcript) if (transcript and Path(transcript).exists()) else f"{transcript}  ⚠ not written (Whisper unavailable)"
        print(f"  cleaned video   : {cleaned}", file=sys.stderr)
        print(f"  word transcript : {transcript_label}", file=sys.stderr)
        print(f"  sidecar         : {sidecar}", file=sys.stderr)
    else:
        print(f"HyperFrames sidecar candidates for: {recording.name}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        transcript_label = str(transcript) if (transcript and Path(transcript).exists()) else f"{transcript}  ⚠ not written (Whisper unavailable)"
        print(f"  cleaned video   : {cleaned}", file=sys.stderr)
        print(f"  word transcript : {transcript_label}", file=sys.stderr)
        print(f"  candidates ({len(candidates)}):", file=sys.stderr)
        for cand in candidates:
            print(f"    - {cand}", file=sys.stderr)
        print("", file=sys.stderr)
        print("  Multiple sidecars match the time window. Confirm with:", file=sys.stderr)
        print(
            f'    python3 tools/cuecam_recording_intake.py pair "{recording}" "<sidecar>"',
            file=sys.stderr,
        )
    print("", file=sys.stderr)
    print(f"  Next step: {HYPERFRAMES_NEXT_STEP}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)


def print_transcript_ready_next_step(
    recording: Path,
    cleaned: Path,
    transcript: Path,
) -> None:
    """Emit a transcript-ready next-step block to stderr for recordings without a sidecar."""
    print("", file=sys.stderr)
    print("─" * 54, file=sys.stderr)
    print("TRANSCRIPT READY — no HyperFrames sidecar paired", file=sys.stderr)
    print(f"  recording       : {recording.name}", file=sys.stderr)
    transcript_label = (
        str(transcript)
        if (transcript and transcript.exists())
        else f"{transcript}  ⚠ not written (Whisper unavailable)"
    )
    print(f"  cleaned video   : {cleaned}", file=sys.stderr)
    print(f"  word transcript : {transcript_label}", file=sys.stderr)
    print("", file=sys.stderr)
    print(f"Next step: {TRANSCRIPT_READY_NEXT_STEP}", file=sys.stderr)
    print("─" * 54, file=sys.stderr)
    print("", file=sys.stderr)


def cmd_process(args: argparse.Namespace) -> int:
    recordings_dir = Path(args.recordings_dir).expanduser()
    state = load_state()
    if not STATE_PATH.exists() and not args.include_known:
        print(
            f"State file is missing. Run `{Path(__file__).name} init` first to baseline existing recordings.",
            file=sys.stderr,
        )
        return 2

    candidates, skipped_short = find_candidates(
        state,
        recordings_dir,
        min_age_seconds=args.min_age_seconds,
        min_duration_seconds=args.min_duration_seconds,
        include_known=args.include_known,
    )
    candidates = candidates[: args.limit]
    processed = state.setdefault("processed", {})
    known = state.setdefault("known", {})
    results = []
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None

    # Permanently mark short files so they never resurface in future scans
    if skipped_short and not args.dry_run:
        existing_skipped = {r["path"] for r in state.get("skipped_short", [])}
        state.setdefault("skipped_short", [])
        for rec in skipped_short:
            if rec["path"] not in existing_skipped:
                state["skipped_short"].append({**rec, "skipped_at": utc_now(), "reason": "under_min_duration"})

    for path in candidates:
        output_path = cleaned_output_path(path, output_dir)
        sidecar_candidates = find_sidecar_candidates(path, args.sidecar_window_days)
        export_words = words_export_path(output_path)  # always export for post-production analysis
        result = {
            "source": str(path.resolve()),
            "output": str(output_path.resolve()),
            "dry_run": args.dry_run,
            "sidecar_candidate_count": len(sidecar_candidates),
        }
        try:
            run_clean_video(path, output_path, args, export_words=export_words)
            result["status"] = "planned" if args.dry_run else "cleaned"
            if not args.dry_run:
                key = file_key(path)
                word_transcript_value = str(export_words.resolve()) if export_words.exists() else None
                entry: dict[str, Any] = {
                    **file_record(path),
                    "cleaned_output": str(output_path.resolve()),
                    "word_transcript": word_transcript_value,
                    "processed_at": utc_now(),
                    "method": "tools/clean_video.py",
                    "no_stumbles": args.no_stumbles,
                }
                if sidecar_candidates:
                    if len(sidecar_candidates) == 1:
                        sidecar = sidecar_candidates[0]
                        entry["status"] = "hyperframes-pending"
                        entry["sidecar"] = relpath_for_state(sidecar)
                        entry["auto_paired"] = True
                        entry["next_step"] = HYPERFRAMES_NEXT_STEP
                        result["hyperframes"] = {
                            "auto_paired": True,
                            "sidecar": relpath_for_state(sidecar),
                        }
                        print_hyperframes_next_step(
                            path, output_path, export_words, sidecar, sidecar_candidates,
                        )
                    else:
                        entry["status"] = "hyperframes-candidates"
                        entry["sidecar_candidates"] = [
                            relpath_for_state(c) for c in sidecar_candidates
                        ]
                        entry["auto_paired"] = False
                        entry["next_step"] = HYPERFRAMES_NEXT_STEP
                        result["hyperframes"] = {
                            "auto_paired": False,
                            "sidecar_candidates": [
                                relpath_for_state(c) for c in sidecar_candidates
                            ],
                        }
                        print_hyperframes_next_step(
                            path, output_path, export_words, None, sidecar_candidates,
                        )
                else:
                    entry["status"] = "transcript-ready"
                    entry["next_step"] = TRANSCRIPT_READY_NEXT_STEP
                    print_transcript_ready_next_step(path, output_path, export_words)
                processed[key] = entry
                known[key] = file_record(path)
        except subprocess.CalledProcessError as exc:
            result["status"] = "error"
            result["returncode"] = exc.returncode
        results.append(result)

    if not args.dry_run:
        save_state(state)

    print(json.dumps({
        "status": "ok",
        "processed_count": len(results),
        "results": results,
        "skipped_short_count": len(skipped_short),
        "skipped_short": skipped_short,
    }, indent=2))
    return 0 if all(item["status"] != "error" for item in results) else 1


# ─── Dropbox public-share helpers ─────────────────────────────────────────────
# Dropbox shared folder APIs require auth tokens even for public links.
# The only unauthenticated path is downloading the folder as a ZIP (?dl=1).
# We download once, list contents, and extract files in a single pass.

_VIDEO_SUFFIXES = {".mov", ".mp4", ".m4v", ".avi", ".mkv"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}


def _dropbox_zip_url(shared_url: str) -> str:
    """Return the ZIP download URL for a public Dropbox shared folder."""
    parsed = urlparse(shared_url)
    qs = parse_qs(parsed.query)
    params: dict[str, str] = {"dl": "1"}
    if qs.get("rlkey"):
        params["rlkey"] = qs["rlkey"][0]
    if qs.get("st"):
        params["st"] = qs["st"][0]
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{base}?{urlencode(params)}"


def _dropbox_fetch_zip(shared_url: str, dest: Path) -> None:
    """Download the public Dropbox shared folder ZIP to dest."""
    import requests

    zip_url = _dropbox_zip_url(shared_url)
    print(f"Downloading Dropbox folder ZIP...", file=sys.stderr)
    with requests.get(zip_url, stream=True, timeout=600,
                      headers={"User-Agent": "Mozilla/5.0"}) as resp:
        if resp.status_code == 404:
            raise FileNotFoundError("Dropbox folder not found — check the URL and rlkey.")
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        last_pct = -1
        with dest.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if pct >= last_pct + 10:
                        print(f"  {downloaded // 1024 // 1024}/{total // 1024 // 1024} MB ({pct}%)", file=sys.stderr)
                        last_pct = pct
    print(f"  Done — {downloaded // 1024 // 1024} MB", file=sys.stderr)


def _dropbox_list_zip(zip_path: Path) -> list[dict]:
    """List files inside a Dropbox folder ZIP (skips __MACOSX, hidden files, dirs)."""
    import zipfile

    entries = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if not name or info.is_dir() or name.startswith(".") or "__MACOSX" in info.filename:
                continue
            entries.append({
                ".tag": "file",
                "name": name,
                "zip_path": info.filename,
                "size": info.file_size,
            })
    return entries


def _dropbox_extract(zip_path: Path, zip_entry_path: str, dest: Path) -> None:
    """Extract a single file from a ZIP to dest."""
    import zipfile

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        data = zf.read(zip_entry_path)
    dest.write_bytes(data)


def _dropbox_slugify(name: str) -> str:
    stem = Path(name).stem
    slug = re.sub(r"[^\w\s-]", "", stem.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:60] or "field-clip"


def cmd_from_dropbox(args: argparse.Namespace) -> int:
    """
    Download the latest phone video (+ images) from a public Dropbox shared folder,
    run the full Whisper edit pipeline, and land in state: transcript-ready.

    Public Dropbox folders don't expose a REST listing API without auth.
    We download the folder as a ZIP (?dl=1), list contents, and extract in one pass.
    """
    import shutil
    import tempfile

    shared_url = args.url
    intake_dir = PKA_ROOT / "team-inbox" / "video-intake"
    intake_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Download ZIP to temp file, list contents ────────────────────────────
    tmp_zip = Path(tempfile.mktemp(suffix=".zip", prefix="dropbox-"))
    try:
        _dropbox_fetch_zip(shared_url, tmp_zip)
        all_entries = _dropbox_list_zip(tmp_zip)
    except Exception as exc:
        if tmp_zip.exists():
            tmp_zip.unlink()
        print(json.dumps({"error": f"Dropbox fetch failed: {exc}"}))
        return 1

    videos = [e for e in all_entries if Path(e["name"]).suffix.lower() in _VIDEO_SUFFIXES]
    images = [e for e in all_entries if Path(e["name"]).suffix.lower() in _IMAGE_SUFFIXES]

    # ── 2. Dry-run: list and exit ──────────────────────────────────────────────
    if args.dry_run:
        zip_size_mb = round(tmp_zip.stat().st_size / 1024 / 1024, 1)
        tmp_zip.unlink(missing_ok=True)
        print(json.dumps({
            "status": "dry-run",
            "zip_size_mb": zip_size_mb,
            "videos": [{"name": v["name"], "size_mb": round(v["size"] / 1024 / 1024, 1)} for v in videos],
            "images": [i["name"] for i in images],
        }, indent=2))
        return 0

    if not videos:
        tmp_zip.unlink(missing_ok=True)
        print(json.dumps({"error": "No video files found in Dropbox folder", "all_files": [e["name"] for e in all_entries]}))
        return 1

    # ── 3. Pick video ──────────────────────────────────────────────────────────
    if args.pick:
        matches = [v for v in videos if v["name"].lower() == args.pick.lower()]
        if not matches:
            tmp_zip.unlink(missing_ok=True)
            print(json.dumps({
                "error": f"No video named '{args.pick}' found.",
                "available": [v["name"] for v in videos],
            }, indent=2))
            return 1
        chosen = matches[0]
    elif len(videos) > 1 and not args.latest:
        # Multiple videos — show list; Larry will ask John to pick
        tmp_zip.unlink(missing_ok=True)
        print(json.dumps({
            "action": "pick-required",
            "message": f"{len(videos)} videos in folder. Re-run with --pick <name> or --latest.",
            "videos": [{"name": v["name"], "size_mb": round(v["size"] / 1024 / 1024, 1)} for v in videos],
        }, indent=2))
        return 0
    else:
        # --latest: sort by filename descending (IMG_NNNN — higher = more recent)
        chosen = sorted(videos, key=lambda v: v["name"], reverse=True)[0]

    slug = args.slug or _dropbox_slugify(chosen["name"])

    # ── 4. Extract video from ZIP ──────────────────────────────────────────────
    dest_video = intake_dir / chosen["name"]
    if dest_video.exists() and not args.force:
        print(f"Already in intake (skipping re-extract): {dest_video.name}", file=sys.stderr)
    else:
        print(f"Extracting {chosen['name']} ({round(chosen['size']/1024/1024,1)} MB)...", file=sys.stderr)
        _dropbox_extract(tmp_zip, chosen["zip_path"], dest_video)

    # ── 5. Extract images → team-inbox/video-intake/<slug>-assets/ ────────────
    asset_dir = intake_dir / f"{slug}-assets"
    downloaded_images: list[str] = []
    if images:
        asset_dir.mkdir(parents=True, exist_ok=True)
        for img in images:
            img_dest = asset_dir / img["name"]
            if not img_dest.exists() or args.force:
                print(f"Extracting image: {img['name']}", file=sys.stderr)
                _dropbox_extract(tmp_zip, img["zip_path"], img_dest)
            downloaded_images.append(str(img_dest.relative_to(PKA_ROOT)))

    # ZIP fully consumed — delete it
    tmp_zip.unlink(missing_ok=True)

    # ── 5b. Rotation normalization ─────────────────────────────────────────────
    # Probe each clip individually. Apply hflip,vflip only to -180° Display Matrix
    # clips (front/selfie camera). Never normalize globally across a batch.
    normalized_video = intake_dir / (dest_video.stem + "_normalized.mp4")
    rotation_result = normalize_clip_rotation(dest_video, normalized_video)
    # Use the normalized file as the source for all downstream steps
    pipeline_video = normalized_video

    # ── 6. Set up Codex project structure ─────────────────────────────────────
    codex_studio = Path(os.environ.get("PKA_VIDEO_STUDIO", str(PKA_ROOT / "video-studio"))).expanduser()
    codex_project = codex_studio / "projects" / slug
    for sub in ("raw", "transcripts", "edit", "motion", "hyperframes/assets", "renders"):
        (codex_project / sub).mkdir(parents=True, exist_ok=True)

    meta_path = codex_project / "hyperframes" / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(
            json.dumps({"id": slug, "name": slug.replace("-", " ").title()}, indent=2),
            encoding="utf-8",
        )

    # ── 7. Run Whisper edit pipeline ───────────────────────────────────────────
    whisper_script = codex_studio / "bin" / "run_whisper_edit.sh"
    if not whisper_script.exists():
        print(json.dumps({"error": f"Whisper script not found: {whisper_script}"}))
        return 1

    env = {**os.environ, "WHISPER_MODEL": args.whisper_model, "TAIL": "0.7"}
    print(f"Running Whisper edit pipeline (model={args.whisper_model})...", file=sys.stderr)
    subprocess.run(
        ["bash", str(whisper_script), str(pipeline_video), slug],
        check=True,
        env=env,
    )

    # ── 8. Keyframe re-encode edited.mp4 → hyperframes/assets/edited.mp4 ──────
    edited_src = codex_project / "edit" / "edited.mp4"
    edited_hf  = codex_project / "hyperframes" / "assets" / "edited.mp4"
    if edited_src.exists():
        print("Re-encoding keyframes for HyperFrames render...", file=sys.stderr)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(edited_src),
            "-c:v", "libx264",
            "-r", "30", "-g", "30", "-keyint_min", "30",
            "-movflags", "+faststart",
            "-c:a", "aac",
            str(edited_hf),
        ], check=True)
    else:
        print(f"WARNING: edited.mp4 not produced by Whisper pipeline at {edited_src}", file=sys.stderr)

    # Copy trigger images into hyperframes/assets/ for index.html relative paths
    for img_rel in downloaded_images:
        img_src = PKA_ROOT / img_rel
        img_dst = codex_project / "hyperframes" / "assets" / img_src.name
        if not img_dst.exists():
            shutil.copy2(img_src, img_dst)

    # ── 9. Register in cuecam_recording_intake.json ────────────────────────────
    transcript_packed = codex_project / "motion" / "transcript_packed_edited.md"
    words_json        = codex_project / "transcripts" / f"{Path(chosen['name']).stem}.json"

    state_data = load_state()
    processed  = state_data.setdefault("processed", {})
    known      = state_data.setdefault("known", {})
    key        = file_key(dest_video)

    entry: dict[str, Any] = {
        "filename":         chosen["name"],
        "path":             str(dest_video.resolve()),
        "normalized_path":  str(normalized_video.resolve()),
        "rotation_detected": rotation_result["detected_rotation"],
        "rotation_action":  rotation_result["action"],
        "mtime":            dest_video.stat().st_mtime,
        "mtime_iso":        utc_now(),
        "size":             dest_video.stat().st_size,
        "status":           "transcript-ready",
        "source":           "dropbox",
        "dropbox_shared_url": shared_url,
        "codex_project":    str(codex_project.resolve()),
        "cleaned_output":   str(edited_src.resolve())    if edited_src.exists()    else None,
        "word_transcript":  str(words_json.resolve())    if words_json.exists()    else None,
        "transcript_packed":str(transcript_packed.resolve()) if transcript_packed.exists() else None,
        "hf_video":         str(edited_hf.resolve())     if edited_hf.exists()     else None,
        "assets":           downloaded_images,
        "processed_at":     utc_now(),
        "method":           "from-dropbox",
        "next_step":        TRANSCRIPT_READY_NEXT_STEP,
    }
    processed[key] = entry
    known[key] = {
        "filename": chosen["name"],
        "path":     str(dest_video.resolve()),
        "size":     dest_video.stat().st_size,
    }
    save_state(state_data)

    # ── 10. Create hyperframes task record ─────────────────────────────────────
    subprocess.run([
        sys.executable,
        str(PKA_ROOT / "tools" / "hyperframes_state.py"),
        "create",
        "--slug",          slug,
        "--recording",     str(dest_video.resolve()),
        "--transcript",    str(transcript_packed.resolve()),
        "--codex-project", str(codex_project.resolve()),
        "--force",
    ], check=True)

    result = {
        "status":           "ok",
        "slug":             slug,
        "state":            "transcript-ready",
        "recording":        str(dest_video),
        "normalized_video": str(normalized_video),
        "rotation_detected": rotation_result["detected_rotation"],
        "rotation_action":  rotation_result["action"],
        "codex_project":    str(codex_project),
        "transcript":       str(transcript_packed) if transcript_packed.exists() else "pending",
        "edited_video":     str(edited_src)         if edited_src.exists()        else "pending",
        "hf_video":         str(edited_hf)          if edited_hf.exists()         else "pending",
        "task_record":      f"owners-inbox/tasks/{slug}.md",
        "draft":            f"team-inbox/discord/{slug}.hyperframes-draft.md",
        "images":           len(downloaded_images),
    }
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track and clean future CueCam Presenter recordings")
    parser.add_argument(
        "--recordings-dir",
        default=str(DEFAULT_RECORDINGS_DIR),
        help="Folder where CueCam Presenter writes recordings",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Mark current recordings as known baseline")

    p_scan = sub.add_parser("scan", help="List future recordings not yet processed")
    p_scan.add_argument("--min-age-seconds", type=int, default=120)
    p_scan.add_argument("--min-duration-seconds", type=int, default=MIN_DURATION_SECONDS,
                        help="Skip recordings shorter than this (audio-sync tests, etc.)")
    p_scan.add_argument("--include-known", action="store_true", help="Include baseline files that are not processed")
    p_scan.add_argument("--limit", type=int, default=20)

    p_process = sub.add_parser("process", help="Clean future recordings not yet processed")
    p_process.add_argument("--min-age-seconds", type=int, default=120)
    p_process.add_argument("--min-duration-seconds", type=int, default=MIN_DURATION_SECONDS,
                           help="Skip recordings shorter than this (audio-sync tests, etc.)")
    p_process.add_argument("--include-known", action="store_true", help="Allow processing baseline files too")
    p_process.add_argument("--limit", type=int, default=1)
    p_process.add_argument("--output-dir", help="Optional folder for cleaned outputs; default is beside source")
    p_process.add_argument("--model", default="base")
    p_process.add_argument("--silence-db", type=float, default=-35.0)
    p_process.add_argument("--min-silence", type=float, default=0.5)
    p_process.add_argument("--pad", type=float, default=0.15)
    p_process.add_argument("--no-stumbles", action="store_true", help="Skip Whisper stumble cleanup")
    p_process.add_argument("--dry-run", action="store_true", help="Show planned cuts without rendering or updating state")
    p_process.add_argument(
        "--sidecar-window-days",
        type=int,
        default=DEFAULT_SIDECAR_WINDOW_DAYS,
        help="Pair recordings to .hyperframes.md sidecars whose mtime is within this many days "
             "of the recording (default: 7). 0 disables sidecar detection.",
    )

    p_pair = sub.add_parser(
        "pair",
        help="Manually pair a processed recording with a HyperFrames sidecar",
    )
    p_pair.add_argument("recording", help="Path to the source recording (or its key in state)")
    p_pair.add_argument("sidecar", help="Path to the .hyperframes.md sidecar")

    p_db = sub.add_parser(
        "from-dropbox",
        help="Download latest phone video from a public Dropbox shared folder and run full intake",
    )
    p_db.add_argument("--url", required=True, help="Public Dropbox shared folder URL")
    p_db.add_argument("--pick", help="Filename to download (default: most recently modified video)")
    p_db.add_argument("--latest", action="store_true", help="Auto-pick the most recently modified video even when multiple exist")
    p_db.add_argument("--slug", help="Override the recording slug (default: derived from filename)")
    p_db.add_argument("--whisper-model", default="turbo", help="Whisper model name (default: turbo)")
    p_db.add_argument("--force", action="store_true", help="Re-download even if file already exists in intake")
    p_db.add_argument("--dry-run", action="store_true", help="List folder contents without downloading or processing")

    return parser


def cmd_pair(args: argparse.Namespace) -> int:
    state = load_state()
    processed = state.setdefault("processed", {})

    recording_path = Path(args.recording).expanduser()
    if recording_path.exists():
        key = file_key(recording_path)
    else:
        # Allow passing the key directly (already-resolved path string)
        key = str(Path(args.recording).expanduser())

    if key not in processed:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "recording not in processed state — run `process` first",
                    "recording": args.recording,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    sidecar_path = Path(args.sidecar).expanduser()
    if not sidecar_path.exists():
        alt = PKA_ROOT / args.sidecar
        if alt.exists():
            sidecar_path = alt
        else:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error": "sidecar not found",
                        "sidecar": args.sidecar,
                    },
                    indent=2,
                ),
                file=sys.stderr,
            )
            return 2
    if not sidecar_path.name.endswith(".hyperframes.md"):
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "sidecar must be a *.hyperframes.md file",
                    "sidecar": str(sidecar_path),
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    entry = processed[key]
    entry["status"] = "hyperframes-pending"
    entry["sidecar"] = relpath_for_state(sidecar_path)
    entry["auto_paired"] = False
    entry["paired_at"] = utc_now()
    entry["next_step"] = HYPERFRAMES_NEXT_STEP
    entry.pop("sidecar_candidates", None)

    save_state(state)

    payload = {
        "status": "ok",
        "recording": key,
        "sidecar": entry["sidecar"],
        "cleaned_video": entry.get("cleaned_output"),
        "word_transcript": entry.get("word_transcript"),
        "auto_paired": False,
    }
    print(json.dumps(payload, indent=2))
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "init":
        return cmd_init(args)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "process":
        return cmd_process(args)
    if args.command == "pair":
        return cmd_pair(args)
    if args.command == "from-dropbox":
        return cmd_from_dropbox(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
