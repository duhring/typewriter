#!/usr/bin/env bash
# Re-index markdown deliverables into this peer's local DB.
#
# Category resolution, in order:
#   1. the file's own `category:` frontmatter
#   2. inferred from its directory (see map below)
#   3. knowledge-base
#
# Step 2 matters: most of the archive predates the frontmatter convention, and
# without it ~2/3 of files index as generic knowledge-base and lose their facet.
#
# Safe to re-run: indexing is idempotent and updates the existing row in place.
#
# Usage:  bash tools/reindex_session_files.sh <file-or-glob> [more...]
set -uo pipefail
PY="discord-bridge/venv/bin/python3"
[ -x "$PY" ] || PY="python3"

infer_from_path() {
  case "$1" in
    */owners-inbox/blog/*)                  echo "blog" ;;
    */owners-inbox/youtube/*|owners-inbox/youtube/*)          echo "youtube" ;;
    */owners-inbox/session-logs/*|owners-inbox/session-logs/*) echo "session-log" ;;
    */owners-inbox/sop-promotions/*|owners-inbox/sop-promotions/*) echo "sop-promotion" ;;
    */owners-inbox/editorial-recurrences/*|owners-inbox/editorial-recurrences/*) echo "editorial-recurrence" ;;
    */owners-inbox/records/*|owners-inbox/records/*)          echo "records" ;;
    */owners-inbox/transcript-extracts/*|owners-inbox/transcript-extracts/*) echo "transcript-extract" ;;
    */owners-inbox/transcripts/*|owners-inbox/transcripts/*)  echo "transcript" ;;
    */owners-inbox/tasks/*|owners-inbox/tasks/*)              echo "task-record" ;;
    owners-inbox/blog/*)                                      echo "blog" ;;
    *)                                                        echo "" ;;
  esac
}

ok=0; fail=0; inferred=0; declare -a failed_paths=()
for f in "$@"; do
  [ -f "$f" ] || continue
  cat=$(awk -F': *' '/^category:/{gsub(/"/,"",$2); print $2; exit}' "$f")
  src="frontmatter"
  if [ -z "$cat" ]; then
    cat=$(infer_from_path "$f"); src="inferred"
    [ -n "$cat" ] && inferred=$((inferred+1))
  fi
  if [ -z "$cat" ]; then cat="knowledge-base"; src="default"; fi
  if "$PY" tools/pka_index.py index-markdown --file "$f" --category "$cat" >/dev/null 2>&1; then
    printf "  %-11s %-24s %s\n" "$src" "$cat" "$f"; ok=$((ok+1))
  else
    printf "  FAILED      %-24s %s\n" "$cat" "$f"; fail=$((fail+1)); failed_paths+=("$f")
  fi
done
echo
echo "indexed: $ok   (category inferred from path for $inferred)   failed: $fail"
for p in ${failed_paths+"${failed_paths[@]}"}; do echo "  failed: $p"; done
