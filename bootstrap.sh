#!/bin/bash
# PKA starter bootstrap for macOS. Idempotent: safe to re-run.
#
#   ./bootstrap.sh                     # core install
#   ./bootstrap.sh --with-transcribe   # also install local Whisper (pulls torch, ~2 GB);
#                                      # only needed to transcribe local video files
#   ./bootstrap.sh --skip-brew         # do not touch Homebrew (you manage ffmpeg/yt-dlp yourself)
#   ./bootstrap.sh --python PATH       # use this interpreter (or set PKA_PYTHON)
#   ./bootstrap.sh --allow-rosetta     # accept an Intel Python on Apple Silicon (not recommended)
#
# What it does: Homebrew deps -> check the Python and toolchain -> Python venv ->
# .env and machine identity -> local SQLite DB -> smoke tests -> report on the
# optional tools each workflow needs. It installs no model service and needs no
# model keys: your own assistant does the thinking.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

WITH_TRANSCRIBE=0; SKIP_BREW=0; ALLOW_ROSETTA=0; PY_OVERRIDE="${PKA_PYTHON:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --with-transcribe) WITH_TRANSCRIBE=1 ;;
    --skip-brew) SKIP_BREW=1 ;;
    --allow-rosetta) ALLOW_ROSETTA=1 ;;
    --python) shift; PY_OVERRIDE="${1:-}"; [ -n "$PY_OVERRIDE" ] || { echo "--python needs a path" >&2; exit 2; } ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
indent() { sed 's/^/      /'; }

VENV="$ROOT/discord-bridge/venv"
HOST_ARCH="$(uname -m)"
ROSETTA_FLAG=""; [ "$ALLOW_ROSETTA" = 1 ] && ROSETTA_FLAG="--allow-rosetta"

# 1. Homebrew packages ---------------------------------------------------------
step "1/6 Homebrew packages"
if [ "$SKIP_BREW" = 1 ]; then
  warn "skipped (--skip-brew)"
elif command -v brew >/dev/null 2>&1; then
  brew bundle --file="$ROOT/Brewfile" --no-upgrade >/dev/null || { fail "Homebrew dependency installation failed"; exit 1; }
  ok "brew bundle satisfied"
else
  warn "Homebrew not found. Install from https://brew.sh then re-run, or use --skip-brew and install python, ffmpeg, yt-dlp yourself."
fi

# 2. Python: select, check, then create or validate the environment -------------
step "2/6 Python"
echo "  host: $HOST_ARCH"

# Candidate interpreters, most specific first. Native Homebrew locations come
# before whatever is first on PATH, because a leftover Intel Python in
# /usr/local is the usual way an Apple Silicon install goes wrong.
candidates=()
if [ -n "$PY_OVERRIDE" ]; then
  candidates+=("$PY_OVERRIDE")
else
  for c in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 \
           python3.13 python3.12 python3.11 python3; do
    p="$(command -v "$c" 2>/dev/null || true)"
    [ -n "$p" ] && candidates+=("$p")
  done
fi

PY=""
reports=""
for c in "${candidates[@]}"; do
  [ -x "$c" ] || continue
  if report="$("$c" "$ROOT/tools/env_check.py" --host-arch "$HOST_ARCH" $ROSETTA_FLAG 2>&1)"; then
    PY="$c"; ok "python: $c"; echo "$report" | grep -E '^(version|toolchain|warning)' | indent
    break
  else
    reports="$reports
--- $c
$report"
  fi
done
if [ -z "$PY" ]; then
  fail "no compatible Python found. Each candidate was checked:"
  echo "$reports" | indent
  echo
  echo "  Install a native Python (brew install python@3.13) or pass --python /path/to/python3, then re-run." >&2
  exit 1
fi

# An existing environment is validated before it is reused: it may have been
# built by a Rosetta Python or by an interpreter that no longer exists.
if [ -e "$VENV" ]; then
  if [ -x "$VENV/bin/python3" ] && report="$("$VENV/bin/python3" "$ROOT/tools/env_check.py" --host-arch "$HOST_ARCH" --venv "$VENV" $ROSETTA_FLAG 2>&1)"; then
    ok "existing venv at discord-bridge/venv is compatible ($("$VENV/bin/python3" --version))"
  else
    fail "existing venv at discord-bridge/venv is not usable:"
    echo "${report:-its python3 does not run}" | indent
    echo
    echo "  Bootstrap will not reuse or delete it. Remove it yourself, then re-run:" >&2
    echo "    rm -rf discord-bridge/venv && ./bootstrap.sh" >&2
    exit 1
  fi
else
  "$PY" -m venv "$VENV" && ok "created venv at discord-bridge/venv"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$ROOT/requirements.txt" || { fail "Python dependency installation failed"; exit 1; }
ok "requirements installed"
if [ "$WITH_TRANSCRIBE" = 1 ]; then
  "$VENV/bin/pip" install --quiet -r "$ROOT/requirements-transcribe.txt" || { fail "Whisper installation failed"; exit 1; }
  ok "local Whisper installed"
fi

# 3. Secrets and machine identity --------------------------------------------
step "3/6 Configuration"
if [ ! -f discord-bridge/.env ]; then
  cp discord-bridge/.env.example discord-bridge/.env
  ok "created discord-bridge/.env from the example (only needed for Discord or YouTube upload)"
else
  ok "discord-bridge/.env present"
fi
if [ ! -f config/machine.local.json ]; then
  HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
  SLUG="$(echo "$HOST" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' '-')"
  OWNER="$(id -F 2>/dev/null || id -un)"
  sed -e "s/my-macbook.local/$HOST.local/" -e "s/PKA-my-macbook/PKA-$SLUG/" -e "s/my-macbook/$SLUG/" \
      -e "s/My MacBook/$HOST/" -e "s/Your Name/$OWNER/" \
      config/machine.local.json.example > config/machine.local.json
  ok "wrote config/machine.local.json (machine_id=$SLUG, owner=$OWNER). Edit if wrong."
else
  ok "config/machine.local.json present"
fi

# 4. Local database -------------------------------------------------------------
step "4/6 Local SQLite database"
"$VENV/bin/python3" tools/pka_db.py init >/dev/null || { fail "Database initialization failed"; exit 1; }
ok "data/pka.db initialized (idempotent)"

# 5. Smoke tests --------------------------------------------------------------
step "5/6 Smoke tests"
if "$VENV/bin/python3" -m unittest discover -s tests -q >/tmp/pka-bootstrap-tests.log 2>&1; then
  ok "tests pass ($(grep -oE 'Ran [0-9]+ tests' /tmp/pka-bootstrap-tests.log))"
else
  fail "tests failed. See /tmp/pka-bootstrap-tests.log"; tail -20 /tmp/pka-bootstrap-tests.log
  exit 1
fi

# 6. Optional tools, by the workflow that needs them ---------------------------
step "6/6 Optional tools"
echo "  Your assistant: any AI assistant that can read files and run commands in this directory"
echo "  (Claude Code, Codex, or another). It uses bin/pka to run the tools."
if command -v claude >/dev/null || command -v codex >/dev/null; then
  ok "an assistant CLI is on PATH"
else
  warn "no assistant CLI found on PATH. A browser-only assistant also works; you then run bin/pka commands yourself."
fi
command -v ffmpeg >/dev/null && ok "ffmpeg found" \
  || warn "ffmpeg not found: needed by the publish and article paths (clean, QC, local transcription). Not needed for interview -> CueCam."
command -v yt-dlp >/dev/null && ok "yt-dlp found" \
  || warn "yt-dlp not found: transcript fallback for YouTube sources on the article path."
[ -d "/Applications/CueCam Presenter.app" ] && ok "CueCam Presenter installed" \
  || warn "CueCam Presenter not installed: needed to present and record a deck (https://cuecam.app). Bundles still build without it."
if [ "$WITH_TRANSCRIBE" = 1 ]; then ok "local Whisper installed (transcribes local video files)"; else
  echo "  - local Whisper not installed: re-run with --with-transcribe if you will transcribe local video files. YouTube captions and supplied transcripts need nothing."
fi
command -v node >/dev/null && ok "node found (only HyperFrames renders use it)" \
  || echo "  - node not found: only needed for HyperFrames renders, which none of the three core paths use."

cat <<'NEXT'

Done. Next:
  1. Pick an entry point:
       develop   interview -> CueCam bundle           docs/larry/development.md
       publish   edited video -> YouTube              docs/larry/video-production.md
       article   video or intake -> written draft     docs/larry/video-production.md, youtube-writing.md
  2. Start your assistant in this directory and point it at AGENTS.md. It runs tools with bin/pka.
  3. YouTube upload and other Google tools need your own OAuth client (tools/GCAL-SETUP.md,
     then bin/pka auth_doctor). Everything else needs no keys.
  4. bin/pka check   re-runs the Python and toolchain check any time.
NEXT
