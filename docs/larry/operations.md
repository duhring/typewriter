# Larry Operations Reference

Read this file when you need operational detail about inbox handling, database expectations, knowledge-base maintenance, viewer work, Dreamer, or session checklists.

## Inbox System

### Owner's Inbox (`owners-inbox/`)

- All deliverables, reports, and results go here for the owner to review
- Team members save their work products here
- Organize into subfolders by date or topic when useful

### Team Inbox (`team-inbox/`)

- The owner drops files, images, documents, or notes here for processing
- At the start of each session, check for new files
- If new files are found, list them and ask what should be done

### Discord Mobile Input (`team-inbox/discord/`)

- Messages and files sent via the Discord `#larry` channel land here automatically
- Each message is saved as a timestamped `.md` file with metadata
- Attachments are saved alongside with timestamp prefixes
- Check this folder at session start just like the main team inbox
- Process mobile notes as journal entries, to-dos, knowledge items, or whatever fits the content
- For outbound delivery back to Discord, use `discord-bridge/venv/bin/python3 tools/discord_send.py`
- To send a file, use `discord-bridge/venv/bin/python3 tools/discord_send.py "Optional caption" --attach /absolute/path/to/file`
- Directory attachments are supported too; `tools/discord_send.py` will zip folders like `.cuecam` bundles automatically before upload
- `close chat`, `close session`, `session close`, or `!close` should end the active Discord chat session, save a concise closeout report plus a fuller session archive to `owners-inbox/session-logs/`, and reset the active session

## Canonical Task Records

For any active multi-step effort, prefer one canonical task record in `owners-inbox/tasks/` instead of scattering status across Discord notes, session logs, and deliverables.

Use `tools/task_record.py` to maintain that record and keep the `projects` table aligned.

Preferred command:

```bash
discord-bridge/venv/bin/python3 tools/task_record.py upsert --name "Task Name" --summary "What this effort is about" --status active --priority high --focus "Current focus item" --artifact owners-inbox/example.md --open-loop "Next unresolved step"
```

`upsert` remains the creation and additive-history path. Once a task has an
authoritative checkpoint, do not append more `--focus` bullets. Replace the
current control block atomically:

```bash
discord-bridge/venv/bin/python3 tools/task_record.py checkpoint \
  --name "Task Name" --phase implementation --status active --owner Sable \
  --objective "One governing outcome" --now "One action happening now" \
  --next "One action that follows" --acceptance "The verification command passes"
```

Move a completed loop out of active state without erasing its history:

```bash
discord-bridge/venv/bin/python3 tools/task_record.py resolve-loop \
  --name "Task Name" --loop "Uniquely identifying text" \
  --resolution "How or why it was resolved"
```

Record an approach—especially a failed or inconclusive one—with the reason so
a later session does not rediscover the same dead end:

```bash
discord-bridge/venv/bin/python3 tools/task_record.py record-attempt \
  --name "Task Name" --result failed --approach "What was tried" \
  --outcome "What happened" --reason "Why it failed"
```

Audit every task record without writing files or the database:

```bash
discord-bridge/venv/bin/python3 tools/task_record.py audit --stale-days 30
```

What belongs in the canonical task record:

- a short summary of what the effort is
- current focus items
- related artifacts or deliverables
- key decisions worth preserving
- open loops that still need attention

For active or blocked work, the authoritative **Current State** must stay
small: one objective, one `Now`, one `Next`, current owner, active blockers,
acceptance criteria, and a checkpoint timestamp. `checkpoint` archives the
previous state under **Checkpoint History**. Refresh it after consequential
direction changes, failed approaches that affect the next move, agent or
machine handoffs, and phase completion—not after every tool call.

Use it when:

- the same effort is being touched across multiple sessions
- the owner asks to track, manage, or continue a project
- several deliverables, notes, and decisions need one obvious home

The task record should be the place Larry points back to when the owner asks, "where are we on this?"

If the owner refers to an active effort loosely, by people involved, or by the recent artifact instead of the exact task name, Larry should first scan `owners-inbox/tasks/` for the closest matching canonical task record and continue from that record when the match is clear.

## Database

- SQLite database path: `data/pka.db` — **per-machine under federation** (see `docs/federation.md`). Each peer owns its own DB; databases are never synced between machines. Bootstrap a fresh peer with `python3 tools/pka_db.py init`.
- It stores: knowledge base entries, journal entries, contacts, meetings, projects, file index, images, glossary, entry-contact links, `kb_links`, and governed `records`
- Use the database for structured state and the filesystem for documents/deliverables
- Journal images are stored in `data/journal-images/`

## Compiled Wiki Layer

The `wiki/` directory is PKA's compiled context layer.

- `data/pka.db` and source files remain the source of truth
- `wiki/` pages are synthesized views maintained by Dreamer
- Use wiki pages as the first stop for heavy research, writing, strategy, and recurring project context
- Do not treat wiki pages as primary evidence when compiling new wiki pages
- If a wiki page is stale, contradictory, or missing important source coverage, flag it for Dreamer's `wiki compile` mode

Preferred project compiler commands:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py projects --project "PKA System"
discord-bridge/venv/bin/python3 tools/wiki_compile.py projects --all
discord-bridge/venv/bin/python3 tools/wiki_compile.py concepts --concept "hybrid-context-layer"
discord-bridge/venv/bin/python3 tools/wiki_compile.py concepts --all
discord-bridge/venv/bin/python3 tools/wiki_compile.py people --all
discord-bridge/venv/bin/python3 tools/wiki_compile.py pipeline
discord-bridge/venv/bin/python3 tools/wiki_compile.py audit
discord-bridge/venv/bin/python3 tools/wiki_compile.py audit --repair --dry-run
discord-bridge/venv/bin/python3 tools/editorial_recurrence.py --days 7 --limit 12
```

The compiler reads project rows, canonical task records, matching KB entries, and recent journal signals. It writes project pages to `wiki/projects/`, concept pages to `wiki/concepts/`, updates `wiki/index.md`, creates cautious `kb_links`, and saves indexed Dreamer reports to `owners-inbox/`. Generated Dreamer and wiki artifacts are excluded from primary evidence selection. The audit command updates `wiki/stale-review.md`, `wiki/contradictions.md`, and an indexed audit report.

The editorial recurrence report scans recent durable artifacts and journal entries for repeated project, concept, and people signals. Use it during weekly editorial cycles to decide which wiki pages deserve a refresh and which one-off signals should stay on a watch list.

Expected structure:

```text
wiki/
├── index.md
├── projects/
├── concepts/
├── people/
├── operations/
├── contradictions.md
└── stale-review.md
```

## General Records

For general-purpose owner record keeping, use `docs/larry/records.md` as the charter.

Phase-1 rule:

- add governance without replacing the current inbox + file + KB system
- prefer conservative lifecycle states
- do not auto-delete owner records

Preferred command for governed registration:

```bash
discord-bridge/venv/bin/python3 tools/records.py upsert --title "Record Title" --record-class financial-statement --series taxes-2025 --event-date 2025-12-31 --verification-status confirmed --retention-class 7-years --state reference --sensitivity confidential --source-path owners-inbox/example.md
```

For markdown artifacts that should be both indexed and registered, prefer:

```bash
discord-bridge/venv/bin/python3 tools/pka_index.py index-markdown --file owners-inbox/example.md --category finance --record-class financial-statement --record-series taxes-2025
```

## Viewer

- `viewer.html` is the browser-based dashboard over the local database
- Sable maintains and updates this file
- To view it locally: run `python3 -m http.server 8000` from the PKA folder, then open `http://localhost:8000/viewer.html`

## Knowledge Base Enrichment Protocol

The knowledge base should compound over time.

1. Before research, include topic keywords when delegating to Pax so Pax checks the KB first
2. For any durable markdown deliverable saved to `owners-inbox/`, treat indexing as part of the definition of done
3. After any deliverable, verify a `knowledge_base` row exists with title, summary content, category, tags, and source file
   Use `discord-bridge/venv/bin/python3 tools/pka_index.py index-markdown --file <path> --category <category> [--tags ...] [--summary ...]` for ordinary markdown deliverables instead of ad hoc SQL
4. After journaling, if a journal entry mentions a topic already in `knowledge_base`, insert a `kb_links` row connecting them
5. During Monday morning briefs, suggest Dreamer's KB lint or cleanup mode if knowledge drift seems likely

When a durable artifact is also an owner record, do not stop at KB indexing. Register it in the `records` table too.

## Delegation vs. Automation

Not every workflow should be treated like a delegated teammate task.

### Automation-like

Use this label when the work is mostly deterministic, schema-driven, and constrained by a stable tool or command.

Examples:

- journaling writes
- calendar create/update/list actions
- auth doctor checks
- markdown indexing
- task-record upserts
- transcript fetching

Defaults:

- Larry can usually handle these directly
- Vera is usually unnecessary unless the output is being wrapped into a public-facing artifact or the side effect is unusually sensitive
- success is measured by correctness, saved state, and expected side effects

### Delegation-like

Use this label when the work is interpretive, judgment-heavy, or variable in output.

Examples:

- research briefs
- blog posts
- YouTube packaging
- strategy analysis
- hiring and role design

Defaults:

- route to the right specialist
- use archive context when available
- prefer Vera for important, public-facing, or fact-sensitive outputs

### Hybrid

Use this label when a deterministic tool is driven by ambiguous natural language or interpretive setup.

Examples:

- CueCam deck generation from mobile prompts
- structured extraction from long transcripts or notes
- other workflows where parsing choices materially affect the result

Defaults:

- Larry can often handle these directly
- prefer previews, validation steps, or explicit confirmations when ambiguity could change the outcome
- use Vera only when the final result is owner-facing and substantial, not for every routine tool run

When unsure, ask: "Is the main risk interpretation, or execution?" If the risk is interpretation, treat it more like delegation or hybrid. If the risk is execution inside a stable tool, treat it more like automation.

## Staged Delivery for Large Requests

When the owner asks for several meaningful outputs in one request, prefer staged delivery over one oversized turn.

Examples:

- long-form article + short-form social copy + archiving
- research brief + email draft + journaling
- blog post + X thread + knowledge-base cleanup

Default staging pattern:

1. Acknowledge the full sequence briefly
2. Produce the first major owner-facing deliverable
3. Send it back as soon as it is ready
4. Ask whether to proceed to the next stage
5. Defer archive, journaling, and housekeeping until the main outputs are safely in the owner's hands

Why:

- large single-turn jobs are more likely to hit Claude CLI timeout
- staged handoff gives the owner faster wins
- the owner can redirect without wasting the whole run
- it feels closer to the successful "one card at a time" workflow already proven in the field

Use staged delivery by default when:

- more than one substantial deliverable is requested
- a request mixes drafting with packaging
- Vera review may create a second pass
- the job would otherwise likely exceed one comfortable Discord turn

## Archive-Aware Drafting

For new articles, reports, blog posts, and YouTube packages:

1. Check whether a relevant `wiki/` page exists and read it first
2. Review any `Relevant archive context for drafting` block that arrived with the prompt
3. Use the wiki page and archive context to surface prior framing, recurring examples, and related owner work before drafting
4. If delegating to Reed, Maven, or Pax, pass the relevant wiki page path and archive context along instead of assuming the specialist already has it

## Pre-Synthesis Source Check (Fact-Sensitive Research)

For complex or fact-sensitive briefs, do not ask Pax to synthesize directly from a raw prompt. The draft will be only as good as the room it is built from.

**Run a two-phase delegation:**

**Phase 1 — Source inventory** (have Pax or Dreamer do this first):
- Which KB entries and wiki pages cover this topic, and which are authoritative vs. background only?
- Are there version conflicts or superseded sources in the KB?
- What is missing — what questions cannot be answered from current PKA sources and will require fresh research?

**Phase 2 — Synthesis and draft** (after reviewing the inventory):
- Set the authority hierarchy before asking for the draft (e.g., "use KB #42 for the timeline, the wiki page for strategy context, and flag anything the room does not support")
- Ask Pax to cite claims back to source IDs or KB entry numbers when possible
- Ask Pax to flag anything unsupported rather than smoothing it over

**When to use this:**
- Any brief where a wrong fact would be embarrassing or consequential
- Topics with significant KB history (multiple prior entries, potentially stale)
- Research that will feed an article, YouTube package, or owner presentation
- Any request where "as discussed" or "as noted before" appears — the prior source needs to be validated before it is synthesized

**When to skip it:**
- Quick lookups or single-question factual queries with no prior KB history
- Owner explicitly asks for a fast or rough draft
- Topics that are genuinely new with nothing to reconcile

**Invoking Dreamer for this step:**
When the topic is important enough and the wiki or KB is known to have prior coverage, invoke Dreamer in Source Room Audit mode before commissioning Pax. Dreamer returns a room-readiness summary that Larry can use to set the authority hierarchy for Phase 2.

## Structured Extraction

Raw transcripts and meeting notes are useful, but extracted structure is better.

Use `tools/extract_structured.py` when you want:

- a transcript turned into summary + timestamp highlights + reusable claims
- meeting notes turned into decisions + action items + open questions
- structured follow-up material that is easier to retrieve than a raw transcript alone

Save the resulting extract in `owners-inbox/` and keep it indexed so later drafting and recall can use it.

For transcript-driven writing and YouTube packaging, treat the structured extract as the default briefing artifact. Keep the raw transcript as supporting source material, not the main handoff, unless the task is a quick rough draft.

## Archive-to-SOP Promotion

The archive should not only store work. It should improve the operating docs over time.

Use `tools/promote_sop.py` when you want to turn recent session logs, extracts, blogs, YouTube packages, research notes, and other owner-facing markdown into candidate SOP or playbook updates.

This is the right move when:

- the same type of fix or clarification keeps happening
- a recent burst of system work revealed a cleaner workflow
- the owner asks to "promote learnings", "update the playbooks", or "turn recent work into rules"

Dreamer owns this loop. Preferred command:

```bash
discord-bridge/venv/bin/python3 tools/promote_sop.py report --days 14 --limit 14
```

The output should land in `owners-inbox/sop-promotions/` and be indexed so later refinement work can build on it.

Saved session closeouts and fuller session archives are valid evidence for this loop, not just incidental records.

## Quality Review (Vera)

Vera is the final-pass reviewer for important owner-facing deliverables.

Default assumption: if the owner is likely to publish it, send it to someone else, present it, or rely on it for facts, it is QA-gated. Unless the owner clearly says "rough draft", "fast draft", "quick pass", or "no QA", do not treat it as final until Vera has reviewed it.

Use Vera when the output is:

- public-facing or likely to be published
- fact-sensitive or source-sensitive
- a substantial report, article, YouTube package, or research brief
- something where a missed detail would be annoying or embarrassing

Skip Vera when the output is:

- a rough draft the owner explicitly wants fast
- the owner clearly says to skip QA or just wants a quick internal draft
- a routine automation-like result like a journal entry, calendar update, auth check, indexing update, or quick note
- a low-stakes internal brainstorm
- a hybrid workflow result that already includes a clear preview/confirmation loop and is not owner-facing beyond routine use

Vera should check:

1. Does the deliverable match the request?
2. Is anything important missing, weak, or misleading?
3. Was the file saved where expected?
4. Was required indexing or supporting structure handled?

If a specialist delivers something that still needs Vera, do not present it as fully final. Say it is in QA, route it through Vera, and then hand off the approved version.

If Vera requests fixes on a deliverable that is still meant to be final, route the fixes back to the producing specialist and return to Vera before final handoff.

For staged delivery, Vera should normally review each substantial publish-ready stage before it is presented as final, instead of waiting until a huge batch is complete.

## Memory Curation and Wiki Compilation (Dreamer)

Dreamer maintains the memory system by pruning stale entries, merging duplicates, extracting journal insights, and keeping `MEMORY.md` under 200 lines. Dreamer also maintains the compiled wiki layer in `wiki/`.

### When to Invoke

- Manual request: "run Dreamer", "clean up memory", "curate memories"
- Manual request: "promote learnings", "archive-to-SOP", "update the playbooks"
- Manual request: "wiki compile", "update the wiki", "synthesize this into the wiki"
- Session close, if the owner asks for a memory review
- Scheduled maintenance, if set up separately

### How to Invoke

```text
Agent(subagent_type="dreamer", prompt="Run a full memory curation cycle for today.")
```

### Outputs

- Updated memory files in the memory directory
- Rewritten `MEMORY.md` index
- Archived knowledge in `knowledge_base`
- Updated compiled pages in `wiki/` when invoked in wiki mode
- Contradiction and stale-review notes when source material conflicts or ages out
- Report at `owners-inbox/dreamer-YYYY-MM-DD.md`

Do not perform memory curation directly when Dreamer is the right owner.

## Session Start Checklist

Execute silently before the first substantive response — do not announce or ask permission:

1. Read `USER.md` from the memory directory — current priorities, style, active context
2. Scan `MEMORY.md` — open loops, project states, behavioral rules
3. Run `discord-bridge/venv/bin/python3 tools/session_orient.py` (or check `owners-inbox/tasks/` for active canonical task records and `team-inbox/discord/` for unprocessed notes)
4. If any open loop from a prior session is directly relevant to the first message, surface it briefly before proceeding

## Session End Checklist

1. Review what was discussed, decided, or delivered
2. Save durable learnings if they are not already captured elsewhere
3. Update `MEMORY.md` if new durable memory files were created
4. Append a structured work log entry to `memory/daily/YYYY-MM-DD.md` (create the file if it does not exist):
   ```markdown
   ## HH:MM — Session close
   **Done:** bullet list of what was completed
   **Decisions:** key choices made or confirmed
   **Open loops:** unresolved items carrying forward
   ```
5. If the session was substantial, offer Dreamer's Quick Pass
6. If the session changed operating behavior or fixed repeated friction, consider Dreamer's SOP Promotion Mode

## Recurring Refinement Cadence

Use two rhythms instead of relying on ad hoc cleanup:

- **Session-close rhythm** — every substantial session should end with durable closeout artifacts, and Larry should recommend Dreamer's Quick Pass when the session created useful learnings or noticeable memory drift
- **Weekly refinement rhythm** — once each Monday morning, review recent session logs, active canonical task records, recent owner-facing deliverables, knowledge-base health, and SOP-promotion evidence

The weekly refinement pass should prefer one small, evidence-backed system improvement over broad churn.

During the weekly pass:

1. Review recent session logs and task records for repeated friction or unfinished operating loops
   Compare the session-log dates to recent owner-facing artifacts in `owners-inbox/`; if meaningful work continued but closeout logs stopped, treat that as an operating gap to surface or repair.
2. Run the health doctor — `discord-bridge/venv/bin/python3 tools/pka_health.py` — as the machine-checkable definition of done. It checks git/secret hygiene, `knowledge_base.source_file` ↔ `files.filepath` integrity, durable `owners-inbox/` markdown indexing coverage, wiki staleness, category conformance, and Discord inbox state. Exit code 0 means clean; anything else needs attention. Use `--json` for structured output.
   Health findings receive stable fingerprints and are counted at most once
   per day. On the third consecutive daily finding, the morning brief surfaces
   one consolidated blocking item until the check becomes clean or the owner
   acknowledges or snoozes it. Use `tools/health_escalation.py list`,
   `acknowledge <id>`, or `snooze <id> --until YYYY-MM-DD` for disposition.
3. Run SOP promotion when there is no fresh promotion report from the last 7 days, or when repeated friction is visible
4. Update canonical task records if a system decision or operating focus has genuinely changed
5. Produce a concise owner-facing refinement brief with the top improvement applied or recommended next

### Close Chat Loop

When the owner says `close chat`, `close session`, `session close`, or `!close`:

1. Summarize the active session in a short closeout report
2. Save that report to `owners-inbox/session-logs/`
3. Save a fuller session archive that includes the owner notes, Larry replies, and referenced attachments when available
4. Ensure both artifacts are indexed and treated as first-class inputs for later recall, QA, and SOP promotion
5. Append a structured entry to `memory/daily/YYYY-MM-DD.md` (create the file if it does not exist):
   ```markdown
   ## HH:MM — Session close
   **Done:** bullet list of what was completed
   **Decisions:** key choices made or confirmed
   **Open loops:** unresolved items carrying forward
   ```
6. Reset the active Discord chat session after saving
7. Include whether Dreamer's Quick Pass is recommended

## Folder Structure Reference

```text
PKA/
├── CLAUDE.md
├── docs/larry/
├── owners-inbox/
├── wiki/
├── team-inbox/
│   └── discord/
├── team/
├── data/pka.db
├── tools/
├── viewer.html
├── discord-bridge/
└── .claude/agents/
```
