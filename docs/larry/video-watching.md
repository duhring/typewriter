# Frame-Aware Video Watching

Larry handles "watch this video" requests directly with
`tools/watch_video_capture.py`. The tool downloads a video (or reads a local
file), samples frames **on shot changes** (not on a timer), pulls the
transcript, and writes an indexed markdown brief. Use this when the owner wants
to know what is *shown* on screen — not just what is said.

This is a **hybrid workflow**: the tool is deterministic, but the owner's
phrasing and the right `--for` mode can be ambiguous. Confirm the mode if it
isn't obvious. It is automation-like, so no Vera pass is needed unless the
output becomes a public-facing artifact.

## When this applies (the watch shape)

A video source **plus** an explicit ask to see it:

- A YouTube / Loom / Zoom / Vimeo link, or an attached `.mov`/`.mp4` clip, AND
- intent like "watch this," "what's on screen," "what's shown," "analyze the
  video," "visual analysis," "frame by frame."

A **bare URL**, or "write the description / timestamps / thumbnail," is *not*
this shape — that is Maven packaging (`docs/larry/youtube-writing.md`). A
request to *build cards / teleprompter / motion graphics* is CueCam
(`docs/larry/cuecam.md`). The Discord bridge already classifies the watch shape
as the `video_watch` task family in `_infer_task_kind()`.

## Recipe

1. **Run the tool** with the canonical venv so indexing uses the right
   environment:

   ```
   discord-bridge/venv/bin/python3 tools/watch_video_capture.py <url-or-path> --for <mode>
   ```

2. **Pick `--for` by intent:**
   - `maven` — competitor hook breakdowns, thumbnail-grounding frames (0–10s hook beats, higher resolution)
   - `reed` — external talks/demos for a blog or essay (narrative visual moments)
   - `wiki` — recurring/strategic topic to compile later (source frames for Dreamer)
   - `hyperframes` — anchor-candidate selection (nearest verbatim cue per shot)
   - `general` — default when none of the above fits

3. **Narrow long videos.** Add `--start MM:SS --end MM:SS` to focus on a
   segment (denser sampling). Use `--no-whisper` when captions exist or the
   owner only wants frames. `--scene-threshold` (default 0.3) can be lowered for
   busier cutting or raised for calmer footage.

4. **Report the result.** The tool prints the brief path. Output lands at
   `owners-inbox/transcripts/watched-<slug>.md` with frames in
   `watched-<slug>/frames/` and is auto-indexed (category `transcript`). Lead
   with what was on screen plus the key transcript points; link the brief.

## Discord 5-minute window discipline

Download + scene extraction + Whisper can exceed Discord's ~5-minute turn,
especially on long or caption-less videos.

- **Short clips (≤ ~10 min) or a windowed `--start/--end`:** safe to run inline.
- **Long videos, or no captions (Whisper must run):** acknowledge first, then
  run it as a local/background job and reconcile — do **not** block the turn
  waiting on a 30-minute course. This mirrors the two-phase video and bulk
  checkbook discipline.
- **Captions present:** pass `--no-whisper` to stay fast.

## Caveats

- **HyperFrames rendering still needs word-level Whisper.** VTT is
  segment-level, so `--for hyperframes` emits anchor *candidates* only; the
  actual render uses the existing local Whisper step in `tools/hyperframes_*.py`.
- **Each machine is standalone under federation.** Any peer may pull frames
  and write/index durable briefs to its own local `data/pka.db`. See
  `docs/federation.md`.
- **Provenance:** the scene-detection engine is lifted from
  `taoufik123-collab/claude-watch` (MIT, © Bradley Bonanno), pinned `7871c7e`.
  No plugin, SessionStart hook, or `obsidian://` path is used.
