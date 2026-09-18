#!/bin/bash
# PKA starter bootstrap for macOS. Idempotent: safe to re-run.
#
#   ./bootstrap.sh                 # core install
#   ./bootstrap.sh --with-transcribe   # also install local Whisper (pulls torch, ~2 GB)
#   ./bootstrap.sh --skip-brew     # do not touch Homebrew (you manage ffmpeg/node/yt-dlp yourself)
#
# What it does: Homebrew deps -> Python venv -> .env and machine identity ->
# local SQLite DB -> smoke tests -> report on optional tools (hyperframes, claude, CueCam).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

WITH_TRANSCRIBE=0; SKIP_BREW=0
for a in "$@"; do
  case "$a" in
    --with-transcribe) WITH_TRANSCRIBE=1 ;;
    --skip-brew) SKIP_BREW=1 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# 1. Homebrew packages ---------------------------------------------------------
step "1/6 Homebrew packages"
if [ "$SKIP_BREW" = 1 ]; then
  warn "skipped (--skip-brew)"
elif command -v brew >/dev/null 2>&1; then
  brew bundle --file="$ROOT/Brewfile" --no-upgrade >/dev/null && ok "brew bundle satisfied"
else
  warn "Homebrew not found. Install from https://brew.sh then re-run, or use --skip-brew and install ffmpeg, node, yt-dlp yourself."
fi

# 2. Python virtualenv --------------------------------------------------------
step "2/6 Python virtualenv"
PY="$(command -v python3.13 || command -v python3)"
VENV="$ROOT/discord-bridge/venv"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' || { echo "Python 3.11+ required (found $("$PY" --version))"; exit 1; }
[ -x "$VENV/bin/python3" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$ROOT/requirements.txt"
ok "venv at discord-bridge/venv ($("$VENV/bin/python3" --version))"
if [ "$WITH_TRANSCRIBE" = 1 ]; then
  "$VENV/bin/pip" install --quiet -r "$ROOT/requirements-transcribe.txt" && ok "local Whisper installed"
fi

# 3. Secrets and machine identity --------------------------------------------
step "3/6 Configuration"
if [ ! -f discord-bridge/.env ]; then
  cp discord-bridge/.env.example discord-bridge/.env
  warn "created discord-bridge/.env from the example. Fill in the keys you use."
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
"$VENV/bin/python3" tools/pka_db.py init >/dev/null && ok "data/pka.db initialized (idempotent)"

# 5. Smoke tests --------------------------------------------------------------
step "5/6 Smoke tests"
if "$VENV/bin/python3" -m unittest discover -s tests -q >/tmp/pka-bootstrap-tests.log 2>&1; then
  ok "tests pass ($(grep -oE 'Ran [0-9]+ tests' /tmp/pka-bootstrap-tests.log))"
else
  warn "tests failed. See /tmp/pka-bootstrap-tests.log"; tail -20 /tmp/pka-bootstrap-tests.log
fi

# 6. Optional tools -----------------------------------------------------------
step "6/6 Optional tools"
command -v claude       >/dev/null && ok "claude CLI found"      || warn "claude CLI not found: npm install -g @anthropic-ai/claude-code (the orchestrator runs inside Claude Code)"
command -v hyperframes  >/dev/null && ok "hyperframes CLI found" || warn "hyperframes not found: npm install -g hyperframes (needed for HTML video renders)"
command -v ffmpeg       >/dev/null && ok "ffmpeg found"          || warn "ffmpeg not found"
command -v yt-dlp       >/dev/null && ok "yt-dlp found"          || warn "yt-dlp not found (transcript fallback)"
[ -d "/Applications/CueCam Presenter.app" ] && ok "CueCam Presenter installed" || warn "CueCam Presenter not installed (recording stage; https://cuecam.app). Grant Accessibility + Screen Recording to Terminal for live control."
command -v ollama       >/dev/null && ok "ollama found"          || warn "ollama not found (optional local models)"

cat <<'NEXT'

Done. Next:
  1. Open discord-bridge/.env and add any API keys you will use.
  2. Google Calendar/Docs/Drive/YouTube tools need your own OAuth client: see tools/GCAL-SETUP.md
     then run   discord-bridge/venv/bin/python3 tools/auth_doctor.py
  3. Start a session from this directory:   claude
     The orchestrator reads CLAUDE.md; say "start a video project" or "package this YouTube URL".
  4. Read docs/larry/video-production.md and docs/larry/youtube-writing.md for the two main workflows.
NEXT
