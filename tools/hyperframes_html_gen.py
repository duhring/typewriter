#!/usr/bin/env python3
"""
Generate a HyperFrames index.html from overlay_timing.json + assets.

Two layout primitives cover ~90% of phone-clip overlays:

  corner-card         — image + caption in bottom-right corner (default)
                        Matches Filoli "William Bowers Bourn II" style.

  fullscreen-takeover — image fills left side, label panel on right
                        Matches Filoli "Spring Valley Water Company" map style.

Multi-image sequences (e.g. Empire Mine trio) expand a single overlay entry
into N consecutive corner-card clips at equal durations.

Usage:
    # By slug (looks up codex_project from task record):
    python3 tools/hyperframes_html_gen.py --slug filoli-2026-05-05

    # By explicit paths:
    python3 tools/hyperframes_html_gen.py \\
        --overlay-timing "video-studio/projects/yosemite-clip-1/motion/overlay_timing.json" \\
        --out "video-studio/projects/yosemite-clip-1/hyperframes/index.html"

    # Preview without writing:
    python3 tools/hyperframes_html_gen.py --slug ... --dry-run
"""

from __future__ import annotations

import os
import argparse
import json
import sys
from pathlib import Path
from string import Template

PKA_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = PKA_ROOT / "owners-inbox" / "tasks"
CODEX_VIDEO_ROOT = Path(os.environ.get("PKA_VIDEO_STUDIO", str(PKA_ROOT / "video-studio"))).expanduser() / "projects"

# ─── Template ─────────────────────────────────────────────────────────────────
# Uses string.Template so CSS/JS braces need no escaping; only $var / ${var} is substituted.

_TEMPLATE_SRC = r"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1920, height=1080" />
    <script src="assets/gsap.min.js"></script>
    <style>
      * { margin: 0; padding: 0; box-sizing: border-box; }

      html, body {
        width: 1920px; height: 1080px;
        overflow: hidden;
        background: #090c0e; color: #f8fbfb;
        font-family: Inter, sans-serif;
        letter-spacing: 0;
      }

      #root {
        position: relative; width: 1920px; height: 1080px;
        overflow: hidden; background: #090c0e;
      }

      #video-shell { position: absolute; inset: 0; overflow: hidden; background: #000; }

      #a-roll {
        position: absolute; inset: 0;
        width: 100%; height: 100%; object-fit: cover;
      }

      /* ── Shared clip rules ── */
      .clip.beat {
        position: absolute; opacity: 0;
        pointer-events: none; z-index: 8;
      }

      /* ── corner-card primitive ── */
      .corner-card {
        right: 54px; bottom: 54px; width: 340px;
        background: #f7f9f6; color: #111816;
        border: 7px solid rgba(255,255,255,0.92);
        box-shadow: 0 26px 72px rgba(0,0,0,0.48);
        overflow: hidden;
      }
      .corner-card img {
        display: block; width: 100%; height: 320px;
        object-fit: cover; object-position: center top;
      }
      .corner-card .caption {
        padding: 18px 22px;
        font-size: 22px; font-weight: 880; line-height: 1.2;
        color: #111816; border-top: 3px solid #b8a07a;
      }

      /* ── fullscreen-takeover primitive ── */
      .fullscreen-takeover {
        inset: 0; background: #0a0e10;
        display: grid; grid-template-columns: 1fr 540px;
        overflow: hidden;
      }
      .fullscreen-takeover img {
        display: block; width: 100%; height: 100%;
        object-fit: contain; object-position: center center;
        background: #0a0e10;
      }
      .fullscreen-takeover .label {
        display: flex; flex-direction: column; justify-content: center;
        padding: 64px 72px;
        border-left: 1px solid rgba(255,255,255,0.14);
        background: #0a0e10;
      }
      .fullscreen-takeover .eyebrow {
        font-size: 28px; font-weight: 860;
        text-transform: uppercase; letter-spacing: 0.08em;
        color: #7bd0d1; margin-bottom: 28px;
      }
      .fullscreen-takeover .title {
        font-size: 72px; font-weight: 880; line-height: 1.05;
      }

      /* ── caption-emphasis primitive ── */
      /* Bold centered text overlay — no image. Use for key statements,
         stats, or moments that need emphasis without covering the frame. */
      .caption-emphasis {
        left: 50%; bottom: 96px;
        transform: translateX(-50%);
        width: 1400px;
        background: rgba(9, 12, 14, 0.82);
        border-top: 4px solid #7bd0d1;
        box-shadow: 0 18px 56px rgba(0,0,0,0.54);
        padding: 28px 56px 32px;
        text-align: center;
      }
      .caption-emphasis .caption {
        font-size: 40px; font-weight: 880; line-height: 1.2;
        color: #f8fbfb;
      }
      .caption-emphasis .eyebrow {
        font-size: 22px; font-weight: 860;
        text-transform: uppercase; letter-spacing: 0.09em;
        color: #7bd0d1; margin-bottom: 12px;
      }

      /* ── lower-third primitive ── */
      /* Classic lower-third bar — name/title/stat identification overlay. */
      .lower-third {
        left: 0; right: 0; bottom: 0;
        background: rgba(9, 12, 14, 0.86);
        border-top: 5px solid #7bd0d1;
        box-shadow: 0 -8px 40px rgba(0,0,0,0.48);
        padding: 24px 80px 28px;
      }
      .lower-third .eyebrow {
        font-size: 22px; font-weight: 860;
        text-transform: uppercase; letter-spacing: 0.09em;
        color: #7bd0d1; margin-bottom: 8px;
      }
      .lower-third .caption {
        font-size: 32px; font-weight: 880; line-height: 1.2;
        color: #f8fbfb;
      }

      /* ── inline-left primitive ── */
      /* Left-anchored floating panel — mirrors CueCam's left-layout aesthetic.
         Semi-transparent so the video shows through; teal left bar as accent.
         Use for text labels, species IDs, product names, etc. that should feel
         inline with the content rather than covering it. */
      .inline-left {
        left: 54px; bottom: 80px; width: 700px;
        background: rgba(9, 12, 14, 0.88);
        border-left: 6px solid #7bd0d1;
        box-shadow: 0 18px 56px rgba(0,0,0,0.54);
        overflow: hidden;
      }
      .inline-left img {
        display: block; width: 100%; height: 480px;
        object-fit: cover; object-position: center top;
      }
      .inline-left .body {
        padding: 22px 28px 26px;
      }
      .inline-left .eyebrow {
        font-size: 22px; font-weight: 860;
        text-transform: uppercase; letter-spacing: 0.09em;
        color: #7bd0d1; margin-bottom: 10px;
      }
      .inline-left .caption {
        font-size: 40px; font-weight: 880; line-height: 1.15;
        color: #f8fbfb;
      }
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="${total_duration}"
      data-width="1920"
      data-height="1080"
    >
      <div id="video-shell">
        <video
          id="a-roll"
          src="assets/edited.mp4"
          muted playsinline
          data-start="0"
          data-duration="${total_duration}"
          data-track-index="0"
        ></video>
      </div>
      <audio
        id="a-roll-audio"
        src="assets/edited.mp4"
        data-start="0"
        data-duration="${total_duration}"
        data-track-index="2"
        data-volume="1"
      ></audio>

${overlay_html}
    </div>

    <script>
      window.__timelines = window.__timelines || {};
      const tl = gsap.timeline({ paused: true });

      function showHide(selector, start, duration, from) {
        tl.fromTo(
          selector,
          { opacity: 0, ...from },
          { opacity: 1, x: 0, y: 0, scale: 1, duration: 0.36, ease: "power3.out" },
          start
        );
        tl.to(selector, { opacity: 0, duration: 0.24, ease: "power2.in" }, start + duration - 0.24);
        tl.set(selector, { opacity: 0 }, start + duration);
      }

${gsap_calls}

      tl.to({}, { duration: ${video_duration} }, 0);
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
"""

# ─── Overlay builders ─────────────────────────────────────────────────────────

def _corner_card_html(clip_id: str, asset: str, caption: str,
                       start: float, duration: float, track_idx: int) -> str:
    img_html = f'        <img src="assets/{asset}" alt="" />\n' if asset else ""
    return (
        f'      <!-- corner-card: {caption} -->\n'
        f'      <section\n'
        f'        id="{clip_id}"\n'
        f'        class="corner-card clip beat"\n'
        f'        data-start="{start}"\n'
        f'        data-duration="{duration}"\n'
        f'        data-track-index="{track_idx}"\n'
        f'      >\n'
        f'{img_html}'
        f'        <div class="caption">{caption}</div>\n'
        f'      </section>\n'
    )


def _caption_emphasis_html(clip_id: str, caption: str,
                            start: float, duration: float, track_idx: int,
                            eyebrow: str = "") -> str:
    eyebrow_html = f'        <div class="eyebrow">{eyebrow}</div>\n' if eyebrow else ""
    return (
        f'      <!-- caption-emphasis: {caption} -->\n'
        f'      <section\n'
        f'        id="{clip_id}"\n'
        f'        class="caption-emphasis clip beat"\n'
        f'        data-start="{start}"\n'
        f'        data-duration="{duration}"\n'
        f'        data-track-index="{track_idx}"\n'
        f'      >\n'
        f'{eyebrow_html}'
        f'        <div class="caption">{caption}</div>\n'
        f'      </section>\n'
    )


def _lower_third_html(clip_id: str, caption: str,
                       start: float, duration: float, track_idx: int,
                       eyebrow: str = "") -> str:
    eyebrow_html = f'        <div class="eyebrow">{eyebrow}</div>\n' if eyebrow else ""
    return (
        f'      <!-- lower-third: {caption} -->\n'
        f'      <section\n'
        f'        id="{clip_id}"\n'
        f'        class="lower-third clip beat"\n'
        f'        data-start="{start}"\n'
        f'        data-duration="{duration}"\n'
        f'        data-track-index="{track_idx}"\n'
        f'      >\n'
        f'{eyebrow_html}'
        f'        <div class="caption">{caption}</div>\n'
        f'      </section>\n'
    )


def _fullscreen_html(clip_id: str, asset: str, caption: str,
                      start: float, duration: float, track_idx: int,
                      eyebrow: str = "") -> str:
    # Derive eyebrow from caption if not explicitly supplied:
    #   "Water rights — Spring Valley Water Company" → eyebrow="Water rights", title="Spring Valley..."
    #   Plain caption → eyebrow="" (hidden), title=caption
    title = caption
    if not eyebrow and " — " in caption:
        eyebrow, _, title = caption.partition(" — ")

    # Split long titles across two lines at midpoint
    words = title.split()
    if len(words) >= 3:
        mid = (len(words) + 1) // 2
        title_html = " ".join(words[:mid]) + "<br />" + " ".join(words[mid:])
    else:
        title_html = title

    eyebrow_html = f'          <div class="eyebrow">{eyebrow}</div>\n' if eyebrow else ""

    return (
        f'      <!-- fullscreen-takeover: {caption} -->\n'
        f'      <section\n'
        f'        id="{clip_id}"\n'
        f'        class="fullscreen-takeover clip beat"\n'
        f'        data-start="{start}"\n'
        f'        data-duration="{duration}"\n'
        f'        data-track-index="{track_idx}"\n'
        f'      >\n'
        f'        <img src="assets/{asset}" alt="" />\n'
        f'        <div class="label">\n'
        f'{eyebrow_html}'
        f'          <div class="title">{title_html}</div>\n'
        f'        </div>\n'
        f'      </section>\n'
    )


def _inline_left_html(clip_id: str, asset: str, caption: str,
                       start: float, duration: float, track_idx: int,
                       eyebrow: str = "") -> str:
    """Left-anchored floating panel — CueCam left-layout style.
    asset is optional: when empty no image is rendered (text-only label).
    eyebrow is optional: small teal category label above the caption.
    """
    img_html     = f'        <img src="assets/{asset}" alt="" />\n' if asset else ""
    eyebrow_html = f'          <div class="eyebrow">{eyebrow}</div>\n' if eyebrow else ""
    # Only render .body when there is visible text — avoids dark empty padding bar
    if caption or eyebrow:
        body_html = (
            f'        <div class="body">\n'
            f'{eyebrow_html}'
            f'          <div class="caption">{caption}</div>\n'
            f'        </div>\n'
        )
    else:
        body_html = ""
    return (
        f'      <!-- inline-left: {caption or "(image-only)"} -->\n'
        f'      <section\n'
        f'        id="{clip_id}"\n'
        f'        class="inline-left clip beat"\n'
        f'        data-start="{start}"\n'
        f'        data-duration="{duration}"\n'
        f'        data-track-index="{track_idx}"\n'
        f'      >\n'
        f'{img_html}'
        f'{body_html}'
        f'      </section>\n'
    )


def _gsap_corner(clip_id: str, start: float, duration: float) -> str:
    return f'      showHide("#{clip_id}", {start}, {duration}, {{ x: 54, y: 24, scale: 0.97 }});'


def _gsap_fullscreen(clip_id: str, start: float, duration: float) -> str:
    return f'      showHide("#{clip_id}", {start}, {duration}, {{ y: 48 }});'


def _gsap_inline_left(clip_id: str, start: float, duration: float) -> str:
    return f'      showHide("#{clip_id}", {start}, {duration}, {{ x: -48, scale: 0.97 }});'


def _gsap_caption_emphasis(clip_id: str, start: float, duration: float) -> str:
    return f'      showHide("#{clip_id}", {start}, {duration}, {{ y: 32, scale: 0.98 }});'


def _gsap_lower_third(clip_id: str, start: float, duration: float) -> str:
    return f'      showHide("#{clip_id}", {start}, {duration}, {{ y: 48 }});'


# ─── Overlay resolution ───────────────────────────────────────────────────────

_DEFAULT_OVERLAY_DURATION = 8.0   # seconds when not explicitly set
_SEQUENCE_ITEM_DURATION   = 5.0   # seconds per image in a multi-image sequence


def _resolve_overlays(overlays: list[dict], video_duration: float) -> list[dict]:
    """
    Expand overlay_timing.json entries into flat clip specs.
    Handles multi-image sequences (assets:[]) by splitting into N equal clips.
    Computes hide times for implicit-next-overlay.
    """
    flat: list[dict] = []

    for ov in overlays:
        show_time  = float(ov.get("show_time", 0.0))
        primitive  = ov.get("primitive", "corner-card")
        caption    = ov.get("caption", "")
        hide       = ov.get("hide_anchor", "implicit-next-overlay")
        explicit_d = float(ov["duration"]) if "duration" in ov else None

        assets_raw = ov.get("assets") or ([ov["asset"]] if ov.get("asset") else [])
        # Strip any path prefix if assets are referenced with motion/ prefix
        assets = [Path(a).name for a in assets_raw]

        eyebrow = ov.get("eyebrow", "")

        if len(assets) > 1:
            # Multi-image sequence — equal slots, ignore hide_anchor
            per = explicit_d or _SEQUENCE_ITEM_DURATION
            t = show_time
            for i, asset in enumerate(assets):
                flat.append({
                    "id":        f"{ov['id']}-{chr(ord('a')+i)}",
                    "primitive": "corner-card",  # sequences are always corner-cards
                    "asset":     asset,
                    "caption":   caption,
                    "eyebrow":   eyebrow,
                    "start":     round(t, 3),
                    "duration":  round(per, 3),
                })
                t += per
        else:
            asset = assets[0] if assets else ""
            flat.append({
                "id":        ov["id"],
                "primitive": primitive,
                "asset":     asset,
                "caption":   caption,
                "eyebrow":   eyebrow,
                "start":     round(show_time, 3),
                "duration":  None,          # resolved in next pass
                "hide":      hide,
                "explicit_d": explicit_d,
            })

    # Second pass: resolve durations for non-sequence clips
    resolved: list[dict] = []
    for i, clip in enumerate(flat):
        if clip.get("duration") is not None:
            resolved.append(clip)
            continue
        exp = clip.get("explicit_d")
        if exp is not None:
            clip["duration"] = round(exp, 3)
        elif clip.get("hide") == "implicit-next-overlay":
            # Find next clip's start
            next_clips = [c for c in flat[i+1:] if c.get("duration") is None or c.get("duration", 0) > 0]
            if next_clips:
                clip["duration"] = round(next_clips[0]["start"] - clip["start"], 3)
            else:
                clip["duration"] = round(video_duration - clip["start"], 3)
        else:
            # card-end or unknown
            clip["duration"] = round(video_duration - clip["start"], 3)
        clip["duration"] = max(clip["duration"], 0.5)
        resolved.append(clip)

    return resolved


# ─── HTML generator ───────────────────────────────────────────────────────────

def generate(overlay_timing: dict, video_duration: float | None = None) -> str:
    """
    Generate index.html content from an overlay_timing dict.
    video_duration is auto-detected from the last overlay end if not provided.
    """
    overlays_raw = overlay_timing.get("overlays", [])
    clips        = _resolve_overlays(overlays_raw, video_duration or 999.0)

    # Auto-detect video duration from last clip end if not given
    if video_duration is None:
        ends = [c["start"] + c["duration"] for c in clips]
        video_duration = max(ends) if ends else 60.0
    total_duration = round(video_duration, 3)

    overlay_html_parts: list[str] = []
    gsap_parts:         list[str] = []
    base_track = 6

    for idx, clip in enumerate(clips):
        track = base_track + idx
        cid   = clip["id"].replace(".", "-")
        start = clip["start"]
        dur   = clip["duration"]

        if clip["primitive"] == "fullscreen-takeover":
            overlay_html_parts.append(_fullscreen_html(
                cid, clip["asset"], clip["caption"], start, dur, track,
                eyebrow=clip.get("eyebrow", ""),
            ))
            gsap_parts.append(_gsap_fullscreen(cid, start, dur))
        elif clip["primitive"] == "inline-left":
            overlay_html_parts.append(_inline_left_html(
                cid, clip["asset"], clip["caption"], start, dur, track,
                eyebrow=clip.get("eyebrow", ""),
            ))
            gsap_parts.append(_gsap_inline_left(cid, start, dur))
        elif clip["primitive"] == "caption-emphasis":
            overlay_html_parts.append(_caption_emphasis_html(
                cid, clip["caption"], start, dur, track,
                eyebrow=clip.get("eyebrow", ""),
            ))
            gsap_parts.append(_gsap_caption_emphasis(cid, start, dur))
        elif clip["primitive"] == "lower-third":
            overlay_html_parts.append(_lower_third_html(
                cid, clip["caption"], start, dur, track,
                eyebrow=clip.get("eyebrow", ""),
            ))
            gsap_parts.append(_gsap_lower_third(cid, start, dur))
        else:
            overlay_html_parts.append(_corner_card_html(cid, clip["asset"], clip["caption"], start, dur, track))
            gsap_parts.append(_gsap_corner(cid, start, dur))

    overlay_html = "".join(overlay_html_parts)
    gsap_calls   = "\n".join(gsap_parts)

    return Template(_TEMPLATE_SRC).substitute(
        total_duration = total_duration,
        video_duration = total_duration,
        overlay_html   = overlay_html,
        gsap_calls     = gsap_calls,
    )


# ─── Slug lookup ─────────────────────────────────────────────────────────────

def _slug_to_codex_project(slug: str) -> Path | None:
    """Look up codex_project path from the task record for this slug."""
    task_path = TASKS_DIR / f"{slug}.md"
    if not task_path.exists():
        return None
    for line in task_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        for prefix in ("- **Codex project:**", "- codex_project:", "codex_project:"):
            if stripped.lower().startswith(prefix.lower()):
                val = stripped[len(prefix):].strip().strip("`").strip("*").strip()
                if val:
                    return Path(val)
    return None


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate HyperFrames index.html from overlay_timing.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Source: slug (preferred) OR explicit paths
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--slug", help="Recording slug — auto-resolves overlay-timing + output paths")
    src.add_argument("--overlay-timing", help="Path to overlay_timing.json")

    parser.add_argument("--out", help="Output path for index.html (required when using --overlay-timing)")
    parser.add_argument("--video-duration", type=float, default=None,
                        help="Explicit video duration in seconds (default: inferred from edited.mp4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print generated HTML to stdout instead of writing")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing index.html")
    args = parser.parse_args()

    if not args.slug and not args.overlay_timing:
        parser.error("Provide --slug or --overlay-timing")

    # ── Resolve paths ──
    if args.slug:
        cp = _slug_to_codex_project(args.slug)
        if cp is None:
            print(json.dumps({"error": f"No task record / codex_project for slug: {args.slug}"}))
            return 1
        if not cp.is_absolute():
            cp = CODEX_VIDEO_ROOT / cp if (CODEX_VIDEO_ROOT / cp).exists() else PKA_ROOT / cp
        timing_path = cp / "motion" / "overlay_timing.json"
        out_path    = cp / "hyperframes" / "index.html"
        assets_dir  = cp / "hyperframes" / "assets"
    else:
        timing_path = Path(args.overlay_timing)
        if not timing_path.is_absolute():
            timing_path = PKA_ROOT / timing_path
        if not args.out:
            parser.error("--out is required when using --overlay-timing")
        out_path   = Path(args.out)
        if not out_path.is_absolute():
            out_path = PKA_ROOT / out_path
        assets_dir = out_path.parent / "assets"

    if not timing_path.exists():
        print(json.dumps({"error": f"overlay_timing.json not found: {timing_path}"}))
        return 1

    if out_path.exists() and not args.dry_run and not args.force:
        print(json.dumps({"error": f"index.html already exists: {out_path}. Use --force to overwrite."}))
        return 1

    overlay_timing = json.loads(timing_path.read_text(encoding="utf-8"))

    # Auto-detect video duration from hyperframes/assets/edited.mp4 → edit/edited.mp4
    video_duration = args.video_duration
    if video_duration is None:
        for candidate in [assets_dir / "edited.mp4", assets_dir.parent.parent / "edit" / "edited.mp4"]:
            if candidate.exists():
                import subprocess
                try:
                    r = subprocess.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json",
                         "-show_format", str(candidate)],
                        capture_output=True, text=True, timeout=15,
                    )
                    fmt = json.loads(r.stdout).get("format", {})
                    video_duration = float(fmt.get("duration", 0)) or None
                    if video_duration:
                        break
                except Exception:
                    pass

    html = generate(overlay_timing, video_duration)

    if args.dry_run:
        print(html)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    
    # Stage local GSAP copy
    _stage_gsap_locally(assets_dir)
    
    print(json.dumps({
        "written": str(out_path),
        "overlay_count": len(overlay_timing.get("overlays", [])),
        "video_duration": video_duration,
    }, indent=2))
    return 0


def _stage_gsap_locally(assets_dir: Path) -> None:
    """
    Ensures that a local copy of gsap.min.js is placed in the project's assets/ directory.
    Uses a local cache under PKA_ROOT/tools/assets/gsap.min.js, downloading it if necessary.
    """
    import urllib.request
    import shutil
    
    cache_dir = PKA_ROOT / "tools" / "assets"
    cache_path = cache_dir / "gsap.min.js"
    
    # Ensure local cache exists
    if not cache_path.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        url = "https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"
        try:
            print(f"Downloading GSAP to cache: {url} -> {cache_path}", file=sys.stderr)
            with urllib.request.urlopen(url, timeout=15) as response:
                cache_path.write_bytes(response.read())
        except Exception as e:
            raise RuntimeError(f"Failed to download GSAP to cache: {e}. Please place gsap.min.js at {cache_path} manually.")
            
    # Ensure project's assets directory exists
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest_path = assets_dir / "gsap.min.js"
    
    # Copy from cache to destination
    try:
        shutil.copy2(cache_path, dest_path)
    except Exception as e:
        raise RuntimeError(f"Failed to copy GSAP to project assets: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
