#!/bin/bash
# Clean-install verification: every entry point, end to end, on a machine
# with no model keys and no model server.
#
#   tests/acceptance/all.sh [repo-root]
#
# Run it on a fresh clone after ./bootstrap.sh (or point it at one). It
# clears every model-related variable, checks nothing in the repo reaches
# for a provider, runs the unit suite, then the five acceptance runs:
#
#   develop                    topic -> CueCam bundle
#   article intake             intake interview -> final article
#   article transcript         transcript + extract -> final article
#   publish owner              edited master -> owner's release + URL
#   publish automated          edited master -> private upload, QA, release gate, URL
#
# Exit 0 means the starter's contract holds on this machine.

set -uo pipefail
ROOT="$(cd "${1:-$(dirname "$0")/../..}" && pwd)"
cd "$ROOT"
PKA="$ROOT/bin/pka"
HERE="$ROOT/tests/acceptance"

for var in XAI_API_KEY OPENAI_API_KEY GLM_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY LLM_PROVIDER LLM_FALLBACK \
           LM_STUDIO_BASE_URL OLLAMA_HOST; do unset "$var" || true; done

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

results=()
record() { results+=("$1|$2"); }

bold "Clean-install verification at $ROOT"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ) · $(uname -m) · $("$PKA" python --version 2>&1)"

bold "Environment"
if "$PKA" check >/dev/null 2>&1; then ok "python and toolchain check"; record env-check pass; else bad "python and toolchain check"; record env-check FAIL; fi
keys=$(env | grep -E '^(XAI|OPENAI|GLM|ANTHROPIC|GEMINI)_API_KEY=|^LLM_PROVIDER=' || true)
if [ -z "$keys" ]; then ok "no model keys or provider selection in the environment"; record no-keys pass; else bad "model variables present: $keys"; record no-keys FAIL; fi
if ls tools/llm.py tools/lmstudio.py tools/xai.py tools/glm.py tools/generate_thumbnail.py >/dev/null 2>&1; then
  bad "provider modules present"; record no-provider-modules FAIL
else ok "no provider modules in tools/"; record no-provider-modules pass; fi
if grep -rqE '^\s*(import|from) (llm|lmstudio|xai|glm)\b' --include='*.py' tools discord-bridge/bot.py 2>/dev/null; then
  bad "a tool still imports a provider"; record no-provider-imports FAIL
else ok "no tool imports a provider"; record no-provider-imports pass; fi

bold "Unit suite"
if out=$("$PKA" test 2>&1) && echo "$out" | grep -qE '^OK'; then
  ok "$(echo "$out" | grep -E '^Ran ')"; record unit-suite pass
else bad "unit suite failed"; echo "$out" | tail -15; record unit-suite FAIL; fi

run_acceptance() {
  local label="$1"; shift
  bold "Acceptance: $label"
  if out=$(bash "$@" "$ROOT" 2>&1); then
    echo "$out" | grep -E '✓' | sed 's/^/  /' | tail -3
    ok "$label complete"; record "$label" pass
  else
    echo "$out" | tail -20
    bad "$label failed"; record "$label" FAIL
  fi
}
run_acceptance "develop"            "$HERE/develop.sh"
run_acceptance "article intake"     "$HERE/article.sh" intake
run_acceptance "article transcript" "$HERE/article.sh" transcript
run_acceptance "publish owner"      "$HERE/publish.sh" owner
run_acceptance "publish automated"  "$HERE/publish.sh" automated

bold "Summary"
failed=0
for entry in "${results[@]}"; do
  name="${entry%%|*}"; status="${entry##*|}"
  if [ "$status" = pass ]; then ok "$name"; else bad "$name"; failed=1; fi
done
if [ "$failed" = 0 ]; then
  echo
  echo "  All three entry points run end to end on this machine with no model service and no model keys."
  echo "  Every approval present was given by the owner; every assistant response was validated on import."
  exit 0
fi
echo; echo "  One or more checks failed; see above." >&2
exit 1
