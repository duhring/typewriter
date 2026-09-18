#!/usr/bin/env python3
"""
NotebookLM CLI wrapper for PKA.
Usage: discord-bridge/venv/bin/python3 tools/notebooklm.py <command> [options]

Thin, non-interactive wrapper around the `notebooklm` CLI (notebooklm-py),
shaped for Larry: name->ID resolution, clean JSON to stdout, errors to stderr.

Commands:
  doctor                Check auth/profile health (notebooklm auth check + doctor)
  list                  List all notebooks (JSON passthrough + count)
  resolve <name|id>     Resolve a notebook name (or partial id) to a full id
  ask <question>        Ask a notebook a question (citations preserved)

Notes:
  - Logging in is interactive and is NOT done here. Run it yourself once:
        notebooklm login --browser-cookies safari        # no window, reads Safari cookies
        notebooklm login --browser chrome                 # opens system Chrome to sign in
    then verify with: tools/notebooklm.py doctor
  - `ask` always uses --json (which implies --yes), so it never blocks on a prompt.
  - --notebook accepts a notebook title OR a (partial) id. Titles are resolved
    to ids via `notebooklm list`; ids/partial-ids are passed straight through.
  - `ask --receipts` adds a Performed/Verified/Rejected breakdown: VERIFIED =
    sentences carrying an inline [N] (receipt = source_id + cited_text);
    needs_judgment = uncited sentences (PERFORMED or REJECTED — Vera decides).

Output: JSON to stdout. Errors to stderr (as {"error": ...}, exit 1).
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
NB_BIN = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "notebooklm"

# Default per-ask HTTP timeout (seconds). NotebookLM answers can be slow.
DEFAULT_ASK_TIMEOUT = 180
# Hard ceiling for the subprocess so the wrapper can never hang forever.
SUBPROCESS_GRACE = 60


def _bin() -> str:
    if not NB_BIN.exists():
        raise RuntimeError(
            f"notebooklm CLI not found at {NB_BIN}. "
            "Install with: discord-bridge/venv/bin/python3 -m pip install 'notebooklm-py[cookies]'"
        )
    return str(NB_BIN)


def _run_raw(args, timeout=120):
    """Run the notebooklm CLI without raising. Returns (returncode, stdout, stderr)."""
    cmd = [_bin(), "--quiet", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"notebooklm timed out after {timeout}s: {' '.join(args)}")
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def run_nb(args, parse_json=True, timeout=120):
    """Run the notebooklm CLI and return parsed JSON (or raw stdout).

    Passes the global --quiet flag so status/INFO noise stays off stdout and
    JSON parsing is reliable. Raises RuntimeError with stderr on failure.
    """
    returncode, stdout, stderr = _run_raw(args, timeout=timeout)

    if returncode != 0:
        msg = (stderr or stdout or "").strip()
        # Surface the common unauthenticated case with an actionable hint.
        low = msg.lower()
        if any(k in low for k in ("login", "unauthenticated", "not authenticated", "sign in")):
            raise RuntimeError(
                f"{msg or 'notebooklm command failed'}  "
                "(hint: run `notebooklm login --browser-cookies safari` then `tools/notebooklm.py doctor`)"
            )
        raise RuntimeError(msg or f"notebooklm exited {returncode}")

    out = stdout.strip()
    if not parse_json:
        return out
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"notebooklm did not return JSON for: {' '.join(args)}\n{out[:500]}")


def _notebook_items(listing):
    """Normalize `notebooklm list --json` output to a list of dicts.

    The CLI schema isn't contractually fixed, so accept either a bare list or
    a dict wrapping the list under a plausible key.
    """
    if isinstance(listing, list):
        return listing
    if isinstance(listing, dict):
        for key in ("notebooks", "items", "data", "results"):
            val = listing.get(key)
            if isinstance(val, list):
                return val
        # dict-of-notebooks fallback
        vals = list(listing.values())
        if vals and all(isinstance(v, dict) for v in vals):
            return vals
    return []


def _id_of(item):
    for key in ("id", "notebook_id", "notebookId", "uuid"):
        if isinstance(item, dict) and item.get(key):
            return str(item[key])
    return None


def _title_of(item):
    for key in ("title", "name", "summary"):
        if isinstance(item, dict) and item.get(key):
            return str(item[key])
    return ""


def list_notebooks():
    return _notebook_items(run_nb(["list", "--json"]))


def resolve_notebook(value):
    """Resolve a notebook title (or partial id) to a full id.

    Returns {"id", "title", "match"}. `match` is one of:
      exact_title | unique_substring | passthrough_id
    Raises on an ambiguous title match so callers don't silently hit the wrong
    notebook.
    """
    items = list_notebooks()
    val_l = value.strip().lower()

    # 1) exact (case-insensitive) title match
    exact = [it for it in items if _title_of(it).lower() == val_l]
    if len(exact) == 1:
        return {"id": _id_of(exact[0]), "title": _title_of(exact[0]), "match": "exact_title"}

    # 2) exact id match (full id)
    by_id = [it for it in items if _id_of(it) == value]
    if by_id:
        return {"id": _id_of(by_id[0]), "title": _title_of(by_id[0]), "match": "exact_id"}

    # 3) unique title substring
    subs = [it for it in items if val_l in _title_of(it).lower()]
    if len(subs) == 1:
        return {"id": _id_of(subs[0]), "title": _title_of(subs[0]), "match": "unique_substring"}
    if len(subs) > 1:
        raise RuntimeError(
            f"Ambiguous notebook '{value}'; matches: "
            + ", ".join(f"{_title_of(it)} ({_id_of(it)})" for it in subs)
        )

    # 4) unique id-prefix match (the CLI also accepts partial ids directly)
    prefix = [it for it in items if (_id_of(it) or "").startswith(value)]
    if len(prefix) == 1:
        return {"id": _id_of(prefix[0]), "title": _title_of(prefix[0]), "match": "id_prefix"}

    # 5) nothing matched locally; hand the raw value to the CLI as a partial id
    return {"id": value, "title": None, "match": "passthrough_id"}


# ── Receipts (Performed / Verified / Rejected provenance) ────────────
#
# Maps a NotebookLM answer onto the owner's "showing the receipts" protocol:
#   VERIFIED       sentence carries an inline [N] -> receipt = references[N]
#   needs_judgment sentence carries NO citation   -> PERFORMED (original voice)
#                  or REJECTED (unsupported factual claim); Vera/owner decides.
# Only the deterministic split (cited vs. uncited) is done here on purpose.

_CITE_RE = re.compile(r"\[(\d+(?:\s*[-,]\s*\d+)*)\]")


def _expand_citation_token(tok):
    """Expand a marker body like '1', '1-3', '14, 15' into a set of ints."""
    nums = set()
    for part in tok.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            if a.strip().isdigit() and b.strip().isdigit():
                a, b = int(a), int(b)
                nums.update(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            nums.add(int(part))
    return nums


_MD_HEADER_RE = re.compile(r"^\s*#{1,6}\s*")
# Split on sentence-ending punctuation only when it follows a letter — so we
# don't break on ordinals ("1.") or after a citation marker ("]."). This keeps
# markdown headers and cited sentences intact while still splitting plain prose.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[^\W\d][.!?])\s+")


def _split_units(text):
    """Split an answer into reviewable units (per line, then per sentence)."""
    units = []
    for line in text.split("\n"):
        line = _MD_HEADER_RE.sub("", line).strip()
        if not line:
            continue
        for part in _SENTENCE_SPLIT_RE.split(line):
            part = part.strip().strip("*").strip()
            if part:
                units.append(part)
    return units


def build_receipts(result):
    """Classify an ask result's claims per the Performed/Verified/Rejected protocol."""
    answer = result.get("answer", "") or ""
    refs = result.get("references", []) or []
    by_num = {}
    for r in refs:
        n = r.get("citation_number")
        if n is not None:
            by_num[int(n)] = r

    verified_sentences = []
    needs_judgment = []
    cited_numbers = set()
    for unit in _split_units(answer):
        nums = set()
        for m in _CITE_RE.finditer(unit):
            nums |= _expand_citation_token(m.group(1))
        if nums:
            cited_numbers |= nums
            verified_sentences.append({"text": unit, "citations": sorted(nums)})
        else:
            needs_judgment.append(unit)

    verified = [{
        "citation_number": n,
        "source_id": (by_num.get(n) or {}).get("source_id"),
        "cited_text": (by_num.get(n) or {}).get("cited_text"),
    } for n in sorted(cited_numbers)]
    unused = sorted(int(r["citation_number"]) for r in refs
                    if r.get("citation_number") is not None
                    and int(r["citation_number"]) not in cited_numbers)

    return {
        "protocol": "performed/verified/rejected",
        "verified": verified,                      # receipts actually cited in the answer
        "verified_sentences": verified_sentences,  # carry >=1 receipt
        "needs_judgment": needs_judgment,          # PERFORMED or REJECTED — Vera/owner decides
        "summary": {
            "verified_claims": len(verified_sentences),
            "needs_judgment": len(needs_judgment),
            "receipts_available": len(refs),
            "receipts_used": len(verified),
            "receipts_unused": len(unused),
            "unused_citation_numbers": unused,
        },
    }


# ── Commands ─────────────────────────────────────────────────────────

# Phrases that appear ONLY when login is required (not table row labels, which
# show up whether or not you're authenticated).
_NOT_AUTHED_SIGNALS = (
    "not authenticated",
    "storage file not found",
    "run 'notebooklm login'",
    "please log in",
    "please authenticate",
)


def cmd_doctor(args):
    """Check auth/profile health.

    `auth check` / `doctor` render Rich text tables and ignore --json, so we
    read their text and decide authentication from failure signals rather than
    a JSON field.
    """
    rc, out, err = _run_raw(["auth", "check"], timeout=60)
    text = "\n".join(p for p in (out.strip(), err.strip()) if p)
    low = text.lower()
    needs_login = any(sig in low for sig in _NOT_AUTHED_SIGNALS) or rc != 0
    print(json.dumps({
        "status": "needs_login" if needs_login else "authenticated",
        "authenticated": not needs_login,
        "detail": text,
    }, indent=2))


def cmd_list(args):
    """List all notebooks."""
    items = list_notebooks()
    notebooks = [{"id": _id_of(it), "title": _title_of(it), "raw": it} for it in items]
    print(json.dumps({"notebooks": notebooks, "count": len(notebooks)}, indent=2))


def cmd_resolve(args):
    """Resolve a notebook name/partial-id to a full id."""
    print(json.dumps(resolve_notebook(args.notebook), indent=2))


def cmd_ask(args):
    """Ask a notebook a question (citations preserved via --json)."""
    if not args.question and not args.prompt_file:
        raise RuntimeError("Provide a question, or --prompt-file PATH (or '-' for stdin).")

    notebook = None
    if args.notebook:
        notebook = resolve_notebook(args.notebook)

    nb_args = ["ask", "--json"]
    if notebook:
        nb_args += ["-n", notebook["id"]]
    if args.new:
        nb_args += ["--new", "-y"]
    if args.conversation_id:
        nb_args += ["-c", args.conversation_id]
    for src in args.source or []:
        nb_args += ["-s", src]
    nb_args += ["--timeout", str(args.timeout)]
    if args.prompt_file:
        nb_args += ["--prompt-file", args.prompt_file]
    else:
        nb_args.append(args.question)

    # Give the subprocess a little more wall-clock than the HTTP timeout.
    result = run_nb(nb_args, timeout=args.timeout + SUBPROCESS_GRACE)
    out = {
        "status": "ok",
        "notebook": notebook,
        "result": result,
    }
    if args.receipts:
        out["receipts"] = build_receipts(result if isinstance(result, dict) else {})
    print(json.dumps(out, indent=2))


# ── CLI Setup ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NotebookLM CLI wrapper for PKA")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("doctor", help="Check auth/profile health")
    sub.add_parser("list", help="List all notebooks")

    p_resolve = sub.add_parser("resolve", help="Resolve a notebook name/partial-id to a full id")
    p_resolve.add_argument("notebook", help="Notebook title or (partial) id")

    p_ask = sub.add_parser("ask", help="Ask a notebook a question")
    p_ask.add_argument("question", nargs="?", help="The question (omit if using --prompt-file)")
    p_ask.add_argument("-n", "--notebook", help="Notebook title or (partial) id")
    p_ask.add_argument("--prompt-file", help="Read the question from a file ('-' for stdin)")
    p_ask.add_argument("--new", action="store_true", help="Start a fresh conversation (DESTRUCTIVE: deletes current server-side conversation)")
    p_ask.add_argument("-c", "--conversation-id", help="Continue a specific conversation")
    p_ask.add_argument("-s", "--source", action="append", help="Limit to a source id (repeatable)")
    p_ask.add_argument("--timeout", type=int, default=DEFAULT_ASK_TIMEOUT, help=f"HTTP timeout seconds (default {DEFAULT_ASK_TIMEOUT})")
    p_ask.add_argument("--receipts", action="store_true", help="Also classify the answer per the Performed/Verified/Rejected protocol")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "doctor": cmd_doctor,
        "list": cmd_list,
        "resolve": cmd_resolve,
        "ask": cmd_ask,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
