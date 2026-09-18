#!/usr/bin/env python3
"""
HyperFrames render orchestrator — fire-and-forget Discord-safe render pipeline.

Takes a recording slug in spec-complete state and runs the full chain:
  draft-to-overlay-timing → html-gen → npx hyperframes render → gdrive upload → Discord callback

Two modes:
  --slug <slug>          Start a new render (fires background worker, returns immediately)
  --slug <slug> --worker Internal mode: runs the actual render (called by the background subprocess)

Usage (Larry triggers from Discord):
  python3 tools/codex_hyperframes_render.py --slug yosemite-clip-1

The caller gets an immediate JSON reply:
  {"status": "started", "slug": "...", "job_id": "...", "eta_seconds": 180}

On completion the worker posts a Discord message:
  "✅ yosemite-clip-1 rendered — Drive link: https://..."
  or
  "❌ yosemite-clip-1 render failed — check data/render_jobs.json"
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PKA_ROOT / "tools"
VENV_PYTHON = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"
JOBS_FILE = PKA_ROOT / "data" / "render_jobs.json"
TASKS_DIR = PKA_ROOT / "owners-inbox" / "tasks"

# Drive folder where rendered MP4s land
DRIVE_REVIEW_FOLDER = "PKA Reviews"

# ETA estimate based on Filoli proof-of-life: ~2 min for 62s clip
ETA_SECONDS = 180


# ── Job registry ──────────────────────────────────────────────────────────────

def _load_jobs() -> dict:
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_jobs(jobs: dict) -> None:
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def _register_job(slug: str, job_id: str) -> None:
    jobs = _load_jobs()
    jobs[job_id] = {
        "slug": slug,
        "job_id": job_id,
        "status": "running",
        "started_at": _utcnow(),
        "finished_at": None,
        "error": None,
        "drive_link": None,
        "rendered_video": None,
    }
    _save_jobs(jobs)


def _update_job(job_id: str, **kwargs) -> None:
    jobs = _load_jobs()
    if job_id in jobs:
        jobs[job_id].update(kwargs)
        jobs[job_id]["finished_at"] = _utcnow()
    _save_jobs(jobs)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Task record helpers ───────────────────────────────────────────────────────

def _task_path(slug: str) -> Path:
    return TASKS_DIR / f"{slug}.md"


def _parse_task_record(slug: str) -> dict:
    """Extract key fields from the markdown task record."""
    path = _task_path(slug)
    if not path.exists():
        return {}
    data: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        for prefix in ("- **Codex project:**", "- codex_project:", "codex_project:"):
            if stripped.lower().startswith(prefix.lower()):
                data["codex_project"] = stripped[len(prefix):].strip().strip("`").strip("*").strip()
        for prefix in ("- **State:**", "- state:", "state:"):
            if stripped.lower().startswith(prefix.lower()):
                data["state"] = stripped[len(prefix):].strip().strip("`").strip("*").strip()
    return data


def _advance_state(slug: str, to: str, **kwargs) -> None:
    cmd = [
        str(VENV_PYTHON), str(TOOLS_DIR / "hyperframes_state.py"),
        "advance", "--slug", slug, "--to", to,
    ]
    for k, v in kwargs.items():
        cmd += [f"--{k.replace('_', '-')}", str(v)]
    subprocess.run(cmd, capture_output=True)


# ── Discord notify ────────────────────────────────────────────────────────────

def _discord_send(message: str) -> None:
    try:
        subprocess.run(
            [str(VENV_PYTHON), str(TOOLS_DIR / "discord_send.py"), message],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass  # best-effort; render already done


def _lint_and_validate_project(hf_dir: Path, cp: Path) -> None:
    """
    Runs static lint and dynamic validate checks.
    Fails the build only on structural errors or contrast warnings
    that occur during active overlay timestamps.
    """
    # 1. Run static lint
    r_lint = subprocess.run(
        ["npx", "--yes", "hyperframes", "lint", "--json"],
        cwd=str(hf_dir),
        capture_output=True, text=True,
    )
    
    lint_data = {}
    try:
        lint_data = json.loads(r_lint.stdout)
    except Exception:
        pass

    lint_errors = []
    lint_warnings = []
    
    findings = lint_data.get("findings", [])
    if isinstance(findings, list):
        for f in findings:
            severity = f.get("severity", "error")
            msg = f.get("message", "")
            code = f.get("code", "")
            finding_str = f"[{code}] {msg}" if code else msg
            if severity == "error":
                lint_errors.append(finding_str)
            else:
                lint_warnings.append(finding_str)
                
    # Fallback to old errors format if present
    old_errors = lint_data.get("errors", [])
    if isinstance(old_errors, list):
        for err in old_errors:
            err_str = str(err)
            if err_str not in lint_errors:
                lint_errors.append(err_str)
                
    if lint_errors:
        raise RuntimeError(f"Static lint errors: {lint_errors}")
        
    for w in lint_warnings:
        print(f"Lint warning (non-blocking): {w}")

    # 2. Run dynamic validate
    r_val = subprocess.run(
        ["npx", "--yes", "hyperframes", "validate", "--json", "--timeout", "10000"],
        cwd=str(hf_dir),
        capture_output=True, text=True,
    )
    
    val_data = {}
    try:
        val_data = json.loads(r_val.stdout)
    except Exception:
        pass
        
    if r_val.returncode != 0 and not val_data:
        raise RuntimeError(f"hyperframes validate failed to run (exit {r_val.returncode}): {r_val.stderr.strip() or r_val.stdout.strip()}")

    val_errors = []
    val_warnings = []
    
    if val_data.get("ok") is False and "error" in val_data:
        val_errors.append(val_data["error"])
        
    errors_list = val_data.get("errors", [])
    if isinstance(errors_list, list):
        for err in errors_list:
            if isinstance(err, dict):
                val_errors.append(err.get("text", "Unknown console error"))
            else:
                val_errors.append(str(err))
                
    warnings_list = val_data.get("warnings", [])
    if isinstance(warnings_list, list):
        for wrn in warnings_list:
            if isinstance(wrn, dict):
                val_warnings.append(wrn.get("text", "Unknown console warning"))
            else:
                val_warnings.append(str(wrn))

    if val_errors:
        raise RuntimeError(f"Runtime validation errors: {val_errors}")
        
    for w in val_warnings:
        print(f"Validation warning (non-blocking): {w}")

    # 3. Parse and filter contrast warnings against overlay_timing.json
    contrast_list = val_data.get("contrast", [])
    active_contrast_warnings = []
    inactive_contrast_warnings = []
    
    overlay_timing_path = cp / "motion" / "overlay_timing.json"
    overlays = []
    if overlay_timing_path.exists():
        try:
            timing_data = json.loads(overlay_timing_path.read_text(encoding="utf-8"))
            overlays = timing_data.get("overlays", [])
        except Exception:
            print(f"Warning: Failed to parse overlay timing at {overlay_timing_path}")

    if isinstance(contrast_list, list):
        for c_entry in contrast_list:
            if not isinstance(c_entry, dict) or c_entry.get("wcagAA") is True:
                continue
                
            warning_time = c_entry.get("time")
            if warning_time is None:
                continue
                
            is_active = False
            for ov in overlays:
                start = ov.get("show_time")
                if start is None:
                    continue
                duration = ov.get("duration", 5.0)
                end = start + duration
                
                # Active window including entrance and small exit fade buffer
                if start <= warning_time <= (end + 0.5):
                    is_active = True
                    break
            
            selector = c_entry.get("selector", "")
            text = c_entry.get("text", "")
            ratio = c_entry.get("ratio")
            threshold = "3.0" if c_entry.get("large") else "4.5"
            warning_msg = f"Low contrast ({ratio}:1 < {threshold}:1) on '{selector}' (text: \"{text}\") at t={warning_time}s"
            
            if is_active:
                active_contrast_warnings.append(warning_msg)
            else:
                inactive_contrast_warnings.append(warning_msg)

    for w in inactive_contrast_warnings:
        print(f"Inactive overlay contrast warning (benign noise, ignored): {w}")
        
    if active_contrast_warnings:
        raise RuntimeError(f"Contrast violations on active overlays: {active_contrast_warnings}")


# ── Worker (runs in background) ───────────────────────────────────────────────

def _run_worker(slug: str, job_id: str) -> int:
    """
    Full render pipeline. Called as a detached background subprocess.
    Exits 0 on success, 1 on failure.
    """
    try:
        data = _parse_task_record(slug)
        codex_project_str = data.get("codex_project", "")
        if not codex_project_str:
            raise RuntimeError(f"No codex_project in task record for slug: {slug}")

        cp = Path(codex_project_str)
        if not cp.is_absolute():
            cp = PKA_ROOT / cp
        if not cp.exists():
            raise RuntimeError(f"Codex project directory not found: {cp}")

        hf_dir = cp / "hyperframes"

        # ── Step 1: draft-to-overlay-timing ──
        _discord_send(f"🎬 `{slug}` — generating overlay timing…")
        r = subprocess.run(
            [str(VENV_PYTHON), str(TOOLS_DIR / "hyperframes_state.py"),
             "draft-to-overlay-timing", "--slug", slug],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"draft-to-overlay-timing failed: {r.stderr.strip() or r.stdout.strip()}")

        # ── Step 2: generate index.html ──
        r = subprocess.run(
            [str(VENV_PYTHON), str(TOOLS_DIR / "hyperframes_html_gen.py"),
             "--slug", slug, "--force"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"html-gen failed: {r.stderr.strip() or r.stdout.strip()}")

        # ── Step 3: lint and validate ──
        _discord_send(f"🎬 `{slug}` — linting and validating composition…")
        _lint_and_validate_project(hf_dir, cp)

        # ── Step 4: advance to render-running ──
        _advance_state(slug, "render-running", render_job=job_id)

        # ── Step 5: npx hyperframes render ──
        render_start = time.time()
        r = subprocess.run(
            ["npx", "--yes", "hyperframes", "render"],
            cwd=str(hf_dir),
            capture_output=True, text=True,
            timeout=600,  # 10 min max
        )
        render_elapsed = round(time.time() - render_start)

        if r.returncode != 0:
            raise RuntimeError(f"hyperframes render failed (exit {r.returncode}): {r.stderr[-500:] or r.stdout[-500:]}")

        # Find the rendered MP4
        renders_dir = hf_dir / "renders"
        mp4s = sorted(renders_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not mp4s:
            raise RuntimeError(f"No MP4 found in {renders_dir} after render")
        rendered_mp4 = mp4s[0]

        # ── Step 6: Drive upload ──
        folder_path = f"{DRIVE_REVIEW_FOLDER}/{slug}"
        r = subprocess.run(
            [str(VENV_PYTHON), str(TOOLS_DIR / "gdrive.py"),
             "upload", "--file", str(rendered_mp4), "--folder", folder_path],
            capture_output=True, text=True, timeout=300,
        )
        drive_link = None
        if r.returncode == 0:
            try:
                upload_result = json.loads(r.stdout)
                drive_link = upload_result.get("webViewLink")
            except Exception:
                pass

        # ── Step 7: advance state ──
        _advance_state(slug, "rendered", rendered_video=str(rendered_mp4))
        if drive_link:
            _advance_state(slug, "uploaded", drive_link=drive_link)

        # ── Step 8: update job registry ──
        _update_job(job_id,
                    status="done",
                    rendered_video=str(rendered_mp4),
                    drive_link=drive_link)

        # ── Step 9: Discord success message ──
        size_mb = round(rendered_mp4.stat().st_size / 1_048_576, 1)
        msg_lines = [
            f"✅ **{slug}** — render complete ({render_elapsed}s, {size_mb} MB)",
        ]
        if drive_link:
            msg_lines.append(f"📁 Drive: {drive_link}")
        else:
            msg_lines.append(f"📁 MP4 at: `{rendered_mp4}`  _(Drive upload failed — check gdrive.py)_")
        _discord_send("\n".join(msg_lines))
        return 0

    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc))
        _advance_state(slug, "transcript-ready")  # roll back to re-renderable state
        _discord_send(
            f"❌ **{slug}** — render failed\n"
            f"```{str(exc)[:400]}```\n"
            f"Check `data/render_jobs.json` for details."
        )
        return 1


# ── Launcher (returns immediately) ────────────────────────────────────────────

def _launch(slug: str) -> dict:
    """
    Validate state, register the job, spawn the worker as a detached subprocess,
    and return immediately with a job receipt.
    """
    task_path = _task_path(slug)
    if not task_path.exists():
        return {"error": f"No task record for slug: {slug}"}

    data = _parse_task_record(slug)
    state = data.get("state", "")
    if state != "spec-complete":
        return {
            "error": f"Slug '{slug}' is in state '{state}', expected 'spec-complete'. "
                     "Finish trigger authoring before rendering."
        }

    job_id = str(uuid.uuid4())[:8]
    _register_job(slug, job_id)

    # Spawn detached background worker
    worker_cmd = [
        str(VENV_PYTHON), str(__file__),
        "--slug", slug, "--worker", "--job-id", job_id,
    ]
    # Detach fully: new process group, stdout/stderr to log file
    log_path = PKA_ROOT / "data" / "tmp" / f"render_{slug}_{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "w") as log_fh:
        subprocess.Popen(
            worker_cmd,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,  # detach from terminal / Discord bridge
            env=os.environ.copy(),
        )

    return {
        "status": "started",
        "slug": slug,
        "job_id": job_id,
        "eta_seconds": ETA_SECONDS,
        "log": str(log_path),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="HyperFrames render orchestrator (fire-and-forget)",
    )
    parser.add_argument("--slug", required=True, help="Recording slug")
    parser.add_argument("--worker", action="store_true",
                        help="Internal: run the actual render (called by background subprocess)")
    parser.add_argument("--job-id", help="Job ID (required when --worker)")

    # Status subcommand
    parser.add_argument("--status", action="store_true",
                        help="Print the current job status for this slug")

    args = parser.parse_args()

    if args.status:
        jobs = _load_jobs()
        slug_jobs = [j for j in jobs.values() if j.get("slug") == args.slug]
        if not slug_jobs:
            print(json.dumps({"slug": args.slug, "jobs": []}))
        else:
            latest = sorted(slug_jobs, key=lambda j: j.get("started_at", ""), reverse=True)[0]
            print(json.dumps(latest, indent=2))
        return 0

    if args.worker:
        if not args.job_id:
            print("ERROR: --job-id required with --worker", file=sys.stderr)
            return 1
        return _run_worker(args.slug, args.job_id)

    # Default: launch mode
    result = _launch(args.slug)
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
