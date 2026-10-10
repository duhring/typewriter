#!/bin/bash
# Acceptance run for the article entry: a source (raw intake interview, or a
# transcript with its structured extract) -> reflective interview -> thesis ->
# Substack brief -> developmental draft -> owner edit -> reconciled final ->
# final-article approval bound to the file -> tracked project completed.
# No model service, no model keys.
#
#   tests/acceptance/article.sh intake     [repo-root]   # standalone article from an intake interview
#   tests/acceptance/article.sh transcript [repo-root]   # article following a video transcript
#
# The "assistant" is this script: it writes the editorial artifacts and, in the
# transcript variant, the extract response file, so the path is deterministic.

set -euo pipefail
MODE="${1:-intake}"
case "$MODE" in intake|transcript) ;; *) echo "mode must be intake or transcript" >&2; exit 2 ;; esac
ROOT="$(cd "${2:-$(dirname "$0")/../..}" && pwd)"
cd "$ROOT"
PKA="$ROOT/bin/pka"
py() { "$PKA" python "$@"; }
SLUG="acceptance-article-$MODE"
DEV="owners-inbox/development/$SLUG"
MANIFEST="owners-inbox/video-projects/$SLUG/project.json"

for var in XAI_API_KEY OPENAI_API_KEY GLM_API_KEY ANTHROPIC_API_KEY LLM_PROVIDER LLM_FALLBACK; do unset "$var" || true; done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
json() { py -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" "$@"; }
state() { json "$MANIFEST" "d['state']"; }

step "0. Preconditions ($MODE)"
"$PKA" check >/dev/null && ok "python and toolchain check passes"
for f in tools/extract_structured.py tools/fetch-transcript.py tools/video_project.py; do
  grep -qE 'import llm|lmstudio|chat_json|chat_text' "$f" && die "$f still references a model provider"
done
ok "article-path tools reference no model provider"
rm -rf "$DEV" "owners-inbox/video-projects/$SLUG" "owners-inbox/transcripts/2026-09-18-$SLUG.md" \
       "owners-inbox/transcript-extracts/requests/2026-09-18-$SLUG" owners-inbox/transcript-extracts/2026-09-18-the-laptop-test*.md
mkdir -p "$DEV"

step "1. Source"
if [ "$MODE" = intake ]; then
  cat > "$DEV/interview.md" <<'EOF'
---
category: development-interview
date: 2026-09-18
slug: acceptance-article-intake
tags: [development, interview]
---

Q: What do you want a reader to do differently after this piece?

A: Try the workflow on a laptop away from the studio. The laptop contribution proved the workflow travels.

Q: What is the objection you hear most?

A: That only coders benefit. I disagree, and the numbers say participation grew.
EOF
  SOURCE_KIND=interview; SOURCE_PATH="$DEV/interview.md"
  ok "intake interview saved (standalone article; no video)"
else
  mkdir -p owners-inbox/transcripts
  TRANSCRIPT="owners-inbox/transcripts/2026-09-18-$SLUG.md"
  cat > "$TRANSCRIPT" <<'EOF'
# The Laptop Test

**Video:** https://youtu.be/acceptance

00:00 Why this matters: the workflow is a writer's workflow.
01:30 The laptop contribution proved the workflow travels.
04:10 Critics say only coders benefit, and I disagree. Participation grew.
EOF
  ok "transcript saved (as fetch-transcript would leave it)"
  OUT=$("$PKA" extract_structured transcript prepare --file "$TRANSCRIPT")
  REQDIR=$(echo "$OUT" | py -c 'import json,sys; print(json.load(sys.stdin)["dir"])')
  py - "$REQDIR/transcript-extract.request.json" <<'EOF'
import json, sys
req = json.load(open(sys.argv[1]))
resp = {"schema_version": 1, "request_id": req["request_id"], "stage": req["stage"],
        "inputs": {k: v["sha256"] for k, v in req["inputs"].items()},
        "items": [{"id": "extract", "summary": "The workflow travels; participation grew.",
                   "timestamp_highlights": [{"timestamp": "01:30", "label": "The laptop test"}],
                   "key_points": ["A writer's workflow, not a coder's"], "reusable_claims": ["The workflow travels"],
                   "questions_raised": [], "action_ideas": ["Write the laptop post"], "tools_mentioned": ["CueCam"],
                   "recommended_tags": ["workflow"]}]}
json.dump(resp, open(sys.argv[1].replace(".request.json", ".response.json"), "w"), indent=2)
EOF
  "$PKA" extract_structured transcript import --dir "$REQDIR" | grep -q '"status": "created"' || die "extract import failed"
  ok "structured extract prepared, answered, imported (timestamp verified against the transcript)"
  SOURCE_KIND=transcript; SOURCE_PATH="$TRANSCRIPT"
fi

step "2. Project at the article entry"
"$PKA" video_project create --title "The Laptop Test ($MODE)" --slug "$SLUG" --entry article >/dev/null
[ "$(state)" = "blog-review" ] || die "article project should start at blog-review"
[ "$(json "$MANIFEST" "d['requested_outputs']")" = "['article']" ] || die "article-only output expected"
"$PKA" video_project attach --slug "$SLUG" --kind "$SOURCE_KIND" --path "$SOURCE_PATH" >/dev/null
ok "project created at blog-review with article-only output; $SOURCE_KIND attached as the source"

step "3. Editorial loop: reflective interview, thesis, brief, draft"
cat > "$DEV/editorial-interview.md" <<'EOF'
# Reflective interview

Q: What did the recording turn out to be about?
A: That the workflow travels, and that the objection about coders is wrong.
EOF
cat > "$DEV/blog-thesis.md" <<'EOF'
# Thesis

The workflow travels. Anyone can run it from a laptop, and participation has grown. (Confirmed by the owner.)
EOF
"$PKA" video_project attach --slug "$SLUG" --kind editorial_interview --path "$DEV/editorial-interview.md" >/dev/null
"$PKA" video_project attach --slug "$SLUG" --kind blog_thesis --path "$DEV/blog-thesis.md" >/dev/null
"$PKA" video_project substack-brief --slug "$SLUG" --pov "Try it on a laptop" --reader "Writers who think this needs a coder" \
  --mode "$([ "$MODE" = intake ] && echo standalone || echo companion)" --adds "The numbers on participation" --cta "Run one interview" >/dev/null
cat > "$DEV/blog-draft.md" <<'EOF'
# The Laptop Test

Anyone can run this workflow from a laptop. Here is what happened when I tried.

The objection is always that only coders benefit. The numbers say otherwise.
EOF
"$PKA" video_project attach --slug "$SLUG" --kind blog_draft --path "$DEV/blog-draft.md" >/dev/null
"$PKA" video_project approve --slug "$SLUG" --gate blog --by owner >/dev/null
"$PKA" video_project advance --slug "$SLUG" >/dev/null
[ "$(state)" = "blog-approved" ] || die "expected blog-approved, got $(state)"
ok "interview, thesis, Substack brief, developmental draft attached; draft approved by the owner"

step "4. Owner edit, reconciled final, final-article approval"
cat > "$DEV/blog-owner-edit.md" <<'EOF'
# The Laptop Test

Anyone can run this workflow from a laptop. I did, in a coffee shop, and here is what happened.

The objection is always that only coders benefit. Participation grew. The numbers say otherwise.
EOF
cat > "$DEV/blog-final.md" <<'EOF'
# The Laptop Test

Anyone can run this workflow from a laptop. I did, in a coffee shop, and here is what happened.

The objection is always that only coders benefit. Participation grew; the numbers say otherwise.
EOF
"$PKA" video_project attach --slug "$SLUG" --kind blog_owner_edit --path "$DEV/blog-owner-edit.md" >/dev/null
"$PKA" video_project attach --slug "$SLUG" --kind blog_final --path "$DEV/blog-final.md" >/dev/null
"$PKA" video_project approve --slug "$SLUG" --gate blog_final --by owner >/dev/null
ok "owner edit and reconciled final attached; final approved as the exact file"

step "5. The approval is bound to the file"
cp "$DEV/blog-final.md" "$DEV/blog-final.md.approved"
printf '\nOne more sentence after approval.\n' >> "$DEV/blog-final.md"
if "$PKA" video_project advance --slug "$SLUG" >/dev/null 2>&1; then die "advance succeeded although the final changed after approval"; fi
[ "$(state)" = "blog-approved" ] || die "state moved despite the stale approval"
mv "$DEV/blog-final.md.approved" "$DEV/blog-final.md"
"$PKA" video_project advance --slug "$SLUG" >/dev/null
[ "$(state)" = "blog-final-approved" ] || die "expected blog-final-approved, got $(state)"
ok "an edited final is refused; the approved bytes restored are accepted"

step "6. Completion"
"$PKA" video_project advance --slug "$SLUG" >/dev/null
[ "$(state)" = "complete" ] || die "project did not complete"
[ "$(json "$MANIFEST" "d['deliveries'][0]['entry']")" = "article" ] || die "delivery not recorded for article"
[ "$(json "$MANIFEST" "list(d['deliveries'][0]['artifacts'])")" = "['blog_final']" ] || die "delivery should be the final article"
[ "$(json "$MANIFEST" "sorted(d['approvals'])")" = "['blog', 'blog_final']" ] || die "only the two blog gates should be approved"
[ "$(json "$MANIFEST" "d['artifacts'].get('final_master')")" = "None" ] || die "no video material should exist"
[ "$(json "$MANIFEST" "'youtube' in d['publications']")" = "False" ] || die "no YouTube record should exist"
ok "complete: final article delivered with its hash; no video, no YouTube, no publication required"

step "Result ($MODE)"
echo "  source ($SOURCE_KIND) -> reflective interview -> thesis -> brief -> draft (approved) -> owner edit -> final (approved, bound to bytes) -> complete"
echo "  no model keys, no provider, no model server"
echo "  files: $DEV, $MANIFEST"
