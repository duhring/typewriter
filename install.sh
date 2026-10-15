#!/bin/bash
# One-line Typewriter install for macOS:
#
#   curl -fsSL https://raw.githubusercontent.com/duhring/typewriter/main/install.sh | bash
#
# Options go after `bash -s --`, for example:
#
#   curl -fsSL https://raw.githubusercontent.com/duhring/typewriter/main/install.sh | bash -s -- --dir ~/PKA --with-transcribe
#
#   --dir PATH        where to put Typewriter (default: ~/Typewriter, or $TYPEWRITER_DIR)
#   --ref NAME        branch or tag to clone (default: main)
#   --no-bootstrap    clone only; run ./bootstrap.sh yourself later
#   anything else     passed through to ./bootstrap.sh (--with-transcribe, --skip-brew, ...)
#
# What it does: checks for git, clones the repository (so later updates can use
# git), then runs ./bootstrap.sh inside it. It never deletes or overwrites an
# existing folder. Nothing is installed outside that folder except what
# bootstrap.sh itself reports (Homebrew packages, only when Homebrew is present).
#
# Everything is inside main(), so a partly downloaded script does nothing.

set -euo pipefail

main() {
  local repo="${TYPEWRITER_REPO:-https://github.com/duhring/typewriter.git}"
  local dir="${TYPEWRITER_DIR:-$HOME/Typewriter}"
  local ref="main" run_bootstrap=1
  local passthrough=()

  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) shift; dir="${1:?--dir needs a path}" ;;
      --ref) shift; ref="${1:?--ref needs a branch or tag}" ;;
      --no-bootstrap) run_bootstrap=0 ;;
      *) passthrough+=("$1") ;;
    esac
    shift
  done

  say()  { printf '\033[1m%s\033[0m\n' "$*"; }
  ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
  fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

  say "Typewriter install"

  case "$(uname -s)" in
    Darwin) ;;
    Linux) echo "  Linux: supported for development and tests only." ;;
    *) fail "Typewriter runs on macOS."; exit 1 ;;
  esac

  # git. On a new Mac, /usr/bin/git is a stub that asks to install the Command
  # Line Tools; check for a working git before relying on it.
  if ! git --version >/dev/null 2>&1; then
    fail "git is not installed."
    if [ "$(uname -s)" = Darwin ]; then
      echo "  Install Apple's Command Line Tools (a system dialog opens), then run this command again:" >&2
      echo "    xcode-select --install" >&2
    fi
    exit 1
  fi
  ok "git: $(git --version | awk '{print $3}')"

  # Target folder: reuse an existing Typewriter clone, never touch anything else.
  if [ -e "$dir" ]; then
    if [ -f "$dir/bootstrap.sh" ] && [ -f "$dir/AGENTS.md" ] && git -C "$dir" rev-parse --git-dir >/dev/null 2>&1; then
      ok "Typewriter already at $dir; not cloning again (updates: say \"Update my Typewriter\" to your assistant)"
    else
      fail "$dir already exists and is not a Typewriter clone. Nothing was changed."
      echo "  Choose another folder: ... | bash -s -- --dir ~/somewhere-else" >&2
      exit 1
    fi
  else
    mkdir -p "$(dirname "$dir")"
    git clone --quiet --branch "$ref" "$repo" "$dir" || { fail "could not clone $repo ($ref)"; exit 1; }
    ok "cloned $ref into $dir"
  fi

  if [ "$run_bootstrap" = 0 ]; then
    echo
    echo "Cloned only. Next:"
    echo "  cd \"$dir\" && ./bootstrap.sh"
    return 0
  fi

  echo
  # stdin is this script when piped from curl; bootstrap must not read it.
  (cd "$dir" && ./bootstrap.sh ${passthrough[@]+"${passthrough[@]}"} </dev/null)

  echo
  echo "Typewriter is in: $dir"
}

main "$@"
