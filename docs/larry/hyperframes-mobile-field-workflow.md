---
title: HyperFrames Mobile Field Workflow
status: active
created: 2026-05-15
source_run: iphone-4011-4014
---

# HyperFrames Mobile Field Workflow

This is the current reproducible flow for producing a HyperFrames review video while John is in the field with only an iPhone, with rendering and upload handled by the Mac mini at home.

## Proven Run

First proven run: `iphone-4011-4014`, completed 2026-05-15.

Outcome:
- Four iPhone videos were downloaded from a Dropbox public folder.
- The clips were normalized, concatenated, cleaned, and transcribed locally.
- John selected five spoken trigger phrases from the transcript using only chat.
- Larry mapped each trigger to cleaned word timestamps, staged the image assets, generated HyperFrames HTML, rendered locally, verified sampled overlay frames, and uploaded a Google Drive review MP4.
- Drive review link: `https://drive.google.com/file/d/1VMFgqz0yLqypSGa2mMGQgeViRcmqRkdd/view?usp=drivesdk`

## Owner Experience

John only needs to do this:

1. Capture video and images on iPhone.
2. Put the raw media in a Dropbox folder or send image attachments during chat.
3. Send the Dropbox folder link.
4. Review the cleaned transcript.
5. Reply with trigger requests in this shape:

```text
Trigger N: "<spoken anchor>", <overlay placement>, <asset>, <duration>
```

Examples:

```text
Trigger 1: "producing events", use attached image with no text or background, placed to the right side of the frame, for 5 seconds
Trigger 3: "effectively developing", full frame of attached image, for 12 secs
Trigger 5: "come across", full frame the attached image to the end of the video
```

John does not need terminal access.

## Operator Runbook

Use slug form `iphone-NNNN-NNNN` unless the owner gives a title.

### 1. Download And Stage

For a Dropbox folder, download with `dl=1`:

```bash
mkdir -p team-inbox/video-intake/<slug>
curl -L --fail --retry 2 \
  --output team-inbox/video-intake/<slug>/dropbox.zip \
  '<dropbox-folder-url-with-dl=1>'
unzip -l team-inbox/video-intake/<slug>/dropbox.zip
unzip -o team-inbox/video-intake/<slug>/dropbox.zip IMG_4011.MOV IMG_4012.MOV IMG_4013.MOV IMG_4014.MOV \
  -d team-inbox/video-intake/<slug>
```

Verify video properties:

```bash
ffprobe -v error -show_entries format=duration,size \
  -show_entries stream=index,codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels \
  -of json team-inbox/video-intake/<slug>/IMG_4011.MOV
```

### 2. Normalize And Concatenate

Create the project:

```bash
mkdir -p "video-studio/projects/<slug>/motion"
mkdir -p "video-studio/projects/<slug>/hyperframes/assets"
```

Concatenate with normalized video and audio. For the proven run, the first three selfie clips had a 180-degree display matrix and the fourth was rear-camera. FFmpeg handled the display matrix during decode.

```bash
ffmpeg -y \
  -i team-inbox/video-intake/<slug>/IMG_4011.MOV \
  -i team-inbox/video-intake/<slug>/IMG_4012.MOV \
  -i team-inbox/video-intake/<slug>/IMG_4013.MOV \
  -i team-inbox/video-intake/<slug>/IMG_4014.MOV \
  -filter_complex '[0:v]fps=30,scale=1920:1080,setsar=1,format=yuv420p[v0];[1:v]fps=30,scale=1920:1080,setsar=1,format=yuv420p[v1];[2:v]fps=30,scale=1920:1080,setsar=1,format=yuv420p[v2];[3:v]fps=30,scale=1920:1080,setsar=1,format=yuv420p[v3];[0:a]aresample=48000[a0];[1:a]aresample=48000[a1];[2:a]aresample=48000[a2];[3:a]aresample=48000[a3];[v0][a0][v1][a1][v2][a2][v3][a3]concat=n=4:v=1:a=1[outv][outa]' \
  -map '[outv]' -map '[outa]' \
  -c:v libx264 -preset fast -crf 18 \
  -c:a aac -b:a 192k \
  "video-studio/projects/<slug>/motion/<slug>_combined.mp4"
```

### 3. Clean And Transcribe

Run silence and simple repeat cleanup with word export:

```bash
python3 tools/clean_video.py \
  "video-studio/projects/<slug>/motion/<slug>_combined.mp4" \
  --output "video-studio/projects/<slug>/motion/<slug>_cleaned.mp4" \
  --model base \
  --export-words "video-studio/projects/<slug>/motion/words_raw.json"
```

Then transcribe the cleaned video so the owner sees the post-cut timeline:

```bash
python3 tools/transcribe.py \
  "video-studio/projects/<slug>/motion/<slug>_cleaned.mp4" \
  "<Readable Title> Cleaned" \
  --model base
```

Generate cleaned word timings for trigger resolution:

```bash
python3 tools/clean_video.py \
  "video-studio/projects/<slug>/motion/<slug>_cleaned.mp4" \
  --no-silence --dry-run --model base \
  --export-words "video-studio/projects/<slug>/motion/words_cleaned.json"
```

Copy the final transcript into the project:

```bash
cp owners-inbox/transcripts/<transcript-file>.md \
  "video-studio/projects/<slug>/motion/transcript_packed_edited.md"
```

### 4. Register HyperFrames State

```bash
python3 tools/hyperframes_state.py create \
  --slug <slug> \
  --recording "video-studio/projects/<slug>/motion/<slug>_cleaned.mp4" \
  --transcript "video-studio/projects/<slug>/motion/transcript_packed_edited.md" \
  --codex-project "video-studio/projects/<slug>" \
  --force
```

Also stage the cleaned video for HyperFrames:

```bash
cp "video-studio/projects/<slug>/motion/<slug>_cleaned.mp4" \
  "video-studio/projects/<slug>/hyperframes/assets/edited.mp4"
```

### 5. Add Triggers

Resolve each anchor phrase against `words_cleaned.json`. The anchor must be a verbatim spoken phrase.

Append each trigger:

```bash
python3 tools/hyperframes_state.py append-trigger --slug <slug>
```

Then paste JSON on stdin, ending with EOF:

```json
{"show_anchor":"producing events","show_time":12.04,"primitive":"image-right","asset":"producing-events.jpg","caption":"","hide_anchor":"duration","duration":5.0}
```

Supported primitives used in the proven run:
- `image-right`: image-only, right side, no panel or text.
- `image-fullscreen`: image-only, full frame.

For "to end of video", omit `duration` and set `hide_anchor` to `card-end`.

### 6. Generate And Lint

```bash
python3 tools/hyperframes_state.py draft-to-overlay-timing --slug <slug>
python3 tools/hyperframes_html_gen.py --slug <slug> --force
npx --yes hyperframes lint --json
```

Expected lint result: `0` errors and `0` warnings.

### 7. Render

```bash
npx --yes hyperframes render
```

If HyperFrames warns about sparse keyframes in `assets/edited.mp4`, render may still complete. If sampled frames show freezes, re-encode `assets/edited.mp4` before rendering:

```bash
ffmpeg -y -i assets/edited.mp4 \
  -c:v libx264 -r 30 -g 30 -keyint_min 30 -movflags +faststart \
  -c:a copy assets/edited_kf30.mp4
mv assets/edited_kf30.mp4 assets/edited.mp4
```

### 8. Verify

After render, inspect:

```bash
ffprobe -v error -show_entries format=duration,size \
  -show_entries stream=index,codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels \
  -of json "video-studio/projects/<slug>/hyperframes/renders/<render>.mp4"
```

Extract still frames at overlay times and visually confirm overlays appear.

### 9. Upload To Google Drive

```bash
discord-bridge/venv/bin/python3 tools/gdrive.py upload \
  --file "video-studio/projects/<slug>/hyperframes/renders/<render>.mp4" \
  --folder "PKA Reviews/<slug>"
```

If token is expired, owner runs:

```bash
discord-bridge/venv/bin/python3 tools/gdocs.py auth
```

Then retry upload.

Record the link:

```bash
python3 tools/hyperframes_state.py advance \
  --slug <slug> \
  --to uploaded \
  --rendered-video "video-studio/projects/<slug>/hyperframes/renders/<render>.mp4" \
  --drive-link "<drive-webViewLink>"
```

## Reproducibility Assessment

The flow is reproducible now, but still semi-manual.

Stable pieces:
- Dropbox public-share download works with `curl` and `dl=1`.
- `ffmpeg` normalization and concatenation are deterministic.
- `tools/clean_video.py` handles silence removal and word timestamp export.
- `tools/transcribe.py` saves and indexes a cleaned transcript.
- `tools/hyperframes_state.py` preserves trigger state across sessions.
- `tools/hyperframes_html_gen.py` generates renderable HTML from `overlay_timing.json`.
- `npx --yes hyperframes lint` and `render` produce local review MP4s.
- `tools/gdrive.py upload` uploads review MP4s to `PKA Reviews/<slug>`.

Manual or fragile pieces:
- Selecting target clips from a Dropbox folder archive is manual.
- Concatenation assumes clip order and count.
- Trigger JSON is hand-entered via stdin.
- Anchor lookup is currently ad hoc against `words_cleaned.json`.
- New overlay primitives sometimes require generator edits.
- Render verification is manual still-frame inspection.
- Drive upload depends on an unexpired Google OAuth token.

Recommended automation path:
1. Add `tools/hyperframes_mobile_intake.py`:
   - download Dropbox folder
   - filter target media by pattern or owner-selected names
   - produce a project manifest
   - normalize/concat
   - clean/transcribe/export words
   - create HyperFrames state
2. Add `tools/hyperframes_add_trigger.py`:
   - accept `--slug`, `--anchor`, `--asset`, `--primitive`, `--duration`, `--to-end`
   - resolve the anchor in `words_cleaned.json`
   - copy the asset to `hyperframes/assets`
   - append trigger JSON
   - regenerate timing and HTML
   - lint
3. Extend `tools/codex_hyperframes_render.py` to handle this phone-project layout:
   - normalize keyframes before render
   - run lint
   - render
   - sample overlay frames at trigger times
   - upload to Drive
   - advance the task record
4. Add a project manifest:
   - source Dropbox URL
   - source clips and order
   - cleaned video path
   - transcript path
   - assets and trigger list
   - render path and Drive link

Target owner experience after automation:

```text
Process this Dropbox folder as iphone-4011-4014, using clips 4011-4014.
```

Then:

```text
Trigger 1: "producing events", right side image, 5 seconds.
```

Larry should do the rest without hand-building commands.

