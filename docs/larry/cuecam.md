# Larry CueCam Reference

Read this file when creating CueCam Presenter files from Google Docs, local files, or Discord media/spec notes.

Larry handles CueCam generation directly using `tools/cuecam.py`.

## Common Commands

```bash
# Create from a Google Doc
discord-bridge/venv/bin/python3 tools/cuecam.py create --doc-id DOC_ID --upload

# Create from a mobile-style spec plus media
discord-bridge/venv/bin/python3 tools/cuecam.py compose --spec-file team-inbox/discord/NOTE.md --asset team-inbox/discord/IMG1.jpg --asset team-inbox/discord/CLIP1.mov

# Create from local markdown
discord-bridge/venv/bin/python3 tools/cuecam.py from-file --file owners-inbox/blog/2026-03-28-post.md --title "My Talk"

# List presentations
discord-bridge/venv/bin/python3 tools/cuecam.py list
```

## Routing Rules

### Google Doc to CueCam

When the owner sends a Google Doc URL with a presentation request:

1. Extract the doc ID
2. Run:

```bash
discord-bridge/venv/bin/python3 tools/cuecam.py create --doc-id DOC_ID --upload
```

3. Return the local path and any upload/download link

### Discord Media to CueCam

When the owner sends a Discord note plus media attachments describing cards:

1. Use the saved Discord note path as `--spec-file`
2. Pass attached media paths in received order with repeated `--asset`
3. Run:

```bash
discord-bridge/venv/bin/python3 tools/cuecam.py compose ...
```

4. If the owner explicitly asks for a preview first, run the same command with `--preview` and summarize the parsed cards before building

## Mobile Card Grammar

- When the owner dictates in loose natural prose, rewrite it internally into the normalized CueCam spec before calling `tools/cuecam.py compose`
- Preserve the owner's intent while making the structure explicit: deck defaults first, then one line per card
- Global defaults may be written as `layout: left|right|lower third|middle` and `images: inline|background`
- Natural variants like `Left layout` should be interpreted as defaults too
- Deck-wide design defaults may also be written as:

```text
theme: cyber-glass-max
background: #0D1117
font: HelveticaNeue-Bold
text: light
pip: off
```

Available Theme Presets:
- `cyber-glass-max`: Dark Glassmorphism, 112pt text, Emerald glow, Green Screen (`pip: off`)
- `swiss-max`: Swiss Modernist, 112pt/144pt text, Amber badges, Green Screen (`pip: off`)
- `warm-editorial`: Espresso background, 112pt Georgia serif, Green Screen (`pip: off`)
- `broadcast-alpha`: Transparent overlay style, 108pt text
- `sage-workshop`: Sage Slate background, 112pt Futura font, Ochre accents


- `transparent`, `clear`, and `none` are valid background color values (sets alpha to 0)
- 8-digit hex codes (`#RRGGBBAA`) are also accepted when you need a specific color with partial transparency

- Each normal card line should look like:

```text
1. "Headline", teleprompter notes
```

- Per-card overrides may be prefixed inline:

```text
2. right background "Headline", notes
```

- PiP may be controlled per card too:

```text
3. center pip off "Tomatoes are starting to come in.", Takes me back to working in a cannery.
```

- Centered or emphasis cards may also carry a per-card font-size override:

```text
3. center pip off font 41.3 "Tomatoes are starting to come in.", Takes me back to working in a cannery.
```

- Quote cards use an explicit `quote` intent so the line renders as a CueCam blockquote with attribution instead of a headline card:

```text
4. quote center pip off "Make hay while the sun shines.", English Proverb
```

- Quoted text is always the on-screen card text
- Text after the comma is always teleprompter notes
- For `quote` cards, text after the comma is attribution, not teleprompter notes
- For `quote` cards, CueCam text should be emitted as `> Quote text ~ Attribution`
- A text-only cue card with no on-screen headline uses:

```text
2. teleprompter: something to read on camera
```

- A teleprompter-only card does not consume an attachment unless explicitly made into an image or movie card
- A card whose image should stand alone with no on-screen headline uses the `image:` form, which consumes the next attachment like a normal card:

```text
5. image: Joe and his father programmed HP minicomputers to make a word processor and a spreadsheet.
```

- Use `image:` for interleaved decks where the stills carry the beats and the words are spoken, not titled. The text after the colon is teleprompter only
- `middle` should be normalized to CueCam's `center` layout
- `centered` should be normalized to CueCam's `center` layout
- `pip off` / `without picture in picture` means `pictureInPicture: false`
- `pip on` / `with picture in picture` means `pictureInPicture: true`

### Dictation Workflow

If the owner speaks a richer dictation such as:

```text
Use a clean theme with a green background and left layout.
The text should be in light Futura font.
For Card One, the headline should be "My Day at the Farmers Market."
The teleprompter for this card should say "There's lots of asparagus."
Include the first image attached.
```

Larry should first rewrite that into a normalized internal spec like:

```text
theme: clean
background: green
layout: left
font: Futura-Light

1. "My Day at the Farmers Market.", There's lots of asparagus.
```

If the owner asks for a relevant quote and attribution, Larry should resolve the quote first and then normalize it into:

```text
4. quote center pip off "Make hay while the sun shines.", English Proverb
```

Then run `tools/cuecam.py compose` on the normalized spec. The tool should be stable; Larry should carry the more flexible dictation burden.

**Optional Grok shortcut: `--raw-prose`.** When dictation is unusually long or messy, Larry can hand the raw prose to `tools/cuecam.py compose --raw-prose <file>`. Grok normalizes the prose into the spec format above, saves it as a sibling `<source>.normalized.spec` for traceability, then runs the deterministic parser unchanged. On parser failure, the JSON error surfaces `normalized_from_prose: true` plus the spec path so Larry can inspect or hand-edit before retrying. This is a Larry-judgment call, not the default — quote attribution, single-card dictations, and short specs are still cleaner to rewrite inline.

## Locating Drive-referenced media

When the owner names a video or image that lives in Google Drive ("the 3bird video", "the rough cut"), do not ask them to download it manually. Run `tools/gdrive.py search --name "<name>" --kind video` (or `--kind image`) and, when the match is unambiguous, append `--download owners-inbox/cuecam-assets/` to materialize the file locally. Pass the resulting local path as `--asset` to `tools/cuecam.py compose`. See `docs/larry/google-tools.md` for the full Drive search reference, including the `drive.file` scope caveat.

## Media Rules

- Attachment order matches card order
- Image attachments can be inline or background
- Video attachments should emit movie cards instead of image markup
- For movie cards, attachment order still matches card order
- If a line starts like a card (`2:` / `2.` / `Card 2`) but does not contain a quoted headline or teleprompter-only form, treat it as malformed and ask the owner to fix that exact line instead of building a partial deck

## Other Notes

- H1, H2, and H3 headings in source docs become separate cards
- Images in Google Docs are downloaded into the `Media/` folder automatically
- Output is saved to `owners-inbox/presentations/` as a `.cuecam` bundle
- New CueCam bundles are indexed automatically into both `knowledge_base` and `files`
- `--upload` pushes a zipped copy to the Google Drive PKA folder
- The owner can open the `.cuecam` bundle directly in CueCam Presenter on Mac

## Recording Cleanup Intake

For future raw recordings created in:

```text
~/Movies/CueCam Presenter Recordings  (override with PKA_RECORDINGS_DIR)
```

use `tools/cuecam_recording_intake.py` as the first step before downstream transcript, HyperFrames, Maven, or publishing work.

Baseline the existing archive once:

```bash
discord-bridge/venv/bin/python3 tools/cuecam_recording_intake.py init
```

Check for future recordings that are stable and not yet processed:

```bash
discord-bridge/venv/bin/python3 tools/cuecam_recording_intake.py scan
```

Clean the next future recording:

```bash
discord-bridge/venv/bin/python3 tools/cuecam_recording_intake.py process --limit 1
```

For a fast silence-only first pass, use:

```bash
discord-bridge/venv/bin/python3 tools/cuecam_recording_intake.py process --limit 1 --no-stumbles
```

The intake state lives at `data/cuecam_recording_intake.json`. Do not bulk-process the current archive unless the owner explicitly asks for backfill; this workflow is for new recordings going forward.

## YouTube Pipeline — Discord-Triggered Paths

The owner's default is the gated manual-first workflow in
`docs/larry/video-production.md`. Larry cleans dead air and hands the result
back, the owner edits externally, final-master QC and approval happen, then
packaging and private upload run as separate commands. Keep the phases distinct.

### Phase 1 — Clean only, then hand back (no transcript, no description, no upload)

Trigger phrases: "clean this video: <path>", "prep <path> for editing", "remove the silences from <path>", "clean my latest recording for editing"

1. Resolve the source video:
   - if the owner names a path, use it
   - if the owner says "my latest recording", auto-detect the most recently modified `CueCam Recording *.mov` in the recordings folder
   - if the owner names a Drive video, locate it first via `tools/gdrive.py search` (see "Locating Drive-referenced media")
2. Run a **silence-only** clean (leave words intact so the owner has full material for the manual edit):

```bash
discord-bridge/venv/bin/python3 tools/clean_video.py "<source-video>" --no-stumbles
```

3. Report back the cleaned file path (it lands as `<source>_cleaned.mp4` alongside the source), and explicitly tell the owner it is **ready for their manual edit**. Close the turn with: "When the edited copy is ready, tell me where it lives and I'll finish and publish it."
4. Stop here. Do not transcribe, run Maven, generate a thumbnail, or upload.

### Phase 2 — Finish & publish the edited copy → private URL

Trigger phrases: "the edited copy is at <path>, finish it", "publish <path> to YouTube", "<filename>.mp4 is in Downloads, finish and upload it"

The owner's edited copies typically land in `~/Downloads` (post-CapCut export). If they name a path, use it; otherwise default to the most recently modified video in `~/Downloads`.

First run `tools/video_qc.py <edited-file> --project <slug>` and obtain John's
master approval. Then run `tools/pipeline.py package --project <slug>`, review
and approve the title/description/thumbnail package, and only then run
`tools/pipeline.py upload --project <slug>`. Upload is private; changing privacy
remains a separate explicit owner decision after YouTube QA.

### One-shot path (no manual edit)

If the owner explicitly wants a fresh raw recording cleaned and published in a single pass with no manual edit — trigger phrases "process my latest recording", "clean and upload", "run the full pipeline" — run all 5 steps:

```bash
python3 tools/pipeline.py one-shot --privacy private --yes
```

Takes ~6–8 minutes. Confirm the YouTube URL and title options when done.

**Note:** only `pipeline.py one-shot` auto-detects the newest recording. The
manual-first `package` and `upload` commands resolve artifacts from the video
project manifest.

## HyperFrames Authoring Flow — Quick Recipe (do these in order)

This is the copy-paste recipe to follow when you see a Discord message containing `hyperframes`/`hyperframe`/`motion`/`motion graphics` cards. The goal is finish-fast — under one minute, not five.

1. **Strip the spec.** Save the Discord spec text to `/tmp/cuecam-spec.txt`, but FIRST:
   - drop any title/header line that appears before the first numbered card (e.g. "2026-04-26 butterfly test deck")
   - drop every indented `on "..."` and `off "..."` line — those are sidecar fodder, not bundle fodder
   - replace each `hyperframes "Title", text` line with `lower-third "Title", text`
2. **Compose.** Run:
   ```bash
   discord-bridge/venv/bin/python3 tools/cuecam.py compose \
     --spec-file /tmp/cuecam-spec.txt \
     --title "<short title without leading date — cuecam.py prefixes today's date automatically>"
   ```
3. **Find the bundle.** The JSON output names the resulting bundle path. Read its `Script.json`.
4. **Write the sidecar.** Save it next to the bundle as `<bundle-stem>.hyperframes.md` using the template below. For each hyperframes card you parsed in step 1:
   - copy the `card_id` from the matching card's UUID in `Script.json` (cards are in spec order)
   - apply default-hide rules in this order: explicit `off "..."` → `implicit-next-overlay` if a later overlay exists in the same card → `card-end`
   - skip regular CueCam cards entirely; only HyperFrames cards belong in the sidecar
5. **Index the sidecar.**
   ```bash
   discord-bridge/venv/bin/python3 tools/pka_index.py index-markdown \
     --file owners-inbox/presentations/<bundle-stem>.hyperframes.md \
     --category cuecam-hyperframes \
     --tags "hyperframes, cuecam, motion-graphics" \
     --summary "<deck title> — N hyperframes cards, M overlays"
   ```
6. **Reply** with both paths and the KB id.

The longer narrative below explains the *why*; the recipe above is what you actually do.

## HyperFrames Authoring Flow

When a Discord CueCam authoring message contains one or more `hyperframes` cards (intent markers: `hyperframes`, `hyperframe`, `motion`, `motion graphics`), follow this flow.

### 1. Parse and normalize

For each `hyperframes` card line in the spec:

- Capture the quoted card title and the teleprompter line after the comma.
- For each indented `on "anchor": description` line, build an overlay record with `show_anchor` and `content`.
- For each indented `off "anchor"` line, attach `hide_anchor` to the immediately preceding overlay.
- For overlays without explicit `off`, leave `hide_anchor` to be filled in step 4.

### 2. Compose the bundle as today

Build the regular cards (non-hyperframes) and the hyperframes cards together into one card list. Hyperframes cards compile to:

- `slide.layout: "lower-third"`
- `text:` the teleprompter line only (no `# Heading` — the recording shows a clean cue card)
- `pictureInPicture: true`

Run `tools/cuecam.py compose` to produce `owners-inbox/presentations/<deck-slug>.cuecam` exactly as today.

### 3. Read UUIDs back from Script.json

Open `<deck-slug>.cuecam/Script.json` and read each card's `id`. Map card index → UUID so the sidecar can reference each hyperframes card by its real identifier.

### 4. Emit the sidecar

Write `owners-inbox/presentations/<deck-slug>.hyperframes.md` using this shape:

```markdown
---
deck: <deck-slug>.cuecam
authored: YYYY-MM-DD
source: discord
---

# HyperFrames spec — <deck-slug>

## Card N — <Card Title>
- card_id: <UUID from Script.json>
- teleprompter: "<teleprompter line>"

### Overlays
- show_anchor: "<phrase>"
  content: <overlay description>
  hide_anchor: "<phrase>" | implicit-next-overlay | card-end
```

Apply the default-hide rules during emit:

- If the overlay has its own `off "..."`, use that phrase.
- Else, if a later overlay exists in the same card, set `hide_anchor: implicit-next-overlay`.
- Else, set `hide_anchor: card-end`.

Only include `## Card N` sections for cards that are HyperFrames cards. Skip regular CueCam cards entirely.

### 5. Index and reply

Index the sidecar:

```bash
discord-bridge/venv/bin/python3 tools/pka_index.py index-markdown \
  --file owners-inbox/presentations/<deck-slug>.hyperframes.md \
  --category cuecam-hyperframes \
  --tags "hyperframes, cuecam, motion-graphics" \
  --summary "<Deck Title> — N hyperframes cards, M overlays"
```

Reply to Discord with both paths so John has confirmation:

```
Bundle: owners-inbox/presentations/<deck-slug>.cuecam
Sidecar: owners-inbox/presentations/<deck-slug>.hyperframes.md
Indexed as KB #<id> (category cuecam-hyperframes).
```

## HyperFrames Render Workflow (desk-side, manual for field test)

During the field-test phase the post-production intake (`cuecam_recording_intake.py`) detects when a recording's source deck has a HyperFrames sidecar, runs silence removal as today, exports the word-level transcript, and surfaces a next-step block. The actual overlay render happens manually at desk via the Codex video-studio pipeline.

When a recording is in `status: hyperframes-pending`, the intake state entry will list:

- `cleaned_video` — silence-removed video produced by `clean_video.py`
- `word_transcript` — JSON file with Whisper word-level timestamps (see shape below)
- `sidecar` — path to the `.hyperframes.md` for the source deck

Manual desk-side steps:

1. Open the Codex video-studio repo at `video-studio/` inside the PKA root (override with PKA_VIDEO_STUDIO).
2. Stage a project folder for this recording.
3. **Keyframe check before render.** CueCam Presenter recordings often have sparse keyframes, which can cause frozen frames during Codex capture. Check and re-encode if needed:

   ```bash
   # Check keyframe interval (look for large gaps between I-frames)
   ffprobe -select_streams v -show_frames -show_entries frame=pict_type,pkt_pts_time \
     cleaned.mov | grep "pict_type=I" | head -20
   ```

   If gaps exceed ~2 seconds, re-encode with 1-second keyframes before handing to Codex:

   ```bash
   ffmpeg -i cleaned.mov \
     -c:v libx264 \
     -force_key_frames "expr:gte(t,n_forced*1)" \
     -c:a copy \
     cleaned_kf1s.mov
   ```

   Use `cleaned_kf1s.mov` as the `cleaned_video` input for the rest of the steps.

4. Run the Codex bin scripts in sequence (anchor mapping → rough cut → render) using the three artifacts as inputs. The rendered video lands wherever Codex writes it.
5. After render, update the intake state entry from `status: hyperframes-pending` to `status: rendered` (manual edit of `data/cuecam_recording_intake.json` for now).
6. To produce a polish-pass bundle, tell Larry: "build the polish-pass bundle" with the rendered video path — Larry will run `tools/cuecam.py polish` and hand back the new `.cuecam` path.

This manual step will be wrapped into a `tools/codex_hyperframes_render.py` orchestrator after field-test feedback stabilizes the grammar and the workflow.

### Transcript JSON shape

The intake exports the word-level transcript next to the cleaned video as `<recording-stem>_cleaned.words.json`:

```json
{
  "language": "en",
  "duration_s": 312.4,
  "words": [
    {"word": "I've", "start": 0.32, "end": 0.51},
    {"word": "been", "start": 0.51, "end": 0.74}
  ]
}
```

The Codex anchor matcher reads this directly to find each `show_anchor` / `hide_anchor` phrase in the cleaned timeline.

### Pairing recordings to sidecars

`cuecam_recording_intake.py` auto-pairs a recording to a sidecar when there is exactly one `*.hyperframes.md` whose mtime is within ±7 days of the recording (configurable via `--sidecar-window-days`). When multiple sidecars match, the intake records all of them under `sidecar_candidates` and instructs the explicit pair subcommand:

```bash
discord-bridge/venv/bin/python3 tools/cuecam_recording_intake.py pair \
  "<recording-path>" \
  "owners-inbox/presentations/<deck-slug>.hyperframes.md"
```

This overrides any auto-pairing and sets `auto_paired: false`.

## Post-Production HyperFrames Analysis Flow

**Trigger:** owner says "add overlays", "do hyperframes analysis", "analyze the transcript", "map the overlays", or similar — after a recording is in `status: transcript-ready` (or `rendered`).

Larry handles this flow inline (no subagent). Goal: produce a reviewed `.hyperframes.md` sidecar from the actual spoken transcript, rather than from anchors written before recording.

### Steps

**1. Load intake state.**

```bash
cat data/cuecam_recording_intake.json
```

Find the recording entry. Confirm `status` is `transcript-ready` (or `rendered`). Note the `cleaned_video`, `word_transcript`, and the deck slug from the recording filename.

**2. Read the word transcript.**

```bash
cat /path/to/recording_cleaned.words.json
```

Scan the `words` array (each entry: `{"word": "...", "start": float, "end": float}`). Build a mental picture of the full spoken text.

**3. Find the source deck.**

The deck slug usually appears in the recording filename or can be found by matching mtime. Read its `Script.json`:

```bash
cat "owners-inbox/presentations/<deck-slug>.cuecam/Script.json"
```

Note the card order and each card's `text` field (the teleprompter line).

**4. Map card boundaries.**

For each card's teleprompter text, find the window in the word transcript where that text was spoken. This gives an approximate `[t_start, t_end]` per card. Cards that are `teleprompter:` or `movie:` form cards have the spoken cue as their text field.

**5. Propose verbatim anchor phrases.**

Within each card's time window, identify noun-dense or visually interesting spans where an overlay would add value. Write the anchor as the *exact words* spoken — copy them from the `words` array. Never paraphrase.

Propose: anchor phrase, overlay description, which card it belongs to.

**6. Present draft to owner.**

Show the proposed sidecar card-by-card in chat:

```
Card 1 — Bird Walk intro
  show_anchor: "it was a finch"  (t=12.3s)
  content: House Finch photo, lower-right
  hide_anchor: card-end

Card 5 — Four species
  show_anchor: "the mockingbird"  (t=87.1s)
  content: "Mockingbird" label badge
  ...
```

Ask for approval or edits before writing anything.

**7. Write the sidecar.**

On approval, write `owners-inbox/presentations/<deck-slug>.hyperframes.md` using the standard shape. Then index it:

```bash
discord-bridge/venv/bin/python3 tools/pka_index.py index-markdown \
  --file owners-inbox/presentations/<deck-slug>.hyperframes.md \
  --category cuecam-hyperframes \
  --tags "hyperframes, cuecam, post-production" \
  --summary "<Deck Title> — N hyperframes cards, M overlays (post-production)"
```

**8. Update intake state.**

```bash
discord-bridge/venv/bin/python3 tools/cuecam_recording_intake.py pair \
  "<recording-path>" \
  "owners-inbox/presentations/<deck-slug>.hyperframes.md"
```

Confirm the state entry flips to `status: hyperframes-pending`.

**9. Instruct owner to run the Codex render.**

Point to the three artifacts from the state entry:

- `cleaned_video`
- `word_transcript`
- `sidecar`

See **HyperFrames Render Workflow** above for the desk-side Codex steps.

**10. After render: finish the video.**

Two paths — ask the owner which applies:

- **Go to CapCut / external editor:** hand off the rendered MP4 directly. The render is the deliverable; no further CueCam step needed.
- **Record a polish pass in CueCam:** build a single-movie-card bundle so the owner can record a new take with overlays visible:

  ```bash
  discord-bridge/venv/bin/python3 tools/cuecam.py polish \
    --source-deck "owners-inbox/presentations/<deck-slug>.cuecam" \
    --rendered-video "/path/to/rendered-overlay-video.mp4" \
    --title "<Deck Title> (polished)" \
    --note "Polish-pass take — overlays baked in"
  ```

  This produces `owners-inbox/presentations/<deck-slug>-polished.cuecam`. Report the path to the owner.

When in doubt, default to handing off the rendered MP4 — the polish-pass bundle is only needed if the owner wants to re-record on camera with the overlays running.
