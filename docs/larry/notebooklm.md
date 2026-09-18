# NotebookLM Q&A (curated corpus, with citations)

Larry answers questions from the owner's **own** NotebookLM notebooks directly
with `tools/notebooklm.py`. Each notebook is a curated source set; `ask` returns
an answer with inline `[N]` citations that trace back to real source spans. Use
this when the answer should come from the owner's gathered sources — **not** the
open web (that is Pax).

This is a **hybrid workflow**: the CLI is deterministic, but "which notebook"
and "is this a NotebookLM question or a Pax web question" can be ambiguous.
Confirm the notebook if it isn't obvious.

## When this applies (vs. Pax / KB)

- **NotebookLM** — the question is answerable from the owner's curated sources:
  "ask my notebook," "what do my sources say about X," "ask NotebookLM," a named
  notebook, or a question that clearly maps to an existing notebook topic
  (**smart-prefer** it in that case, even without the keyword).
- **Pax** (`docs/larry/operations.md`) — open-web research, comparison, anything
  needing sources the owner hasn't gathered. When unsure, fall back to Pax/KB.
- **KB search** — a fact already indexed in local markdown; no synthesis needed.

NotebookLM's edge is provenance: every claim that carries a citation has a
receipt. That is also why it slots straight into the owner's
**Performed / Verified / Rejected** protocol (below).

## Recipe

Run everything through the canonical venv:

1. **Confirm auth** (once per machine; cookies persist):

   ```
   discord-bridge/venv/bin/python3 tools/notebooklm.py doctor
   ```

   If it reports `needs_login`, the owner re-runs
   `discord-bridge/venv/bin/notebooklm login --browser-cookies chrome`
   (the owner's Google session lives in Chrome, not Safari; Firefox isn't
   installed). The login is interactive and is the **owner's** to run.

2. **Find the notebook** when the title is fuzzy:

   ```
   discord-bridge/venv/bin/python3 tools/notebooklm.py list
   discord-bridge/venv/bin/python3 tools/notebooklm.py resolve "<title or id-prefix>"
   ```

   `-n` accepts an exact title or a (partial) id, **not** a fuzzy name. The tool
   resolves exact title → unique substring → id-prefix, and **refuses ambiguous
   title matches** (titles can collide — there are two "…Realtime Revolution…"
   notebooks). When ambiguous, name the exact title or pass an id-prefix.

3. **Ask:**

   ```
   discord-bridge/venv/bin/python3 tools/notebooklm.py ask -n "<title-or-id>" "<question>"
   ```

   `ask` always uses `--json` under the hood (which implies `--yes`), so it never
   blocks on a prompt. Add `--timeout N` (default 180s) for big notebooks,
   `-s <source_id>` to limit to specific sources, and `-c <conversation_id>` to
   continue a thread. **Avoid `--new`** unless the owner wants a fresh thread —
   it deletes the notebook's current server-side conversation.

4. **Report.** Lead with the answer and the resolved notebook (confirm the right
   corpus was hit). Inline `[N]` markers map to `result.references[N]`
   (`source_id` + `cited_text`).

## Performed / Verified / Rejected (publishable / fact-sensitive answers)

When a NotebookLM answer feeds something the owner will publish, send, or rely
on for facts, run it through the receipts protocol instead of a generic Vera
pass:

```
discord-bridge/venv/bin/python3 tools/notebooklm.py ask -n "<title-or-id>" "<question>" --receipts
```

`--receipts` adds a `receipts` block that does the **deterministic** half:

- **VERIFIED** — sentences carrying an inline `[N]`; each receipt lists its
  `source_id` and the exact `cited_text`.
- **needs_judgment** — sentences with no citation. These are **PERFORMED**
  (original framing / the owner's own voice — no receipt needed) or **REJECTED**
  (an unsupported factual claim — flag before use). The wrapper does not guess
  this split; **Vera** (or the owner) decides, the same way the Clemens /
  "showing the receipts" letters are audited.

`summary` reports `verified_claims`, `needs_judgment`, and
`receipts_used / available`. Quick, non-publishable lookups skip `--receipts`
and return directly.

## Discord 5-minute window discipline

A NotebookLM `ask` round-trip commonly takes 30–120s (longer on large
notebooks). For a single quick question that is fine inline. For a batch of
questions, a long synthesis, or a `--receipts` audit feeding a publishable
piece, acknowledge first and run it as a local/background job, then reconcile —
do not block the Discord turn. This mirrors the watch-video and bulk-checkbook
discipline.

## Caveats

- **Auth is Chrome-cookie based and can expire.** If `ask` starts failing with a
  login hint, run `doctor`; re-auth with the `login --browser-cookies chrome`
  command above. Per-notebook access follows the signed-in Google account.
- **Each machine is standalone under federation.** NotebookLM auth/profile lives at
  `~/.notebooklm/profiles/default/` on the running machine; the wrapper shares that
  default profile rather than overriding `--storage`. See `docs/federation.md`.
- **The tool wraps `notebooklm-py` (installed in the discord-bridge venv).**
  `auth check` / `doctor` render text tables and ignore `--json`; `list` and
  `ask` emit real JSON. The wrapper handles both.
