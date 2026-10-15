#!/bin/bash
# PKA starter bootstrap for macOS. Idempotent: safe to re-run.
#
#   ./bootstrap.sh                     # core install
#   ./bootstrap.sh --with-transcribe   # also install local Whisper (pulls torch, ~2 GB);
#                                      # only needed to transcribe local video files
#   ./bootstrap.sh --skip-brew         # do not touch Homebrew (you manage ffmpeg yourself)
#   ./bootstrap.sh --python PATH       # build the venv from this interpreter instead of
#                                      # uv's standalone CPython (or set PKA_PYTHON)
#   PKA_UV=/path/to/uv ./bootstrap.sh  # use this uv instead of finding or downloading one
#   ./bootstrap.sh --allow-rosetta     # accept an Intel Python on Apple Silicon (not recommended)
#
# What it does: Homebrew deps (ffmpeg, node) -> uv + standalone Python -> venv ->
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
# A Terminal running under Rosetta reports x86_64 on Apple Silicon. Detect it,
# so uv still fetches native arm64 builds unless --allow-rosetta is given.
ROSETTA_SHELL=0
if [ "$HOST_ARCH" = x86_64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = 1 ]; then
  ROSETTA_SHELL=1; [ "$ALLOW_ROSETTA" = 1 ] || HOST_ARCH=arm64
fi
UV_VERSION="0.13.1"   # pinned; bump deliberately
PY_VERSION="3.13"

# Timing: each step and the total, so install changes can be compared.
T0=$SECONDS; TS=$SECONDS
step_done() { printf '    (%ss)\n' "$((SECONDS - TS))"; TS=$SECONDS; }
ROSETTA_FLAG=""; [ "$ALLOW_ROSETTA" = 1 ] && ROSETTA_FLAG="--allow-rosetta"

# 1. Homebrew packages ---------------------------------------------------------
step "1/6 Homebrew packages"
if [ "$SKIP_BREW" = 1 ]; then
  warn "skipped (--skip-brew)"
elif command -v brew >/dev/null 2>&1; then
  brew bundle --file="$ROOT/Brewfile" --no-upgrade >/dev/null || { fail "Homebrew dependency installation failed"; exit 1; }
  ok "brew bundle satisfied"
else
  warn "Homebrew not found: skipped. Python and yt-dlp no longer need it; only ffmpeg (publish and article paths) and node (HyperFrames) come from Homebrew."
fi
step_done

# 2. Python: uv-managed interpreter, then create or validate the environment ---
# uv (https://github.com/astral-sh/uv) downloads a standalone CPython and
# installs prebuilt wheels, so no Homebrew Python and no Xcode command line
# tools are needed. Lookup order: PKA_UV, vendor/bin/uv, uv on PATH, then a
# pinned, checksum-verified download into vendor/bin (no PATH or shell changes).
# uv's Python lives in vendor/python, inside this folder.
step "2/6 Python"
echo "  host: $HOST_ARCH$([ "$ROSETTA_SHELL" = 1 ] && [ "$ALLOW_ROSETTA" = 0 ] && echo ' (this Terminal runs under Rosetta; using native arm64 builds)')"

uv_triple() {
  case "$(uname -s)-$HOST_ARCH" in
    Darwin-arm64)  echo "aarch64-apple-darwin" ;;
    Darwin-x86_64) echo "x86_64-apple-darwin" ;;
    Linux-x86_64)  echo "x86_64-unknown-linux-gnu" ;;
    Linux-aarch64) echo "aarch64-unknown-linux-gnu" ;;
    *) return 1 ;;
  esac
}
sha256_of() { if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1"; else sha256sum "$1"; fi | awk '{print $1}'; }

UV=""
if [ -n "${PKA_UV:-}" ]; then UV="$PKA_UV"
elif [ -x "$ROOT/vendor/bin/uv" ]; then UV="$ROOT/vendor/bin/uv"
elif command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
else
  UV_TRIPLE="$(uv_triple)" || { fail "no uv build for $(uname -s) $HOST_ARCH; install uv yourself (https://docs.astral.sh/uv/) and re-run"; exit 1; }
  UV_URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TRIPLE.tar.gz"
  tmp="$(mktemp -d)"
  if ! curl -fsSL "$UV_URL" -o "$tmp/uv.tar.gz" || ! curl -fsSL "$UV_URL.sha256" -o "$tmp/uv.sha256"; then
    rm -rf "$tmp"; fail "could not download uv $UV_VERSION ($UV_URL). Check the network, or install uv and re-run."; exit 1
  fi
  want="$(awk '{print $1}' "$tmp/uv.sha256")"; have="$(sha256_of "$tmp/uv.tar.gz")"
  if [ -z "$want" ] || [ "$want" != "$have" ]; then
    rm -rf "$tmp"; fail "uv download failed its checksum ($UV_URL)"; exit 1
  fi
  tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
  mkdir -p "$ROOT/vendor/bin"
  cp "$tmp/uv-$UV_TRIPLE/uv" "$ROOT/vendor/bin/uv"; chmod 755 "$ROOT/vendor/bin/uv"
  rm -rf "$tmp"
  UV="$ROOT/vendor/bin/uv"
fi
ok "uv: $UV ($("$UV" --version 2>/dev/null || echo 'version unknown'))"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$ROOT/vendor/python}"

# Which interpreter uv builds the environment from. Default: uv's standalone
# CPython for the host architecture, never a Homebrew or leftover Intel Python.
# --python PATH (or PKA_PYTHON) overrides and is checked first, as before.
if [ -n "$PY_OVERRIDE" ]; then
  [ -x "$PY_OVERRIDE" ] || { fail "--python $PY_OVERRIDE is not executable"; exit 1; }
  if report="$("$PY_OVERRIDE" "$ROOT/tools/env_check.py" --host-arch "$HOST_ARCH" $ROSETTA_FLAG 2>&1)"; then
    ok "python: $PY_OVERRIDE"; echo "$report" | grep -E '^(version|toolchain|warning)' | indent || true
  else
    fail "python $PY_OVERRIDE is not compatible:"; echo "$report" | indent; exit 1
  fi
  UV_PY_ARGS=(--python "$PY_OVERRIDE")
else
  case "$(uname -s)-$HOST_ARCH" in
    Darwin-arm64)  UV_PY_ARGS=(--python "cpython-$PY_VERSION-macos-aarch64" --managed-python) ;;
    Darwin-x86_64) UV_PY_ARGS=(--python "cpython-$PY_VERSION-macos-x86_64" --managed-python) ;;
    *)             UV_PY_ARGS=(--python "$PY_VERSION" --managed-python) ;;
  esac
fi

# An existing environment is validated before it is reused: it may have been
# built by a Rosetta Python or by an interpreter that no longer exists.
venv_check() { "$VENV/bin/python3" "$ROOT/tools/env_check.py" --host-arch "$HOST_ARCH" --venv "$VENV" $ROSETTA_FLAG 2>&1; }
if [ -e "$VENV" ]; then
  if [ -x "$VENV/bin/python3" ] && report="$(venv_check)"; then
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
  "$UV" venv --seed --quiet --no-progress "${UV_PY_ARGS[@]}" "$VENV" || { fail "could not create the Python environment with uv"; exit 1; }
  if report="$(venv_check)"; then
    ok "created venv at discord-bridge/venv ($("$VENV/bin/python3" --version))"
  else
    fail "the new environment failed its check:"; echo "$report" | indent; exit 1
  fi
fi
"$UV" pip install --quiet --no-progress --python "$VENV/bin/python3" -r "$ROOT/requirements.txt" \
  || { fail "Python dependency installation failed"; exit 1; }
ok "requirements installed"
if [ "$WITH_TRANSCRIBE" = 1 ]; then
  "$UV" pip install --quiet --no-progress --python "$VENV/bin/python3" -r "$ROOT/requirements-transcribe.txt" \
    || { fail "Whisper installation failed"; exit 1; }
  ok "local Whisper installed"
fi
step_done

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
step_done

# 4. Local database -------------------------------------------------------------
step "4/6 Local SQLite database"
"$VENV/bin/python3" tools/pka_db.py init >/dev/null || { fail "Database initialization failed"; exit 1; }
ok "data/pka.db initialized (idempotent)"
step_done

# 5. Smoke tests --------------------------------------------------------------
step "5/6 Smoke tests"
if "$VENV/bin/python3" -m unittest discover -s tests -q >/tmp/pka-bootstrap-tests.log 2>&1; then
  ok "tests pass ($(grep -aoE 'Ran [0-9]+ tests' /tmp/pka-bootstrap-tests.log))"
else
  fail "tests failed. See /tmp/pka-bootstrap-tests.log"; tail -20 /tmp/pka-bootstrap-tests.log
  exit 1
fi
step_done

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
{ [ -x "$VENV/bin/yt-dlp" ] || command -v yt-dlp >/dev/null; } && ok "yt-dlp found" \
  || warn "yt-dlp not found: transcript fallback for YouTube sources on the article path."
[ -d "/Applications/CueCam Presenter.app" ] && ok "CueCam Presenter installed" \
  || warn "CueCam Presenter not installed: needed to present and record a deck (https://cuecam-presenter.com). Bundles still build without it."
if [ "$WITH_TRANSCRIBE" = 1 ]; then ok "local Whisper installed (transcribes local video files)"; else
  echo "  - local Whisper not installed: re-run with --with-transcribe if you will transcribe local video files. YouTube captions and supplied transcripts need nothing."
fi
command -v node >/dev/null && ok "node found (only HyperFrames renders use it)" \
  || echo "  - node not found: only needed for HyperFrames renders, which none of the three core paths use."

printf '\n  Install time: %dm %02ds\n' "$(( (SECONDS - T0) / 60 ))" "$(( (SECONDS - T0) % 60 ))"

cat <<'NEXT'

Done. Start with a new idea:
  Open this folder with your assistant and point it at AGENTS.md. Then say:
    "Let’s start a new Typewriter run."
  A rough idea, question, or observation is enough. Your chat needs access to
  this folder and its instructions; no previous personal work is required.

At the start of a run, you can also choose "Check for Typewriter updates".
Your assistant follows docs/updating-typewriter.md; updates are not automatic.

Other entry points and setup:
  1. Pick an entry point:
       develop   interview -> CueCam bundle           docs/larry/development.md
       publish   edited video -> YouTube              docs/larry/video-production.md
       article   video or intake -> written draft     docs/larry/video-production.md, youtube-writing.md
  2. Start your assistant in this directory and point it at AGENTS.md. It runs tools with bin/pka.
  3. YouTube upload and other Google tools need your own OAuth client (tools/GCAL-SETUP.md,
     then bin/pka auth_doctor). Everything else needs no keys.
  4. bin/pka check   re-runs the Python environment check any time.
NEXT
