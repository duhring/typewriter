#!/bin/bash
# Acceptance run for the publish entry: an edited master -> technical QC ->
# master approval -> package (assistant) -> chapter review (assistant) ->
# supplied artwork -> package approval -> release -> public URL recorded ->
# tracked project completed. No model service, no model keys.
#
#   tests/acceptance/publish.sh owner     [repo-root]   # the owner uploads and releases the video
#   tests/acceptance/publish.sh automated [repo-root]   # PKA's private upload, QA, and release gate
#
# The "assistant" is this script: it answers the two request files. The one
# step this run cannot perform is the YouTube upload itself, which needs the
# owner's Google authorization; in automated mode the private upload is
# recorded as a fixture. QA, approvals and release are simulated tracker
# commands using synthetic IDs/URLs; they do not verify YouTube or human actions. The master is a two-second clip generated with ffmpeg, and its
# transcript is supplied, so local Whisper is not needed either.

set -euo pipefail
MODE="${1:-owner}"
case "$MODE" in owner|automated) ;; *) echo "mode must be owner or automated" >&2; exit 2 ;; esac
ROOT="$(cd "${2:-$(dirname "$0")/../..}" && pwd)"
cd "$ROOT"
PKA="$ROOT/bin/pka"
PY="$PKA python"
SLUG="acceptance-publish-$MODE"
PROJ="owners-inbox/video-projects/$SLUG"
MANIFEST="$PROJ/project.json"
WORK="$ROOT/data/tmp/$SLUG"

for var in XAI_API_KEY OPENAI_API_KEY GLM_API_KEY ANTHROPIC_API_KEY LLM_PROVIDER LLM_FALLBACK; do unset "$var" || true; done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
json() { $PY -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" "$@"; }
state() { json "$MANIFEST" "d['state']"; }
vp() { "$PKA" video_project "$@"; }

step "0. Preconditions ($MODE)"
"$PKA" check >/dev/null && ok "python and toolchain check passes"
command -v ffmpeg >/dev/null || die "ffmpeg is needed to generate the test master (publish path dependency)"
for f in tools/pipeline.py tools/video_project.py tools/publish_to_youtube.py; do
  grep -qE 'import llm|lmstudio|chat_json|chat_text|generate_thumbnail' "$f" && die "$f still references a model or image provider"
done
ok "publish-path tools reference no model or image provider"
rm -rf "$PROJ" "$WORK" owners-inbox/youtube/*-"$SLUG"*.md owners-inbox/qc/*"$SLUG"* 2>/dev/null || true
mkdir -p "$WORK"

step "1. An edited master, its transcript, and the artwork"
ffmpeg -hide_banner -loglevel error -f lavfi -i "testsrc=duration=2:size=320x240:rate=10" \
  -f lavfi -i "sine=frequency=440:duration=2" -shortest -y "$WORK/master.mp4"
cat > "$WORK/master-transcript.md" <<'EOF'
# Master transcript

**[0:00]**
Why this matters: the workflow is a writer's workflow.

**[0:01]**
The laptop contribution proved the workflow travels.
EOF
ffmpeg -hide_banner -loglevel error -f lavfi -i "color=c=orange:size=640x360:duration=0.1" -frames:v 1 -y "$WORK/cover.png"
ok "master.mp4 (2 s), a timed transcript, and cover.png supplied"

step "2. Project at the publish entry; technical QC attaches the master"
MODE_FLAG=$([ "$MODE" = owner ] && echo owner || echo automated-private)
vp create --title "The Laptop Test ($MODE)" --slug "$SLUG" --entry publish --publication-mode "$MODE_FLAG" >/dev/null
[ "$(state)" = "master-qc" ] || die "publish project should start at master-qc"
"$PKA" video_qc "$WORK/master.mp4" --project "$SLUG" >/dev/null || die "technical QC did not pass"
[ "$(json "$MANIFEST" "d['qc']['status']")" = "pass" ] || die "QC status not recorded"
vp approve --slug "$SLUG" --gate master --by owner >/dev/null
ok "created at master-qc ($MODE_FLAG); QC passed; master approved by the owner"

step "3. Package handoff (assistant writes the package)"
OUT=$("$PKA" pipeline package prepare --project "$SLUG" --transcript "$WORK/master-transcript.md")
echo "$OUT" | grep -q '"status": "prepared"' || die "package prepare failed: $OUT"
$PY - "$PROJ/pipeline.package.request.json" <<'EOF'
import json, sys
req = json.load(open(sys.argv[1]))
segs = req["payload"]["segments"]
markdown = f"""## 1. Title Options
1. **Curiosity Gap** — *What the laptop test proved*
2. **How-To/Tutorial** — *Run the workflow from anywhere*
3. **Bold Claim** — *You do not need the studio*
4. **Listicle** — *Two things a laptop proved*
5. **Question** — *Does the workflow travel?*
6. **Emotional/Story** — *The coffee shop recording*

## 2. YouTube Description
A writer's workflow, run from a laptop.
Here is what the laptop test proved.

**Chapters**
- 0:00 — Why this matters
- {segs[1]['timestamp']} — The laptop test

**Links**
- https://example.com/pka

**Tags**
#workflow #laptop #writing

## 3. Thumbnail Prompt
A laptop on a café table, warm light, text overlay: THE LAPTOP TEST.
"""
resp = {"schema_version": 1, "request_id": req["request_id"], "stage": req["stage"],
        "inputs": {k: v["sha256"] for k, v in req["inputs"].items()},
        "items": [{"id": "package", "markdown": markdown}]}
json.dump(resp, open(sys.argv[1].replace(".request.json", ".response.json"), "w"), indent=2)
EOF
OUT=$("$PKA" pipeline package import --project "$SLUG")
echo "$OUT" | grep -q '"chapters": 2' || die "package import failed: $OUT"
[ "$(state)" = "master-qc" ] || die "state moved before the chapter review"
ok "package imported: chapters on the timeline, saved, indexed, attached; chapter-review request prepared"

step "4. Chapter review handoff (assistant reviews each chapter)"
$PY - "$PROJ/pipeline.chapter-review.request.json" <<'EOF'
import json, sys
req = json.load(open(sys.argv[1]))
items = [{"id": c["id"], "supported": True, "reason": "The label matches the content that begins at its boundary."}
         for c in req["payload"]["chapters"]]
resp = {"schema_version": 1, "request_id": req["request_id"], "stage": req["stage"],
        "inputs": {k: v["sha256"] for k, v in req["inputs"].items()}, "items": items}
json.dump(resp, open(sys.argv[1].replace(".request.json", ".response.json"), "w"), indent=2)
EOF
"$PKA" pipeline chapter-review import --project "$SLUG" | grep -q '"status": "package-review"' || die "chapter review import failed"
[ "$(state)" = "package-review" ] || die "expected package-review, got $(state)"
ok "every chapter supported; project in package-review"

step "5. Supplied artwork, selections, package approval"
vp attach --slug "$SLUG" --kind thumbnail --path "$WORK/cover.png" --note "Owner-supplied artwork." --source owner --rights owner-created >/dev/null
vp select-thumbnail --slug "$SLUG" --path "$WORK/cover.png" >/dev/null
vp select-title --slug "$SLUG" --option 1 --title "What the laptop test proved" >/dev/null
vp approve --slug "$SLUG" --gate package --by owner >/dev/null
vp advance --slug "$SLUG" >/dev/null
[ "$(state)" = "package-approved" ] || die "expected package-approved, got $(state)"
ok "artwork attached and selected; title selected; package approved by the owner"

if [ "$MODE" = owner ]; then
  step "6. Owner uploads and releases; the public URL is recorded"
  if vp advance --slug "$SLUG" >/dev/null 2>&1; then die "advanced to published without a public URL"; fi
  vp publication --slug "$SLUG" --channel youtube --url "https://youtu.be/acceptance-owner" >/dev/null
  vp advance --slug "$SLUG" >/dev/null
  [ "$(state)" = "published" ] || die "expected published, got $(state)"
  ok "URL recorded by the owner; published"
else
  step "6. Private upload (recorded as the upload tool would), QA, release approval"
  vp youtube-private --slug "$SLUG" --video-id acc123 --url "https://youtu.be/acc123" --title "What the laptop test proved" >/dev/null
  [ "$(state)" = "private-upload" ] || die "expected private-upload, got $(state)"
  vp youtube-qa --slug "$SLUG" --pass-item playback --pass-item hd-processing --pass-item title \
    --pass-item description-links --pass-item chapters --pass-item thumbnail-mobile --pass-item captions --pass-item audience >/dev/null
  [ "$(state)" = "youtube-qa" ] || vp advance --slug "$SLUG" --to youtube-qa >/dev/null
  vp approve --slug "$SLUG" --gate release --by owner >/dev/null
  vp advance --slug "$SLUG" >/dev/null
  [ "$(state)" = "release-approved" ] || die "expected release-approved, got $(state)"
  ok "private upload recorded; QA checklist complete; release approved by the owner"

  step "7. The release approval is bound to the master"
  cp "$WORK/master.mp4" "$WORK/master.approved.mp4"
  printf 'x' >> "$WORK/master.mp4"
  if vp publication --slug "$SLUG" --channel youtube --url "https://youtu.be/acc123" >/dev/null 2>&1; then
    die "publication recorded although the master changed after release approval"
  fi
  mv "$WORK/master.approved.mp4" "$WORK/master.mp4"
  vp publication --slug "$SLUG" --channel youtube --url "https://youtu.be/acc123" >/dev/null
  vp advance --slug "$SLUG" >/dev/null
  [ "$(state)" = "published" ] || die "expected published, got $(state)"
  ok "a changed master is refused; the approved bytes restored are accepted; owner's release recorded"
fi

step "Completion"
vp advance --slug "$SLUG" >/dev/null
[ "$(state)" = "complete" ] || die "project did not complete"
[ "$(json "$MANIFEST" "d['deliveries'][0]['entry']")" = "publish" ] || die "delivery not recorded for publish"
[ "$(json "$MANIFEST" "sorted(d['deliveries'][0]['artifacts'])")" = "['final_master', 'thumbnail', 'transcript', 'youtube_package']" ] || die "unexpected delivery"
EXPECTED=$([ "$MODE" = owner ] && echo "['master', 'package']" || echo "['master', 'package', 'release']")
[ "$(json "$MANIFEST" "sorted(d['approvals'])")" = "$EXPECTED" ] || die "unexpected approvals: $(json "$MANIFEST" "sorted(d['approvals'])")"
[ "$(json "$MANIFEST" "d['publications']['youtube']['url']")" != "" ] || die "no public URL"
[ "$(json "$MANIFEST" "d['youtube'].get('privacy')")" = "public" ] || die "release not recorded as public"
ok "complete: materials delivered, public URL recorded, only fixture owner approval labels present"

step "Result ($MODE)"
echo "  master -> QC -> master approval -> package (2 handoffs) -> artwork -> package approval -> release -> URL -> complete"
echo "  no model keys, no provider, no model server, no image API; the YouTube upload itself needs the owner's Google auth"
echo "  files: $PROJ, $WORK"
