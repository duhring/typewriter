#!/usr/bin/env python3
"""Run technical QC on a final video master and write an indexed report."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date
from pathlib import Path

from pka_index import index_markdown_artifact


PKA_ROOT = Path(__file__).resolve().parent.parent


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def probe_video(path: Path) -> dict:
    proc = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    return json.loads(proc.stdout)


def _rate(raw: str | None) -> float | None:
    if not raw or raw == "0/0":
        return None
    try:
        numerator, denominator = raw.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None


def evaluate_probe(probe: dict) -> tuple[list[dict], dict]:
    streams = probe.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    fmt = probe.get("format", {})
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    add("duration", "pass" if duration > 1 else "fail", f"{duration:.2f} seconds")
    add("video_stream", "pass" if video else "fail", "present" if video else "missing")
    add("audio_stream", "pass" if audio else "fail", "present" if audio else "missing")

    metadata = {"duration_seconds": duration}
    if video:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        codec = str(video.get("codec_name") or "unknown")
        frame_rate = _rate(video.get("avg_frame_rate"))
        rotation = 0
        for side in video.get("side_data_list") or []:
            if "rotation" in side:
                rotation = int(side["rotation"])
        metadata.update({
            "width": width,
            "height": height,
            "video_codec": codec,
            "frame_rate": frame_rate,
            "rotation": rotation,
        })
        add(
            "resolution",
            "pass" if max(width, height) >= 1280 and min(width, height) >= 720 else "warn",
            f"{width}×{height}",
        )
        add(
            "video_codec",
            "pass" if codec in {"h264", "hevc", "vp9", "av1"} else "warn",
            codec,
        )
        add("rotation", "pass" if rotation == 0 else "warn", f"metadata rotation={rotation}°")
        add("frame_rate", "pass" if frame_rate and frame_rate >= 23 else "warn", str(frame_rate or "unknown"))
    if audio:
        metadata.update({
            "audio_codec": audio.get("codec_name"),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
        })
        sample_rate = metadata["sample_rate"]
        add("sample_rate", "pass" if sample_rate >= 44100 else "warn", f"{sample_rate} Hz")
    return checks, metadata


def run_decode(path: Path) -> dict:
    proc = _run([
        "ffmpeg", "-v", "error", "-i", str(path),
        "-map", "0:v:0?", "-map", "0:a:0?", "-f", "null", "-",
    ])
    detail = proc.stderr.strip()
    return {
        "check": "complete_decode",
        "status": "pass" if proc.returncode == 0 and not detail else "fail",
        "detail": detail or "decoded without errors",
    }


def detect_edges(path: Path, duration: float) -> tuple[list[dict], dict]:
    proc = _run([
        "ffmpeg", "-hide_banner", "-i", str(path),
        "-vf", "blackdetect=d=1:pix_th=0.10",
        "-af", "silencedetect=n=-50dB:d=2,volumedetect", "-f", "null", "-",
    ])
    output = proc.stderr
    black = [
        {"start": float(a), "end": float(b), "duration": float(c)}
        for a, b, c in re.findall(
            r"black_start:([0-9.]+)\s+black_end:([0-9.]+)\s+black_duration:([0-9.]+)", output
        )
    ]
    silence_starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", output)]
    silence_ends = [
        {"end": float(end), "duration": float(length)}
        for end, length in re.findall(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)", output)
    ]
    edge_black = [
        item for item in black
        if item["start"] <= 0.25 or (duration and item["end"] >= duration - 0.25)
    ]
    edge_silence = []
    for index, start in enumerate(silence_starts):
        end_item = silence_ends[index] if index < len(silence_ends) else {"end": duration, "duration": duration - start}
        if start <= 0.25 or (duration and end_item["end"] >= duration - 0.25):
            edge_silence.append({"start": start, **end_item})
    mean_match = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", output)
    max_match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", output)
    mean_volume = float(mean_match.group(1)) if mean_match else None
    max_volume = float(max_match.group(1)) if max_match else None
    checks = [
        {
            "check": "edge_black",
            "status": "warn" if edge_black else "pass",
            "detail": f"{len(edge_black)} black segment(s) touch the beginning/end",
        },
        {
            "check": "edge_silence",
            "status": "warn" if edge_silence else "pass",
            "detail": f"{len(edge_silence)} silence segment(s) touch the beginning/end",
        },
        {
            "check": "audio_level",
            "status": (
                "warn" if mean_volume is None or max_volume is None
                or mean_volume < -30 or mean_volume > -8
                or max_volume < -12 or max_volume > -0.5
                else "pass"
            ),
            "detail": f"mean={mean_volume} dB, max={max_volume} dB (approximate; verify by ear)",
        },
    ]
    return checks, {
        "black_segments": black,
        "silence_starts": silence_starts,
        "silence_ends": silence_ends,
        "mean_volume_db": mean_volume,
        "max_volume_db": max_volume,
    }


def build_report(path: Path, checks: list[dict], metadata: dict, detections: dict, status: str) -> str:
    lines = [
        "---",
        f'title: {json.dumps(path.stem + " — Final Master QC")}',
        "category: video-project",
        f"date: {date.today().isoformat()}",
        f"status: {status}",
        "---",
        "",
        f"# Final Master QC — {path.name}",
        "",
        f"**Machine result:** `{status}`  ",
        f"**File:** `{path}`  ",
        "",
        "## Technical Checks",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    for check in checks:
        detail = str(check["detail"]).replace("\n", " ")[:400]
        lines.append(f'| {check["check"]} | {check["status"]} | {detail} |')
    lines.extend(["", "## Metadata", "", "```json", json.dumps(metadata, indent=2), "```"])
    if detections:
        lines.extend(["", "## Edge Detection", "", "```json", json.dumps(detections, indent=2), "```"])
    lines.extend([
        "",
        "## Required Human Review",
        "",
        "- [ ] Watch the beginning and ending completely.",
        "- [ ] Confirm spoken audio remains synchronized throughout.",
        "- [ ] Confirm names, captions, overlays, and factual claims are correct.",
        "- [ ] Confirm no accidental edits, black frames, clipped speech, or missing material.",
        "- [ ] Confirm this exact file is the publication master.",
        "",
        "Machine QC passing does not approve the master. The owner must still approve the `master` gate.",
    ])
    return "\n".join(lines).strip() + "\n"


def run_qc(path: Path, *, quick: bool = False) -> tuple[str, list[dict], dict, dict]:
    probe = probe_video(path)
    checks, metadata = evaluate_probe(probe)
    detections = {}
    if quick:
        checks.append({"check": "full_scan", "status": "fail", "detail": "skipped by --quick; not approvable"})
    else:
        checks.append(run_decode(path))
        edge_checks, detections = detect_edges(path, metadata.get("duration_seconds", 0))
        checks.extend(edge_checks)
    status = "fail" if any(item["status"] == "fail" for item in checks) else "pass"
    return status, checks, metadata, detections


def main() -> int:
    parser = argparse.ArgumentParser(description="Run technical QC on a final video master")
    parser.add_argument("video")
    parser.add_argument("--project", help="Video project slug; attaches master and QC report")
    parser.add_argument("--output", help="Report path")
    parser.add_argument("--quick", action="store_true", help="Probe only; deliberately cannot pass")
    args = parser.parse_args()

    path = Path(args.video).expanduser().resolve()
    if not path.exists():
        parser.error(f"video not found: {path}")
    status, checks, metadata, detections = run_qc(path, quick=args.quick)

    if args.output:
        report_path = Path(args.output).expanduser().resolve()
    elif args.project:
        from video_project import _project_dir

        report_path = _project_dir(args.project) / "qc-report.md"
    else:
        report_dir = PKA_ROOT / "owners-inbox" / "video-qc"
        report_path = report_dir / f"{date.today().isoformat()}-{path.stem}-qc.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(path, checks, metadata, detections, status), encoding="utf-8")
    indexed = index_markdown_artifact(
        report_path,
        title=f"{path.stem} — Final Master QC",
        category="video-project",
        tags=["video-project", "final-master", "qc", status],
        summary=f"Final-master technical QC for {path.name}: {status}.",
    )

    result = {
        "status": status,
        "video": str(path),
        "report": str(report_path),
        "knowledge_base_id": indexed["knowledge_base_id"],
        "checks": checks,
    }
    if args.project:
        import video_project

        video_project.attach_artifact(
            slug=args.project,
            kind="final_master",
            raw_path=str(path),
            note="Publication master submitted for technical QC.",
        )
        project_result = video_project.record_qc(
            slug=args.project,
            report_path=str(report_path),
            status=status,
            checks={item["check"]: item["status"] for item in checks},
        )
        result["project"] = project_result
    print(json.dumps(result, indent=2))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
