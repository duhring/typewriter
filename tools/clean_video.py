#!/usr/bin/env python3
"""
Clean CueCam Presenter recordings by removing silences and stumbles.

Pipeline: Video → FFmpeg silence detection + Whisper word-level timestamps
          → merge cut list → FFmpeg reassemble

Usage:
    python3 tools/clean_video.py input.mp4
    python3 tools/clean_video.py input.mp4 --output cleaned.mp4
    python3 tools/clean_video.py input.mp4 --model small
    python3 tools/clean_video.py input.mp4 --no-stumbles        # silence only
    python3 tools/clean_video.py input.mp4 --dry-run            # show cuts, don't render

Options:
    --output PATH        Output file (default: input_cleaned.mp4 alongside input)
    --model MODEL        Whisper model: tiny, base, small, medium, large (default: base)
    --silence-db DB      Silence threshold in dB, negative number (default: -35)
    --min-silence SECS   Min silence duration to remove in seconds (default: 0.5)
    --pad SECS           Seconds of audio to preserve around each cut edge (default: 0.15)
    --fillers LIST       Comma-separated filler words (default: um,uh,ah,er,hmm)
    --no-stumbles        Skip Whisper filler/repeat detection, silence removal only
    --no-silence         Skip silence removal, stumble detection only
    --dry-run            Print planned cuts without rendering
    --export-words PATH  Write Whisper word-level timestamps to PATH as JSON
                         (used by HyperFrames render pipeline; forces stumble
                         detection on if disabled, since it shares the Whisper run)
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

PKA_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FILLERS = {"um", "uh", "ah", "er", "hmm"}
DEFAULT_SILENCE_DB = -35.0
DEFAULT_MIN_SILENCE = 0.5
DEFAULT_PAD = 0.15


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Interval:
    start: float
    end: float

    def duration(self) -> float:
        return self.end - self.start


def merge_intervals(intervals: List[Interval]) -> List[Interval]:
    if not intervals:
        return []
    sorted_ivs = sorted(intervals, key=lambda x: x.start)
    merged = [Interval(sorted_ivs[0].start, sorted_ivs[0].end)]
    for iv in sorted_ivs[1:]:
        if iv.start <= merged[-1].end + 0.02:
            merged[-1] = Interval(merged[-1].start, max(merged[-1].end, iv.end))
        else:
            merged.append(Interval(iv.start, iv.end))
    return merged


def invert_intervals(cut_intervals: List[Interval], total_duration: float) -> List[Interval]:
    keep = []
    pos = 0.0
    for iv in cut_intervals:
        if iv.start > pos + 0.05:
            keep.append(Interval(pos, iv.start))
        pos = iv.end
    if pos < total_duration - 0.05:
        keep.append(Interval(pos, total_duration))
    return keep


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def get_duration(file_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", file_path],
        capture_output=True, text=True, check=True
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def has_video_stream(file_path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", file_path],
        capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def detect_silences(
    file_path: str,
    noise_db: float,
    min_duration: float,
    pad: float,
) -> List[Interval]:
    print(f"  Detecting silences (threshold={noise_db}dB, min={min_duration}s) ...", file=sys.stderr)
    result = subprocess.run(
        ["ffmpeg", "-i", file_path,
         "-af", f"silencedetect=noise={noise_db}dB:duration={min_duration}",
         "-f", "null", "-"],
        capture_output=True, text=True
    )
    output = result.stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", output)]
    ends   = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", output)]

    # If the file ends mid-silence, ffmpeg may emit a start with no matching end
    silences = []
    for s, e in zip(starts, ends):
        a = max(0.0, s + pad)
        b = max(a, e - pad)
        if b - a > 0.05:
            silences.append(Interval(a, b))

    # Handle trailing silence (silence_start with no silence_end)
    if len(starts) > len(ends):
        s = starts[-1]
        a = max(0.0, s + pad)
        silences.append(Interval(a, a + 0.01))  # minimal marker; will be trimmed at invert

    print(f"    → {len(silences)} silence regions found.", file=sys.stderr)
    return silences


# ---------------------------------------------------------------------------
# Whisper stumble detection
# ---------------------------------------------------------------------------

def _whisper_cli_transcribe(file_path: str, model_name: str) -> dict:
    """Run the system whisper CLI and return a parsed result dict, or None if unavailable."""
    import shutil
    import tempfile

    whisper_exe = shutil.which("whisper")
    if not whisper_exe:
        for candidate in [
            Path.home() / "Library/Python/3.9/bin/whisper",
            Path.home() / "Library/Python/3.10/bin/whisper",
            Path.home() / "Library/Python/3.11/bin/whisper",
            Path.home() / ".local/bin/whisper",
        ]:
            if candidate.exists():
                whisper_exe = str(candidate)
                break

    if not whisper_exe:
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            whisper_exe, file_path,
            "--model", model_name,
            "--language", "en",
            "--word_timestamps", "True",
            "--output_format", "json",
            "--output_dir", tmpdir,
            "--verbose", "False",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  whisper CLI error: {result.stderr[:300]}", file=sys.stderr)
            return None

        stem = Path(file_path).stem
        json_path = Path(tmpdir) / f"{stem}.json"
        if not json_path.exists():
            candidates = list(Path(tmpdir).glob("*.json"))
            if not candidates:
                print("  whisper CLI: no JSON output found.", file=sys.stderr)
                return None
            json_path = candidates[0]

        with open(json_path) as fh:
            return json.load(fh)


def detect_stumbles(
    file_path: str,
    model_name: str,
    fillers: set,
    pad: float,
    export_words_path: str = None,
    duration_s: float = None,
) -> List[Interval]:
    print(f"  Transcribing with Whisper ({model_name}) for stumble detection ...", file=sys.stderr)

    result = None
    try:
        import whisper
        model = whisper.load_model(model_name)
        result = model.transcribe(file_path, word_timestamps=True, verbose=False)
    except ImportError:
        print("  openai-whisper not in venv — trying system whisper CLI ...", file=sys.stderr)
        result = _whisper_cli_transcribe(file_path, model_name)
        if result is None:
            print("  Warning: whisper not found — skipping stumble/word-export.", file=sys.stderr)
            print("  Install with: pip3 install openai-whisper", file=sys.stderr)
            return []

    all_words = []
    raw_words = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []):
            raw = w.get("word", "")
            word = raw.strip().lower().rstrip(".,!?;:'\"")
            start = w.get("start", 0.0)
            end = w.get("end", 0.0)
            all_words.append((word, start, end))
            raw_words.append({"word": raw.strip(), "start": start, "end": end})

    if export_words_path:
        import json as _json
        export_payload = {
            "language": result.get("language", "en"),
            "duration_s": duration_s,
            "words": raw_words,
        }
        with open(export_words_path, "w", encoding="utf-8") as fh:
            _json.dump(export_payload, fh, indent=2)
        print(f"    → exported {len(raw_words)} word timestamps to {export_words_path}", file=sys.stderr)

    cuts = []
    filler_count = 0
    repeat_count = 0

    for word, start, end in all_words:
        if word in fillers:
            cuts.append(Interval(max(0.0, start - pad), end + pad))
            filler_count += 1

    for i in range(1, len(all_words)):
        prev_word, prev_start, prev_end = all_words[i - 1]
        curr_word, curr_start, _       = all_words[i]
        # Repeated consecutive word (false start) — remove the first occurrence
        if prev_word == curr_word and len(prev_word) > 2:
            cuts.append(Interval(max(0.0, prev_start - pad), prev_end + pad))
            repeat_count += 1

    print(f"    → {filler_count} filler words, {repeat_count} repeated words found.", file=sys.stderr)
    return cuts


# ---------------------------------------------------------------------------
# FFmpeg render
# ---------------------------------------------------------------------------

def build_filtergraph(
    keep_intervals: List[Interval],
    has_video: bool,
) -> Tuple[str, List[str]]:
    parts = []
    labels_v = []
    labels_a = []

    for i, iv in enumerate(keep_intervals):
        s = f"{iv.start:.4f}"
        e = f"{iv.end:.4f}"
        if has_video:
            parts.append(f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS[v{i}]")
            labels_v.append(f"[v{i}]")
        parts.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{i}]")
        labels_a.append(f"[a{i}]")

    n = len(keep_intervals)
    if has_video:
        concat_in = "".join(f"{labels_v[i]}{labels_a[i]}" for i in range(n))
        parts.append(f"{concat_in}concat=n={n}:v=1:a=1[outv][outa]")
        maps = ["-map", "[outv]", "-map", "[outa]"]
    else:
        concat_in = "".join(labels_a)
        parts.append(f"{concat_in}concat=n={n}:v=0:a=1[outa]")
        maps = ["-map", "[outa]"]

    return ";".join(parts), maps


def render(
    input_path: str,
    keep_intervals: List[Interval],
    output_path: str,
    video: bool,
) -> None:
    print(f"\n  Rendering {len(keep_intervals)} segments → {output_path}", file=sys.stderr)
    filter_complex, maps = build_filtergraph(keep_intervals, video)

    cmd = ["ffmpeg", "-y", "-i", input_path, "-filter_complex", filter_complex] + maps
    if video:
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]
    cmd += ["-c:a", "aac", "-b:a", "192k", output_path]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\nFFmpeg error:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)

    orig_dur = get_duration(input_path)
    out_dur  = get_duration(output_path)
    removed  = orig_dur - out_dur
    orig_mb  = Path(input_path).stat().st_size / 1_000_000
    out_mb   = Path(output_path).stat().st_size / 1_000_000

    print(f"\n  Done.", file=sys.stderr)
    print(f"  Original : {orig_dur:.1f}s  ({orig_mb:.1f} MB)", file=sys.stderr)
    print(f"  Cleaned  : {out_dur:.1f}s  ({out_mb:.1f} MB)", file=sys.stderr)
    print(f"  Removed  : {removed:.1f}s  ({removed / orig_dur * 100:.0f}% of original)", file=sys.stderr)
    print(f"\n  Output   : {output_path}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    input_file  = None
    output_file = None
    model       = "base"
    silence_db  = DEFAULT_SILENCE_DB
    min_silence = DEFAULT_MIN_SILENCE
    pad         = DEFAULT_PAD
    fillers     = set(DEFAULT_FILLERS)
    do_stumbles = True
    do_silence  = True
    dry_run     = False
    export_words = None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--output" and i + 1 < len(args):
            output_file = args[i + 1]; i += 2
        elif a == "--model" and i + 1 < len(args):
            model = args[i + 1]; i += 2
        elif a == "--silence-db" and i + 1 < len(args):
            silence_db = float(args[i + 1]); i += 2
        elif a == "--min-silence" and i + 1 < len(args):
            min_silence = float(args[i + 1]); i += 2
        elif a == "--pad" and i + 1 < len(args):
            pad = float(args[i + 1]); i += 2
        elif a == "--fillers" and i + 1 < len(args):
            fillers = set(args[i + 1].split(",")); i += 2
        elif a == "--no-stumbles":
            do_stumbles = False; i += 1
        elif a == "--no-silence":
            do_silence = False; i += 1
        elif a == "--dry-run":
            dry_run = True; i += 1
        elif a == "--export-words" and i + 1 < len(args):
            export_words = args[i + 1]; i += 2
        elif not a.startswith("--"):
            input_file = a; i += 1
        else:
            print(f"Unknown option: {a}", file=sys.stderr); i += 1

    if export_words and not do_stumbles:
        print("  Note: --export-words requires Whisper; enabling stumble detection.", file=sys.stderr)
        do_stumbles = True

    if not input_file:
        print("Error: no input file specified.\n", file=sys.stderr)
        print(__doc__)
        sys.exit(1)

    p = Path(input_file)
    if not p.exists():
        alt = PKA_ROOT / input_file
        if alt.exists():
            p = alt
        else:
            print(f"Error: file not found: {input_file}", file=sys.stderr)
            sys.exit(1)

    input_path = str(p)
    if not output_file:
        output_file = str(p.parent / (p.stem + "_cleaned" + p.suffix))

    print(f"\nCleaning : {input_path}", file=sys.stderr)
    print(f"Output   : {output_file}\n", file=sys.stderr)

    duration = get_duration(input_path)
    print(f"Duration : {duration:.1f}s", file=sys.stderr)

    cut_intervals: List[Interval] = []

    if do_silence:
        cut_intervals.extend(
            detect_silences(input_path, silence_db, min_silence, pad)
        )

    if do_stumbles:
        cut_intervals.extend(
            detect_stumbles(input_path, model, fillers, pad, export_words, duration)
        )

    cut_intervals = merge_intervals(cut_intervals)
    keep_intervals = invert_intervals(cut_intervals, duration)

    total_cut = sum(iv.duration() for iv in cut_intervals)
    print(
        f"\nPlan: remove {len(cut_intervals)} segments ({total_cut:.1f}s), "
        f"keep {len(keep_intervals)} segments.",
        file=sys.stderr,
    )

    if dry_run:
        print("\n--- Cut segments ---", file=sys.stderr)
        for iv in cut_intervals:
            print(f"  {iv.start:7.2f}s – {iv.end:7.2f}s  ({iv.duration():.2f}s)", file=sys.stderr)
        print("\n--- Keep segments ---", file=sys.stderr)
        for iv in keep_intervals:
            print(f"  {iv.start:7.2f}s – {iv.end:7.2f}s  ({iv.duration():.2f}s)", file=sys.stderr)
        sys.exit(0)

    if not keep_intervals:
        print(
            "Error: nothing to keep after cutting. "
            "Try a lower --silence-db value or --no-stumbles.",
            file=sys.stderr,
        )
        sys.exit(1)

    video = has_video_stream(input_path)
    render(input_path, keep_intervals, output_file, video)


if __name__ == "__main__":
    main()
