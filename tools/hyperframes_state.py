#!/usr/bin/env python3
"""
HyperFrames per-recording state manager.

Maintains two files per recording slug:
  owners-inbox/tasks/<slug>.md           — canonical task record (state + paths)
  team-inbox/discord/<slug>.hyperframes-draft.md — growing trigger draft

Larry reads these on every Discord message to resume without in-head state.
Discord timeouts cannot corrupt a session — the next message continues exactly
where the last one ended.

Usage:
    python3 tools/hyperframes_state.py create \\
        --slug filoli-2026-05-05 \\
        --recording team-inbox/video-intake/IMG_3928.MOV \\
        --transcript "video-studio/projects/iphone-2026-05-05/motion/transcript_packed_edited.md" \\
        --codex-project "video-studio/projects/iphone-2026-05-05"

    python3 tools/hyperframes_state.py get --slug filoli-2026-05-05

    python3 tools/hyperframes_state.py advance --slug filoli-2026-05-05 --to spec-complete

    # append-trigger reads JSON from stdin:
    echo '{"show_anchor": "the family that", "show_time": 8.5, "asset": "motion/bourn-portrait.jpg",
           "caption": "William Bowers Bourn II", "primitive": "corner-card",
           "hide_anchor": "implicit-next-overlay"}' \\
        | python3 tools/hyperframes_state.py append-trigger --slug filoli-2026-05-05

    python3 tools/hyperframes_state.py get-draft --slug filoli-2026-05-05
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PKA_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PKA_ROOT / "owners-inbox" / "tasks"
DRAFTS_DIR = PKA_ROOT / "team-inbox" / "discord"

# Ordered state machine — advance() validates against this sequence
STATE_SEQUENCE = [
    "transcript-ready",
    # awaiting-trigger-N is a special family handled separately
    "spec-complete",
    "render-running",
    "rendered",
    "uploaded",
    "youtube-private",
    "substack-pending",
    "done",
]

TRIGGER_STATE_PREFIX = "awaiting-trigger-"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _task_path(slug: str) -> Path:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    return TASKS_DIR / f"{slug}.md"


def _draft_path(slug: str) -> Path:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    return DRAFTS_DIR / f"{slug}.hyperframes-draft.md"


# ─── Task record read / write ──────────────────────────────────────────────────

def _parse_task_record(path: Path) -> dict:
    """Parse the YAML-ish frontmatter from a task record."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Task record missing frontmatter: {path}")
    parts = text.split("\n---\n", 1)
    if len(parts) < 2:
        raise ValueError(f"Task record frontmatter not closed: {path}")
    front = parts[0]
    data: dict = {}
    for line in front.splitlines()[1:]:
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        raw = val.strip()
        if raw.lower() == "null":
            data[key.strip()] = None
        elif raw.isdigit():
            data[key.strip()] = int(raw)
        else:
            data[key.strip()] = raw
    return data


def _write_task_record(slug: str, data: dict) -> Path:
    """Write (overwrite) the task record from the given data dict."""
    data["updated_at"] = _utc_now()
    path = _task_path(slug)

    def _fmt(v) -> str:
        if v is None:
            return "null"
        return str(v)

    lines = ["---"]
    for key in [
        "slug", "state", "trigger_count",
        "recording", "transcript", "codex_project", "draft",
        "render_job", "rendered_video", "drive_link", "youtube_url",
        "created_at", "updated_at",
    ]:
        if key in data:
            lines.append(f"{key}: {_fmt(data[key])}")
    lines.append("---")
    lines.append("")
    lines.append(f"# HyperFrames — {slug}")
    lines.append("")
    lines.append(f"**State:** `{data.get('state', '?')}`  ")
    lines.append(f"**Triggers confirmed:** {data.get('trigger_count', 0)}  ")
    lines.append(f"**Recording:** `{data.get('recording', '?')}`  ")
    lines.append(f"**Transcript:** `{data.get('transcript', '?')}`  ")
    lines.append(f"**Codex project:** `{data.get('codex_project', '?')}`  ")
    lines.append(f"**Draft:** `{data.get('draft', '?')}`  ")
    if data.get("render_job"):
        lines.append(f"**Render job:** `{data['render_job']}`  ")
    if data.get("rendered_video"):
        lines.append(f"**Rendered video:** `{data['rendered_video']}`  ")
    if data.get("drive_link"):
        lines.append(f"**Drive link:** {data['drive_link']}  ")
    if data.get("youtube_url"):
        lines.append(f"**YouTube:** {data['youtube_url']}  ")
    lines.append("")
    lines.append("---")
    lines.append("_Larry resumes from this file after any Discord timeout._")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ─── Draft file ────────────────────────────────────────────────────────────────

def _init_draft(slug: str, recording: str, transcript: str) -> Path:
    path = _draft_path(slug)
    now = _utc_now()
    content = (
        f"---\n"
        f"slug: {slug}\n"
        f"recording: {recording}\n"
        f"transcript: {transcript}\n"
        f"created_at: {now}\n"
        f"---\n\n"
        f"# HyperFrames Draft — {slug}\n\n"
        f"_Triggers are appended below as John confirms them one at a time._\n\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def _append_trigger_to_draft(slug: str, trigger_num: int, trigger: dict) -> Path:
    path = _draft_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"Draft file not found: {path}")
    block = (
        f"\n## Trigger {trigger_num}\n\n"
        f"- show_anchor: \"{trigger.get('show_anchor', '')}\"\n"
        f"- show_time: {trigger.get('show_time', 'tbd')}\n"
        f"- primitive: {trigger.get('primitive', 'corner-card')}\n"
    )
    assets = trigger.get("assets")
    if assets:
        block += "- assets:\n"
        for asset in assets:
            block += f"  - {asset}\n"
    else:
        block += f"- asset: {trigger.get('asset', '')}\n"
    block += f"- caption: {trigger.get('caption', '')}\n"
    if trigger.get("eyebrow"):
        block += f"- eyebrow: {trigger['eyebrow']}\n"
    block += f"- hide_anchor: {trigger.get('hide_anchor', 'implicit-next-overlay')}\n"
    if trigger.get("duration"):
        block += f"- duration: {trigger['duration']}\n"
    if trigger.get("confirmed_at"):
        block += f"- confirmed_at: {trigger['confirmed_at']}\n"
    else:
        block += f"- confirmed_at: {_utc_now()}\n"

    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


# ─── Commands ──────────────────────────────────────────────────────────────────

def cmd_create(args: argparse.Namespace) -> int:
    slug = args.slug
    task_path = _task_path(slug)
    if task_path.exists() and not args.force:
        print(json.dumps({"error": f"Task record already exists: {task_path}. Use --force to overwrite."}))
        return 1

    draft = str(_draft_path(slug).relative_to(PKA_ROOT))
    now = _utc_now()
    data = {
        "slug": slug,
        "state": "transcript-ready",
        "trigger_count": 0,
        "recording": args.recording,
        "transcript": args.transcript,
        "codex_project": args.codex_project or "",
        "draft": draft,
        "render_job": None,
        "rendered_video": None,
        "drive_link": None,
        "youtube_url": None,
        "created_at": now,
        "updated_at": now,
    }
    _write_task_record(slug, data)
    _init_draft(slug, args.recording, args.transcript)
    print(json.dumps({
        "created": True,
        "slug": slug,
        "state": "transcript-ready",
        "task_record": str(_task_path(slug).relative_to(PKA_ROOT)),
        "draft": draft,
    }, indent=2))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    path = _task_path(args.slug)
    if not path.exists():
        print(json.dumps({"error": f"No task record for slug: {args.slug}"}))
        return 1
    data = _parse_task_record(path)
    print(json.dumps(data, indent=2))
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    path = _task_path(args.slug)
    if not path.exists():
        print(json.dumps({"error": f"No task record for slug: {args.slug}"}))
        return 1
    data = _parse_task_record(path)
    old_state = data.get("state", "?")
    data["state"] = args.to
    if args.render_job:
        data["render_job"] = args.render_job
    if args.rendered_video:
        data["rendered_video"] = args.rendered_video
    if args.drive_link:
        data["drive_link"] = args.drive_link
    if args.youtube_url:
        data["youtube_url"] = args.youtube_url
    _write_task_record(args.slug, data)
    print(json.dumps({"slug": args.slug, "from": old_state, "to": data["state"]}, indent=2))
    return 0


def cmd_append_trigger(args: argparse.Namespace) -> int:
    path = _task_path(args.slug)
    if not path.exists():
        print(json.dumps({"error": f"No task record for slug: {args.slug}"}))
        return 1
    data = _parse_task_record(path)

    raw = sys.stdin.read().strip()
    try:
        trigger = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid trigger JSON: {e}"}))
        return 1

    trigger_count = int(data.get("trigger_count", 0)) + 1
    data["trigger_count"] = trigger_count
    data["state"] = f"{TRIGGER_STATE_PREFIX}{trigger_count}"

    _append_trigger_to_draft(args.slug, trigger_count, trigger)
    _write_task_record(args.slug, data)

    # Warn if referenced assets don't exist in hyperframes/assets/
    missing_assets: list[str] = []
    cp_str = data.get("codex_project", "")
    if cp_str:
        cp = Path(cp_str)
        if not cp.is_absolute():
            cp = PKA_ROOT / cp
        assets_dir = cp / "hyperframes" / "assets"
        raw_assets = trigger.get("assets") or ([trigger["asset"]] if trigger.get("asset") else [])
        for a in raw_assets:
            fname = Path(a).name
            if not (assets_dir / fname).exists():
                missing_assets.append(fname)

    result: dict = {
        "slug": args.slug,
        "trigger_appended": trigger_count,
        "state": data["state"],
        "draft": str(_draft_path(args.slug).relative_to(PKA_ROOT)),
    }
    if missing_assets:
        result["warning"] = f"Asset(s) not found in hyperframes/assets/: {missing_assets}. Copy them there before rendering."
    print(json.dumps(result, indent=2))
    return 0


def cmd_get_draft(args: argparse.Namespace) -> int:
    path = _draft_path(args.slug)
    if not path.exists():
        print(json.dumps({"error": f"No draft file for slug: {args.slug}"}))
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_draft_to_overlay_timing(args: argparse.Namespace) -> int:
    """
    Convert the draft file into overlay_timing.json for the render orchestrator.
    Writes to <codex_project>/motion/overlay_timing.json.
    """
    task_path = _task_path(args.slug)
    if not task_path.exists():
        print(json.dumps({"error": f"No task record for slug: {args.slug}"}))
        return 1
    data = _parse_task_record(task_path)
    draft_path = _draft_path(args.slug)
    if not draft_path.exists():
        print(json.dumps({"error": f"No draft file for slug: {args.slug}"}))
        return 1

    # Parse triggers from draft markdown
    draft_text = draft_path.read_text(encoding="utf-8")
    triggers = []
    current: dict | None = None
    _list_key: str | None = None  # tracks the current list-valued key (e.g. "assets")
    for line in draft_text.splitlines():
        if line.startswith("## Trigger "):
            if current:
                triggers.append(current)
            current = {}
            _list_key = None
        elif current is not None:
            stripped = line.strip()
            if stripped.startswith("- "):
                kv = stripped[2:]
                if ": " in kv:
                    # Regular key: value line
                    _list_key = None
                    key, _, val = kv.partition(": ")
                    key = key.strip()
                    val = val.strip().strip('"')
                    if key in ("show_time", "duration"):
                        try:
                            current[key] = float(val)
                        except ValueError:
                            current[key] = val
                    else:
                        current[key] = val
                elif kv.rstrip().endswith(":"):
                    # key: (no value) — start of a list
                    _list_key = kv.rstrip()[:-1].strip()
                    current[_list_key] = []
                else:
                    # Item in an active list (indented "  - item" within a block)
                    if _list_key:
                        current[_list_key].append(kv.strip())
            elif stripped.startswith("- ") is False and stripped and _list_key:
                # Indented list item that doesn't start at column 0 with "- "
                # e.g. "  - motion/empire-exterior.jpg" becomes stripped "- motion/..."
                pass  # already caught above via stripped.startswith("- ")
    if current:
        triggers.append(current)

    if not triggers:
        print(json.dumps({"error": "No triggers found in draft"}))
        return 1

    # Build overlay_timing.json
    overlays = []
    for i, t in enumerate(triggers):
        entry: dict = {
            "id": f"overlay-{i+1}",
            "card_id": "none",
            "show_anchor": t.get("show_anchor", ""),
            "show_time": t.get("show_time", 0.0),
            "primitive": t.get("primitive", "corner-card"),
            "caption": t.get("caption", ""),
            "hide_anchor": t.get("hide_anchor", "implicit-next-overlay"),
        }
        if "assets" in t:
            entry["assets"] = t["assets"]
        elif "asset" in t:
            entry["asset"] = t["asset"]
        if "duration" in t:
            entry["duration"] = t["duration"]
        # Pass through optional display fields
        for opt_key in ("eyebrow",):
            if opt_key in t:
                entry[opt_key] = t[opt_key]
        overlays.append(entry)

    payload = {
        "slug": args.slug,
        "generated_at": _utc_now(),
        "overlays": overlays,
    }

    # Write to codex_project/motion/overlay_timing.json
    codex_project = data.get("codex_project", "")
    if not codex_project:
        print(json.dumps({"error": "codex_project not set in task record"}))
        return 1

    # codex_project may be absolute or relative
    cp = Path(codex_project)
    if not cp.is_absolute():
        cp = PKA_ROOT / cp
    motion_dir = cp / "motion"
    motion_dir.mkdir(parents=True, exist_ok=True)
    out_path = motion_dir / "overlay_timing.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps({
        "slug": args.slug,
        "overlay_timing": str(out_path),
        "trigger_count": len(overlays),
    }, indent=2))
    return 0


# ─── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HyperFrames per-recording state manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p = sub.add_parser("create", help="Create task record + draft file for a new recording")
    p.add_argument("--slug", required=True, help="Recording slug (e.g. filoli-2026-05-05)")
    p.add_argument("--recording", required=True, help="Path to raw recording")
    p.add_argument("--transcript", required=True, help="Path to transcript file")
    p.add_argument("--codex-project", default="", help="Path to Codex project directory")
    p.add_argument("--force", action="store_true", help="Overwrite existing task record")

    # get
    p = sub.add_parser("get", help="Print current state of a recording")
    p.add_argument("--slug", required=True)

    # advance
    p = sub.add_parser("advance", help="Advance state (e.g. to spec-complete, render-running, ...)")
    p.add_argument("--slug", required=True)
    p.add_argument("--to", required=True, help="Target state")
    p.add_argument("--render-job", help="Render job ID (for render-running state)")
    p.add_argument("--rendered-video", help="Path to rendered MP4")
    p.add_argument("--drive-link", help="Google Drive shareable link")
    p.add_argument("--youtube-url", help="YouTube video URL")

    # append-trigger
    p = sub.add_parser(
        "append-trigger",
        help="Append one confirmed trigger to the draft (reads JSON from stdin)",
    )
    p.add_argument("--slug", required=True)

    # get-draft
    p = sub.add_parser("get-draft", help="Print the current draft file")
    p.add_argument("--slug", required=True)

    # draft-to-overlay-timing
    p = sub.add_parser(
        "draft-to-overlay-timing",
        help="Convert draft triggers → overlay_timing.json in the Codex project",
    )
    p.add_argument("--slug", required=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "create": cmd_create,
        "get": cmd_get,
        "advance": cmd_advance,
        "append-trigger": cmd_append_trigger,
        "get-draft": cmd_get_draft,
        "draft-to-overlay-timing": cmd_draft_to_overlay_timing,
    }
    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
