# Plan: assistant-neutral starter with three entry points

Date: 2026-09-18
Status: proposed, reviewer feedback of 2026-09-18 folded in
Applies to: `~/Code/pka-starter` (the mini will be upgraded to the same system; no compatibility path is kept for it)
Shared copy: https://claude.ai/artifact/W59YJDy776HMVfudcDnhyz

## Contract

> The owner's chosen assistant performs interviews, analysis, drafting, and editorial review. PKA prepares source material, validates submitted results, preserves provenance, builds artifacts, and records the owner's approvals. No separate model service is required for any workflow.

"Chosen assistant" means any AI assistant that can read files and run commands in the repo. A browser-only chatbot can supply the thinking, but the owner then saves files and runs the commands by hand.

## Three entry points

A project is created with one entry point. Each runs on its own from a fresh install. A project that starts at `develop` may continue into `publish` and `article` later.

| Entry | Starts with | States it runs | Ends with |
|---|---|---|---|
| `develop` | a topic | developing through deck-approved | indexed CueCam bundle, deck approved |
| `publish` | an edited master | master-qc through published | released video with a recorded publication URL, after the owner's explicit release |
| `article` | a master, a transcript, or a raw intake | editorial states only | reconciled final article with approval bound to its hash |

## Division of labor

| Stage | Assistant supplies | PKA retains |
|---|---|---|
| Interview | Questions and faithful interview record | Saved sources and project registration |
| Challenge | Claims, proposed verdicts, per-claim context reviews | Quote verification, evidence retrieval, validation, run history, owner overrides |
| Outline and brief | Structure, talking points, packaging intent | Artifact tracking and approval hashes |
| Balance check | Analysis of supplied archive matches | Lexical matching and report generation |
| CueCam | Explicit card spec | Parsing, preview, bundle generation, indexing |
| Transcript extract | Summary, supported claims, highlights | Transcript fetch or local Whisper, source references, validation, indexing |
| YouTube package | Titles, description, chapter labels, separate chapter review | Timestamp checks, final-master identity, package approvals, upload |
| Article | Reflective interview, thesis, brief, draft, reconciliation, final review | Separate versions, evidence links, indexing, approval records |

## The handoff pattern

Every stage that used to call a model follows one shape:

1. **prepare** — the tool writes a request file.
2. **assistant response file** — the assistant reads the request and writes a response next to it.
3. **validate and import** — the tool checks the response against the request and applies it. Rejected responses go back to step 2.

The request file carries the source material, the windows or batches, the IDs the response must address, a request ID, and a hash of every input it was built from. Each response carries `schema_version: 1`, the request ID, the input hashes, and the claim, chapter, or card IDs it answers. A transcript hash alone is not enough: a chapter review names the exact package it reviewed, and a context review names the claims, evidence, and proposed verdicts it saw.

Validation rejects stale input hashes, unknown request IDs, missing reviews, duplicate IDs, quotes not found in the source, and responses aimed at material that has since changed. Importing the same response twice is a no-op: no duplicate artifacts, no duplicate approvals. Failed attempts and the owner's decisions are preserved in the run record, as the challenge tool already does.

Owner approval is always a separate action. Importing an assistant response never creates an owner approval. Idempotent reimport preserves approvals that already exist; it does not grant them.

Where a stage depends on an earlier one, the handoffs run in sequence inside one recorded run. The challenge gate is the clearest case: extraction, then Python verifies quotes and retrieves evidence, then the assistant challenges that evidence, then context review follows the proposed objections. That is three prepare and import steps, not one response file. The YouTube package is the second case: the package is generated and imported first, bound to the transcript and its other source inputs, and only then is the chapter-review request prepared using the imported package's hash.

## Work items

### 1. Remove model calls from the core tools

| Tool | Today | Change |
|---|---|---|
| `tools/challenge.py` | three model calls: extract, verdicts, context review | Three sequential prepare and import stages in one run: `extract-claims`, `challenge` (after quote verification and evidence retrieval), `context-review` (after proposed verdicts). Quote verification, spans, register, report, overrides unchanged. |
| `tools/balance_check.py` | lexical scan then model analysis | `prepare` writes the overlap list; `import --analysis` accepts the assistant's analysis and renders the report. The lexical scan already runs without a provider. |
| `tools/cuecam.py` | `--raw-prose` normalizes dictation through a model | Remove `--raw-prose`. The assistant writes the spec; `compose --spec-file` is the only input. Move the Google imports inside the functions that use them so local bundle creation runs with no Google packages installed. |
| `tools/fetch-transcript.py` | fetches, then runs structured extraction through a model | Fetch only. Extraction moves to `tools/extract_structured.py prepare` and `import`, taking an assistant-authored extract. |
| `tools/pipeline.py` | model writes the package, model reviews chapters, and generates a thumbnail through the OpenAI Images API unless `--no-thumbnail` | Two sequential handoffs: `package` prepare and import bound to the transcript and source inputs, then `chapter-review` prepare and import bound to the imported package's hash. Supplied artwork is the thumbnail input. Timestamp and boundary checks unchanged. |
| `tools/generate_thumbnail.py` | OpenAI Images API, key required | Dropped from the first starter release. Can return later behind a flag. |
| `tools/memory_retrieval.py` | embeddings from LM Studio | Keyword retrieval is the default. Verify whether the index already has a text-search table; add one if not. Embedding support is removed from the starter. Must land before the provider modules are deleted, since this file imports `lmstudio` at module level. |
| `tools/llm.py`, `lmstudio.py`, `xai.py`, `glm.py`, `rotate_glm_key.py` | provider router and clients | Delete from the starter. Remove `LLM_PROVIDER` and the ollama block from `config/machine.json`, the ollama check from `bootstrap.sh`, and the provider row from the README. Retire `docs/larry/llm-providers.md`. |
| `tools/promote_sop.py` | LM Studio for repair and promotion | Dropped from the first starter release. Can return later with the prepare and import shape. |

### 2. Entry points in the project tracker

All in `tools/video_project.py`:

- Add an `entry` field, set at creation: `develop`, `publish`, or `article`.
- `workflow_sequence` slices the state list by entry instead of always starting at `developing`. Projects without an entry keep the full sequence.
- Allow `article` alone as a requested output. Today only `video` and `video` plus `article` are legal.
- Editorial artifacts accept an interview as an alternative source to a transcript, so a raw intake can start an article.
- The YouTube package no longer requires a brief when the project has none.
- Add a `blog_final` approval gate tied to the exact final file's hash. Today the blog gate covers only the draft, and the docs say so. The gate applies the same way to standalone and video-derived articles.
- Define completion per entry. Today `validate_completion` demands a final master, transcript, package, and thumbnail, so a develop-only or article-only project can never complete. `develop` completes at deck approval with an indexed bundle. `article` completes at final-article approval, with no YouTube or published-blog record required. `publish` completes at a recorded publication URL after the owner's release.
- Define continuation. A completed `develop` project can be reopened into `publish` by attaching a final master; its development artifacts and approvals stay in place. A `publish` project can be continued into `article` the same way.
- Every transition and completion check gets a test per entry. These do not carry across unchanged.

Approval hashing, run history, and owner overrides attach to artifacts, not to positions in the sequence, so those carry across without change.

### 3. Assistant-neutral instructions

- `AGENTS.md` becomes the primary workflow document, one section per entry point, each written as steps any assistant can follow: which command prepares, what to write in the response file, which command imports, which approval the owner gives.
- `docs/larry/development.md`, `video-production.md`, and `cuecam.md` become the detailed references those sections point to, with the provider commands replaced by prepare and import commands.
- `CLAUDE.md` and `.claude/agents` and `.claude/skills` remain as Claude conveniences that point at `AGENTS.md`. Nothing essential lives only there.
- Add a neutral launcher (`bin/pka`) so commands are not invoked through the Discord bridge's virtual environment. Bootstrap creates the environment; the launcher hides its location.
- Instructions are updated alongside each working path, not at the end.

### 4. Setup

The owner's setup becomes: clone, run bootstrap, connect the assistant, install CueCam, pick an entry point. Installation stays proportional to the entry chosen:

- Bootstrap installs the Python requirements. Whisper stays behind `--with-transcribe`, since interviews and supplied transcripts never need it.
- CueCam is required only for presenting and recording.
- Node is required only for HyperFrames renders, which none of the three core paths use.
- Bootstrap reports each missing optional tool as a warning naming the path that needs it, and fails clearly on the things every path needs. It no longer looks for ollama or LM Studio.

The assistant's own subscription or authentication applies; PKA needs no model keys.

Bootstrap also checks the Python it selects. Today `bootstrap.sh` takes the first `python3.13` on the path with no architecture check and silently reuses an existing virtual environment. An observed `--skip-brew` run selected an Intel Python at `/usr/local/bin/python3.13` on an Apple Silicon Mac whose developer tools were ARM-only, and the `cryptography` build failed. That was environment-specific, not proof that every Apple Silicon install fails, but the check is cheap. Before creating or reusing the environment, bootstrap reports the selected Python's path, version, and architecture, detects incompatible native and Rosetta toolchain combinations, and stops with actionable instructions before installing anything. An existing environment is validated the same way; rerunning bootstrap never silently reuses an incompatible one.

## Capability boundaries

These stay outside the contract and are documented as such:

- **Transcription** runs Whisper in-process for local files, or fetches YouTube captions. No model service is needed.
- **Thumbnails and other artwork** are supplied by the owner or produced through the chosen assistant's own image capability. PKA never calls an image provider on a core path.
- **YouTube publishing** needs Google authorization for the upload and release. This applies only to the `publish` entry and is platform access, not a model service.

## Order of work

1. Shared request and response contract, validation library, and launcher. Every later path builds on one tested handoff.
2. `develop` path: challenge in three stages, balance check, CueCam, tracker entry field and develop completion.
3. `article` path: transcript decoupling, extract prepare and import, article-only output, interview as source, `blog_final` gate, article completion.
4. `publish` path: package and chapter-review handoffs, supplied artwork, publish completion with release and URL.
5. Complete provider removal, config and bootstrap cleanup, clean-install verification of all three.

## Acceptance

Fresh-install runs on a machine with no provider keys and no model server:

- [ ] `develop`: an interview, a three-stage challenge with owner rulings, a balance check, an indexed CueCam bundle, and a completed project.
- [ ] `publish`: an edited master through package approval, chapter review, private upload, QA, release approval, the owner's explicit release, and a recorded publication URL.
- [ ] `article` from raw intake: reflective interview, thesis, draft, owner edit, reconciled final, and final approval bound to the final file's hash.
- [ ] `article` from transcript: the same loop starting from a supplied transcript, tested separately.

Each run leaves source checks and approval records intact and readable through the project tracker.

Successful runs are not enough. The contract is proven by what imports refuse:

- [ ] A response with a stale input hash is rejected.
- [ ] A challenge run missing its context-review stage cannot be applied.
- [ ] Duplicate claim, chapter, or card IDs are rejected.
- [ ] A chapter review aimed at a package that has since changed is rejected.
- [ ] Final-article approval goes stale when the final file changes.
- [ ] Reimporting the same response creates no duplicate artifacts or approvals, and grants none.
- [ ] Importing any assistant response leaves every approval gate exactly as it was.
- [ ] Bootstrap reports the selected Python's path, version, and architecture, and refuses an incompatible toolchain or environment before installing dependencies.

## Open decisions

- Whether the memory index already has a text-search table, which decides if keyword retrieval is free.

Resolved on review: `promote_sop.py` and `generate_thumbnail.py` are dropped from the first starter release and may return later. The architecture check is the bootstrap Python selection described under Setup and belongs to T3.

## Tickets

One ticket per row, in dependency order. Phase numbers match the order of work.

| ID | Ticket | Depends on | Phase |
|---|---|---|---|
| T1 | Handoff contract: request and response file formats, request IDs, per-stage input hashes, `schema_version: 1`, shared validation library, idempotent import that never touches approvals. Done 2026-09-18: `tools/handoff.py`, `tests/test_handoff.py`, `docs/handoff-contract.md` | | 1 |
| T2 | Negative tests for the contract: stale hash, unknown request, duplicate IDs, changed material, double import, approvals untouched by import. Done 2026-09-18: added `depends_on` and `missing_stage` to the contract, failed applies are not recorded, and gate approvals now go stale on in-place file edits (was re-attach only) | T1 | 1 |
| T3 | Neutral launcher and proportional bootstrap: report selected Python path, version, and architecture; refuse incompatible toolchains and existing environments before installing; clear failure reporting. Done 2026-09-18: `bin/pka`, `tools/env_check.py` (parses the toolchain's Mach-O architectures), `bootstrap.sh` validates an existing venv and never deletes one, optional tools reported by the path that needs them, ollama check removed | | 1 |
| T4 | Challenge gate as three sequential handoffs in one recorded run. Done 2026-09-18: `tools/challenge.py` rewritten around `extract`, `verdicts`, and `context-review` prepare/import; all 16 deterministic functions byte-identical to before; a claim without verified context is held individually instead of failing the whole run; `docs/larry/development.md` updated | T1 | 2 |
| T5 | Balance check prepare and import. Done 2026-09-18: one handoff; the lexical report is written at prepare with the analysis pending; contradiction quotes are now verified verbatim on import (previously a manual instruction in the report footer); `--lexical-only` replaces `--no-llm` | T1 | 2 |
| T6 | CueCam: remove `--raw-prose`, lazy Google imports. Done 2026-09-18: the prose normalizer and its model call are gone; `compose --spec-file` is required; Google and `requests` imports moved into the functions that use them, with a plain `google_packages_missing` error when they are absent; verified importing and previewing on a Python with neither package | | 2 |
| T7 | Tracker: `entry` field, sequence slicing, develop completion and continuation, tests. Done 2026-09-18: `create --entry develop\|publish\|article`, `entries` list on the project, sequence sliced from the first entry's first state to the last entry's last state, per-entry completion (develop: current deck approval plus unchanged bundle), `continue --into` reopens a completed project at the next entry with everything kept; legacy projects untouched. Also fixed: the disk-hash cache from T2 missed edits inside a bundle directory | | 2 |
| T8 | `develop` clean-install acceptance run, with instructions for the develop path. Done 2026-09-18: `tests/acceptance/develop.sh` plays the assistant with canned response files and runs interview to completed project; passed on a fresh clone after bootstrap with every model variable unset (4 requests, 4 imports, brief and deck the only approvals, no video material). `AGENTS.md` rewritten for any assistant with the develop path, within the harness word budget | T2 to T7 | 2 |
| T9 | Transcript fetch decoupled from extraction; extract prepare and import. Done 2026-09-18: `fetch-transcript` only fetches and names the next command; `extract_structured transcript\|meeting prepare\|import` with one item per extract; every cited timestamp is checked against the transcript (was a prompt instruction); 15 deterministic functions byte-identical; `docs/larry/youtube-writing.md` updated | T1 | 3 |
| T10 | Tracker: article-only output, interview as source, `blog_final` gate, article completion, tests. Done 2026-09-18: `blog_final` gate bound to the exact final file (stale on edit), new `blog-final-approved` state, article entry completes on that gate and delivers `blog_final`; interview accepted as the source for a standalone article; article-only output legal; `--outputs` defaults by entry; `docs/larry/video-production.md` updated | T7 | 3 |
| T11 | `article` acceptance runs, raw intake and transcript separately, with instructions for the article path. Done 2026-09-18: `tests/acceptance/article.sh intake\|transcript` runs source to completed project including the extract handoff (transcript mode), the two blog gates, and the final approval refusing an edited file; both modes passed on a fresh clone with every model variable unset. `AGENTS.md` article section added within the word budget | T9, T10 | 3 |
| T12 | Pipeline: two sequential handoffs, package bound to transcript and source inputs, then chapter review bound to the imported package's hash. Done 2026-09-18: `pipeline package prepare\|import` and `chapter-review import` in the project folder; chapter boundaries checked mechanically before the package is attached; review depends on the package import and is stale if the package file changes; project enters package-review only when every chapter is supported; one-shot path removed; the auto-thumbnail call removed from the flow (T13 adds supplied artwork) | T1 | 4 |
| T13 | Supplied artwork as the thumbnail input; remove API generation from the pipeline. Done 2026-09-18: `tools/generate_thumbnail.py` deleted; the `thumbnail` subcommand removed from the publish tool; OpenAI key removed from the env example; README keys row says no model keys; tests prove the artwork is attached, selected, and bound to the package approval (replacing it stales the approval) and that a reference without rights blocks approval | | 4 |
| T14 | Tracker: publish completion with owner release and recorded URL, tests. Done 2026-09-18: completion requires the delivered materials, a recorded public URL, and in automated-private mode a release approval that is still current (a re-encoded master stales it); owner mode skips PKA's private-upload, QA, and release states, and the owner's recorded URL is the release; the brief is optional for the package; `docs/larry/video-production.md` section 6 updated | T7 | 4 |
| T15 | `publish` clean-install acceptance run, with instructions for the publish path. Done 2026-09-18: `tests/acceptance/publish.sh owner\|automated` runs a generated master through QC, both handoffs, supplied artwork, package approval, release, and the recorded URL to completion; automated mode proves the release approval is bound to the master; both modes passed on a fresh clone with every model variable unset. The YouTube upload itself (Google auth) is the one step not performed. `package prepare --transcript` added. AGENTS.md holds the entry table within budget; the command sequences live in `docs/assistant-workflows.md` | T12 to T14 | 4 |
| T16 | Provider removal: delete router and clients, clean config, bootstrap, README, retire provider doc; remove `promote_sop` and `generate_thumbnail` | T8, T11, T15, T17 | 5 |
| T17 | Keyword retrieval default; text-search table if missing; drop the `lmstudio` import from memory retrieval | | 5 |
| T18 | `AGENTS.md` as the primary workflow document; Claude files point at it | T8, T11, T15 | 5 |
| T19 | Clean-install verification of all three entries on a machine with no keys and no model server | T16 to T18 | 5 |

First milestone reached 2026-09-18: T1 to T8 done and committed; the develop path runs end to end on a fresh install with no model service or keys. Phases 3 and 4 (article and publish paths, T9 to T15) done 2026-09-18. Next: phase 5, provider removal, keyword retrieval, AGENTS.md as the primary document, and the clean-install verification of all three entries (T16 to T19).

## Back-port

Path and configuration changes made here are worth back-porting to the working repo when the mini is upgraded, in line with the starter's existing practice of re-filtering from PKA rather than diverging.
