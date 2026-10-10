#!/bin/bash
# Acceptance run for the develop entry: topic -> interview -> three-stage
# challenge -> owner ruling -> outline -> balance check -> brief -> CueCam
# bundle -> tracked project completed. No model service, no model keys.
#
#   tests/acceptance/develop.sh [repo-root]
#
# Run it against a fresh clone after ./bootstrap.sh. The "assistant" here is
# this script: it reads each request file and writes the response file an
# assistant would, so the whole path is exercised deterministically. Every
# check that a real assistant's response would face (stale inputs, quote
# verification, expected ids, dependency order, idempotent import) is the
# same code path.

set -euo pipefail
ROOT="$(cd "${1:-$(dirname "$0")/../..}" && pwd)"
cd "$ROOT"
PKA="$ROOT/bin/pka"
SLUG="acceptance-develop"
DEV="owners-inbox/development/$SLUG"
py() { "$PKA" python "$@"; }

# No model keys, no provider selection, no local model server.
for var in XAI_API_KEY OPENAI_API_KEY GLM_API_KEY ANTHROPIC_API_KEY LLM_PROVIDER LLM_FALLBACK; do unset "$var" || true; done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
json() { py -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" "$@"; }

step "0. Preconditions"
"$PKA" check >/dev/null && ok "python and toolchain check passes"
for f in tools/challenge.py tools/balance_check.py tools/cuecam.py; do
  grep -qE 'import llm|lmstudio|chat_json|chat_text' "$f" && die "$f still references a model provider"
done
ok "develop-path tools reference no model provider"
rm -rf "$DEV" "owners-inbox/video-projects/$SLUG"
mkdir -p "$DEV"

step "1. Interview (the assistant conducts it; the record is saved)"
cat > "$DEV/interview.md" <<'EOF'
---
category: development-interview
date: 2026-09-18
slug: acceptance-develop
tags: [development, interview]
---

Q: What is the one thing you want people to take away?

A: Anyone can use the system today; that is the whole point. The workflow is not a coder's workflow, it is a writer's workflow with a few commands.

Q: Where did that get proven?

A: The laptop contribution proved the workflow travels. I sat in a coffee shop with the laptop, ran the interview, built the deck, and published from there. Nothing needed the studio machine.

Q: What do critics get wrong?

A: Critics say only coders benefit, and I disagree. I never said only coders benefit. If only coders could do this, participation would shrink, and it has not.
EOF
ok "interview.md saved"

step "2. Challenge gate — stage 1: extract claims"
"$PKA" challenge --slug "$SLUG" extract prepare --source "$DEV/interview.md" >/dev/null
REQ="$DEV/challenge.extract-claims.request.json"
[ -f "$REQ" ] || die "extract request not written"
py - "$REQ" <<'EOF'
import json, sys
req = json.load(open(sys.argv[1]))
resp = {"schema_version": 1, "request_id": req["request_id"], "stage": req["stage"],
        "inputs": {k: v["sha256"] for k, v in req["inputs"].items()},
        "items": [
            {"id": "a", "window": 1, "claim": "Anyone can use the system today",
             "support_quote": "Anyone can use the system today; that is the whole point."},
            {"id": "b", "window": 1, "claim": "The laptop contribution proved the workflow travels",
             "support_quote": "The laptop contribution proved the workflow travels."},
            {"id": "c", "window": 1, "claim": "Only coders benefit",
             "support_quote": "only coders benefit"},
            {"id": "d", "window": 1, "claim": "The studio machine is obsolete",
             "support_quote": "the studio machine is obsolete"},
            {"id": "e", "window": 1, "claim": "Participation has grown", "support_quote": ""},
        ]}
json.dump(resp, open(sys.argv[1].replace(".request.json", ".response.json"), "w"), indent=2)
EOF
OUT=$("$PKA" challenge --slug "$SLUG" extract import)
[ "$(echo "$OUT" | py -c 'import json,sys; print(json.load(sys.stdin)["claims"])')" = "5" ] || die "expected 5 claims: $OUT"
[ "$(json "$DEV/claims.json" "[c['support'] for c in d['claims'] if c['claim']=='The studio machine is obsolete'][0]")" = "quote_not_found" ] || die "fabricated quote was not flagged"
ok "5 claims registered; the fabricated quote is flagged quote_not_found; verdicts request prepared"

step "3. Challenge gate — stage 2: verdicts"
REQ="$DEV/challenge.verdicts.request.json"
py - "$REQ" <<'EOF'
import json, sys
req = json.load(open(sys.argv[1]))
verdict = {"c1": "survives", "c2": "survives", "c3": "proposed_drop", "c4": "proposed_drop", "c5": "weakened"}
items = []
for cid in req["expects"]["ids"]:
    items.append({"id": cid, "verdict": verdict[cid], "rationale": f"{cid}: judged against the evidence shown",
                  "needs": "a number or a story" if verdict[cid] == "weakened" else "", "contradiction": None})
resp = {"schema_version": 1, "request_id": req["request_id"], "stage": req["stage"],
        "inputs": {k: v["sha256"] for k, v in req["inputs"].items()}, "items": items}
json.dump(resp, open(sys.argv[1].replace(".request.json", ".response.json"), "w"), indent=2)
EOF
OUT=$("$PKA" challenge --slug "$SLUG" verdicts import)
echo "$OUT" | grep -q '"reviewable": 3' || die "expected 3 reviewable claims: $OUT"
ok "verdicts applied; 3 claims go to context review, 2 held without verified context"

step "4. Challenge gate — stage 3: context review"
REQ="$DEV/challenge.context-review.request.json"
py - "$REQ" <<'EOF'
import json, sys
req = json.load(open(sys.argv[1]))
items = []
for entry in req["payload"]["claims"]:
    cid = entry["id"]
    valid = cid != "c3"   # "only coders benefit" is a reported belief the owner disowns
    items.append({"id": cid, "claim_valid": valid, "verdict": "survives" if valid else "proposed_drop",
                  "rationale": "endorsed in context" if valid else "reported belief, explicitly disagreed with",
                  "needs": "", "contradiction_valid": False})
resp = {"schema_version": 1, "request_id": req["request_id"], "stage": req["stage"],
        "inputs": {k: v["sha256"] for k, v in req["inputs"].items()}, "items": items}
json.dump(resp, open(sys.argv[1].replace(".request.json", ".response.json"), "w"), indent=2)
EOF
OUT=$("$PKA" challenge --slug "$SLUG" context-review import)
echo "$OUT" | grep -q '"cleared": 2' || die "expected 2 cleared: $OUT"
[ -f "$DEV/challenge.md" ] || die "challenge.md not written"
AGAIN=$("$PKA" challenge --slug "$SLUG" context-review import)
echo "$AGAIN" | grep -q already_imported || die "reimport was not a no-op"
ok "2 cleared, report written, reimport is a no-op"

step "5. Owner ruling (the only approval-like act in the gate) and refresh"
py - "$DEV/claims.json" <<'EOF'
import json, sys
reg = json.load(open(sys.argv[1]))
for c in reg["claims"]:
    if c["id"] == "c5":
        c["owner_override"] = "survives"; c["owner_note"] = "I have the numbers; adding them to the outline."
json.dump(reg, open(sys.argv[1], "w"), indent=2)
EOF
"$PKA" challenge --slug "$SLUG" refresh | grep -q '"cleared": 3' || die "override did not clear c5"
ok "owner override recorded; 3 cleared for outline; run history appended"

step "6. Outline from cleared claims, then the balance check"
cat > "$DEV/outline.md" <<'EOF'
# Anyone can use the system

## Promise
Anyone can use the system today; that is the whole point.

## Beats
- The laptop contribution proved the workflow travels: coffee shop, interview, deck, publish.
- Participation has grown, with the numbers.
- What critics get wrong, and why it does not matter.
EOF
OUT=$("$PKA" balance_check --slug "$SLUG" prepare --outline "$DEV/outline.md")
echo "$OUT" | grep -q '"status": "prepared"' || die "balance prepare failed: $OUT"
REQ="$DEV/balance-check.analysis.request.json"
py - "$REQ" <<'EOF'
import json, sys
req = json.load(open(sys.argv[1]))
resp = {"schema_version": 1, "request_id": req["request_id"], "stage": req["stage"],
        "inputs": {k: v["sha256"] for k, v in req["inputs"].items()}, "items": []}
json.dump(resp, open(sys.argv[1].replace(".request.json", ".response.json"), "w"), indent=2)
EOF
"$PKA" balance_check --slug "$SLUG" import | grep -q '"status": "applied"' || die "balance import failed"
grep -q "None found against the top-overlap sources" "$DEV/balance-check.md" || die "balance report not rewritten"
ok "balance check prepared, answered, imported"

step "7. Brief and CueCam bundle"
cat > "$DEV/brief.md" <<'EOF'
# Brief — Anyone can use the system

**Promise:** Anyone can use the system today; that is the whole point.

Outline: outline.md

Packaging intent: "Anyone Can Use It" / "The Laptop Test" / thumbnail: laptop on a café table.

Balance decisions: no retread or contradiction findings.

| Stage | Timestamp | Notes |
|---|---|---|
| interview done | 2026-09-18 | 3 questions |
| challenge gate run | 2026-09-18 | 3 cleared, 1 dropped, 1 held |
EOF
cat > "$DEV/deck.spec" <<'EOF'
Title: Anyone can use the system

1. "Anyone can use the system", that is the whole point
2. "The laptop test", coffee shop, interview, deck, publish
3. "The numbers", participation has grown
4. "What critics get wrong", and why it does not matter
EOF
OUT=$("$PKA" cuecam compose --spec-file "$DEV/deck.spec" --title "Anyone can use the system")
BUNDLE=$(echo "$OUT" | py -c 'import json,sys; print(json.load(sys.stdin)["path"])')
[ -d "$BUNDLE" ] || die "bundle not built: $OUT"
ok "bundle built at $BUNDLE"

step "8. Tracked project at the develop entry"
"$PKA" video_project create --title "Anyone can use the system" --slug "$SLUG" --entry develop >/dev/null
"$PKA" video_project sync-development --slug "$SLUG" | grep -q claims_register || die "sync-development did not attach the register"
"$PKA" video_project advance --slug "$SLUG" >/dev/null   # brief-review
"$PKA" video_project approve --slug "$SLUG" --gate brief --by "owner" >/dev/null
"$PKA" video_project advance --slug "$SLUG" >/dev/null   # brief-approved
"$PKA" video_project advance --slug "$SLUG" >/dev/null   # deck-review
"$PKA" video_project attach --slug "$SLUG" --kind cuecam_bundle --path "$BUNDLE" >/dev/null
"$PKA" video_project approve --slug "$SLUG" --gate deck --by "owner" >/dev/null
"$PKA" video_project advance --slug "$SLUG" >/dev/null   # deck-approved
"$PKA" video_project advance --slug "$SLUG" >/dev/null   # complete
MANIFEST="owners-inbox/video-projects/$SLUG/project.json"
[ "$(json "$MANIFEST" "d['state']")" = "complete" ] || die "project did not complete"
[ "$(json "$MANIFEST" "d['deliveries'][0]['entry']")" = "develop" ] || die "delivery not recorded for develop"
[ "$(json "$MANIFEST" "sorted(d['approvals'])")" = "['brief', 'deck']" ] || die "unexpected approvals: only brief and deck were given by the owner"
[ "$(json "$MANIFEST" "d.get('artifacts',{}).get('final_master')")" = "None" ] || die "no video material should exist"
ok "project complete: brief and deck approved by the owner, bundle delivered, no video material required"

step "Result"
echo "  interview -> 3-stage challenge -> owner ruling -> outline -> balance check -> brief -> bundle -> complete"
echo "  no model keys, no provider, no model server; $(ls "$DEV"/*.request.json | wc -l | tr -d ' ') requests answered, $(json "$DEV/handoff-imports.json" "len(d['imports'])") imports applied"
echo "  files: $DEV, $MANIFEST, $BUNDLE"
