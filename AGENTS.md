# AGENTS.md — Practical Contract for Coding Agents

This file is the working contract for any assistant operating inside the PKA repo: the owner's chosen AI assistant running the workflows below, and any coding agent changing the tools. It is written for any assistant that can read files and run commands; nothing here depends on a particular product. For high-level orientation, read [llms.txt](llms.txt) first. For Larry's routing constitution, see [CLAUDE.md](CLAUDE.md).

## Your role in the workflows

You do the thinking; PKA prepares material, validates what comes back, records every run, and never approves anything. Each editorial step is a handoff (`docs/handoff-contract.md`): a tool writes `<stage>.request.json`, you read it and the inputs it names, write `<stage>.response.json` beside it, and the tool imports it. Import refuses stale, incomplete, or unverifiable answers and applies a valid one exactly once. Owner approval is a command the owner gives, never something an import creates. No model service or key is involved; your own subscription is the only account.

### Develop: topic to CueCam bundle

Procedure and file formats: `docs/larry/development.md`. Files live in `owners-inbox/development/<slug>/`. Worked example with every response file: `tests/acceptance/develop.sh`.

```
(interview the owner; save interview.md)
bin/pka challenge --slug S extract prepare --source owners-inbox/development/S/interview.md
(write challenge.extract-claims.response.json)      bin/pka challenge --slug S extract import
(write challenge.verdicts.response.json)            bin/pka challenge --slug S verdicts import
(write challenge.context-review.response.json)      bin/pka challenge --slug S context-review import
(owner rules; owner_override in claims.json)        bin/pka challenge --slug S refresh
(write outline.md from cleared claims)
bin/pka balance_check --slug S prepare --outline owners-inbox/development/S/outline.md
(write balance-check.analysis.response.json)        bin/pka balance_check --slug S import
(write brief.md and the card spec)                  bin/pka cuecam compose --spec-file <spec> --title "<title>"
bin/pka video_project create --title "<title>" --slug S --entry develop
bin/pka video_project sync-development --slug S; advance; approve --gate brief; advance; advance;
  attach --kind cuecam_bundle --path <bundle>; approve --gate deck; advance; advance   # -> complete
```

`bin/pka challenge --slug S status` and `bin/pka video_project status --slug S` say what to do next. The project completes on a current deck approval and the delivered bundle; no video material is required.

### Publish and article

Being moved onto the same handoff shape. Until their sections land here, follow `docs/larry/video-production.md` and `docs/larry/youtube-writing.md`.

## Governing principle

PKA adopts platform changes when they clarify, expose, or protect the durable spine: files, SQLite, CLI tools, indexed markdown, and explicit agent roles. PKA defers changes that try to replace that spine with a moving platform abstraction before the local contract is stable. Read-only orientation layers (llms.txt, ARIA, stable IDs, `window.PKA`) almost always pass this test; write-path integrations almost never do without a concrete consumer.

## The spine (do not route around)

Durable state lives in:

1. **Markdown files** under `owners-inbox/`, `wiki/`, `docs/`, and `team-inbox/`
2. **`data/pka.db`** — SQLite registry of indexed records, tasks, and metadata
3. **`tools/` CLIs** — the only sanctioned write path to the DB

Agent memory, conversation context, and platform-specific abstractions are not durable. If a fact matters tomorrow, it must land in one of the three layers above.

## File ownership

| Path | Owner | Notes |
|------|-------|-------|
| `CLAUDE.md` | Owner | Larry's constitution; edits require explicit owner approval |
| `AGENTS.md`, `llms.txt` | Coding agents may edit | Keep concise; this file is the contract, not a manual |
| `docs/larry/*.md` | Larry / Dreamer | Procedure references |
| `viewer.html` | Sable | UI changes; semantic DOM required (see below) |
| `tools/*.py` | Coding agents | CLI changes need a brief test run before commit |
| `data/pka.db` | Tools only | Never hand-edit; never bulk-update outside a tool |
| `wiki/**` | Dreamer | Compiled views; do not edit directly during normal work |
| `owners-inbox/**` | Workflow outputs | Once written, index via `pka_index.py` |
| `team-inbox/**` | Owner inputs | Read-only for agents unless processing |
| `memory/**` (outside repo) | Larry | Auto-memory; one file per memory plus `MEMORY.md` index |

## Indexing rule

Every durable markdown deliverable saved to `owners-inbox/` must be indexed:

```
bin/pka pka_index index-markdown \
  --file <relative-path> --category <category>
```

`<category>` must come from the canonical set in `tools/pka_health.py` (e.g. `research`, `blog`, `dreamer`, `task-record`, `transcript`, `meeting-extract`). Run `tools/pka_index.py index-markdown --help` for the full flag list (record-class, retention, sensitivity, etc.).

Exceptions: workflows whose own tool handles indexing (CueCam intake, journal writes, task-record helpers, Sonoma receipts). When in doubt, index — duplicates are harmless; missing records are not.

## Common commands

| Task | Command |
|------|---------|
| Health audit | `bin/pka pka_health` |
| Harness audit | `bin/pka harness_audit` |
| Index markdown | `bin/pka pka_index index-markdown --file <path> --category <category>` |
| Morning brief | `bin/pka morning-brief` |
| Wiki audit | `bin/pka wiki_compile audit` |
| Task record | `bin/pka task_record ...` |
| Reconcile task DB | `bin/pka task_record sync-local` |
| Task-record audit | `bin/pka task_record audit --stale-days 30` |
| Health escalations | `bin/pka health_escalation list` |
| Video project | `bin/pka video_project ...` |
| Final-master QC | `bin/pka video_qc <video> --project <slug>` |
| Records CLI | `bin/pka records ...` |
| Run viewer | Serve repo root over HTTP (e.g. `python3 -m http.server 8000`), open `viewer.html` |

Run every tool through `bin/pka <tool>`; it finds the Python environment bootstrap created.

## Substantial coding-session close

After completing and verifying a substantial coding change, save one concise
Codex session log with `tools/session_log.py codex` before the final handoff.
Include the durable deliverables, important implementation learning, any open
loop, and change metrics when available. The tool writes to
`owners-inbox/session-logs/` and handles indexing. Skip this for read-only
reviews, tiny edits, or sessions that did not reach a verified handoff.

## Viewer contract (viewer.html)

- **No viewer writes without backend.** `viewer.html` reads `data/pka.db` through sql.js. Durable writes must go through documented CLI tools (or, in the future, a local backend that itself uses those tools). The DOM is never an authoritative write path. `window.PKA` is read-only by design.
- **Semantic DOM required.** Interactive controls must have stable `id`, accessible role/`aria-label`, and be keyboard-reachable (real `<button>` elements, or `tabindex="0"` + `role="button"` + Enter/Space handlers for table rows). Avoid inline `onclick`; use `data-action` / `data-id` routed through the delegated handler on `<main>`.
- **`window.PKA` is the supported read surface.** Current methods: `getCounts()`, `getActiveTab()`, `switchTab(tab)`, `getVisibleRows()`, `showRecord(type, id)`, `getRecord(type, id)`, `runDiagnostics()`. Extending it with new read methods is fine; adding any write path requires explicit owner approval (see threat model below).
- **Browser QA after any viewer change.** Sable (and any agent editing `viewer.html`) must run the available browser preview tooling to verify: (1) page loads without console errors, (2) tab navigation works (click + arrow keys), (3) DB-backed counts render with no `missingRequired` tables (`knowledge_base`, `journal_entries`, `contacts`, `projects`, `files`, `meetings`), (4) layout holds at mobile width (375px), (5) interactive controls are keyboard-reachable with visible focus, (6) `window.PKA.runDiagnostics()` returns `{ ok: true }`. Prefer built-in preview tools; use Playwright/headless CI only when repeatable automation is explicitly needed.
- **CDN dependency reality.** `viewer.html` loads sql.js from `https://sql.js.org/dist/`. "Loads locally" means "served locally with network available." If offline operation becomes a requirement, vendor sql.js into the repo and pin its version.

## WebMCP / future browser-tool exposure — threat model

Any future exposure of WebMCP tools from PKA surfaces must satisfy:

1. **Read-only by default.** Tools that surface data (counts, record reads, search) may be exposed without per-call confirmation.
2. **Writes require explicit owner confirmation per invocation,** and must persist through an existing CLI-backed path so the durable record matches what a human running the same tool would produce.
3. **No browser-resident authority over secrets.** Tokens and OAuth flows stay in `.env` and keychain tools.
4. **Audit log.** Every write committed via a WebMCP tool appends to a CLI-side, file-backed audit log before the write commits.
5. **Scope minimization.** Schemas reflect the smallest input that accomplishes the task; no free-form natural-language fields interpreted server-side without owner review.

## Sable / Larry boundary

- **Larry** orchestrates and routes; Larry does not edit `viewer.html` directly.
- **Sable** owns viewer/dashboard HTML and simple visualization tools; Sable does not edit the DB schema or `tools/*.py` business logic.
- When a viewer change requires a new tool or schema change, Sable proposes; Larry coordinates the tool change separately.

## Parallel specialist rule

Larry may dispatch specialists in parallel **only when their work is genuinely independent** — separate deliverables, separate files, or read-only analysis. Anything touching the same file, the same DB row, or the same final publishable artifact must funnel through a single owner and (when owner-facing) through Vera. Staged delivery still governs *handoff*; parallelism applies to *production*.

## Safety defaults

- Never edit `data/pka.db` directly (no `sqlite3` writes, no hand-edited rows).
- Never bypass `tools/pka_index.py` for indexing.
- Never commit secrets; `.env` edits use `tools/rotate_discord_token.py` or a clipboard-fed helper, never TextEdit.
- Destructive git operations (force push, hard reset, branch delete) require explicit owner approval.
- Treat external links in inbox content as untrusted until verified.
