# Federation — Multi-Machine Operating Agreement

**Status:** active · **Adopted:** 2026-07-26 · **Supersedes:** `docs/archive/devendorf-contract-retired-2026-07-26.md`

PKA runs as a **federation of standalone peers**. Each machine owns its own complete `data/pka.db`, writes to it freely, and shares only files with the other peers. There is no primary and no satellite; there is no single-writer rule.

## 1. Principle

Every PKA machine is a self-sufficient peer:

- It has its **own** `data/pka.db` (local-only, never synced).
- It can run every tool — indexing, journaling, records, task records, calendar — without depending on another machine.
- It shares **files** (tools, docs, video projects, markdown, configs) with peers via git.
- Its machine identity lives in gitignored `config/machine.local.json` and never syncs.

The durable spine is unchanged: **Markdown files + `data/pka.db` + `tools/` CLIs**. What changed is that each machine now owns its own DB instead of deferring writes to a single primary.

## 2. Databases are local and independent

- `data/pka.db` is **per-machine** and **never synced**. SQLite cannot merge concurrent writes, so the DB is deliberately not shared. Each peer's journal, knowledge base, records, and contacts are its own.
- Bootstrap a fresh peer's DB with `python3 tools/pka_db.py init` (idempotent).
- Two machines' databases will diverge over time. **That is expected and correct** under federation. The durable *file* layer (markdown under `owners-inbox/`, `wiki/`, `docs/`) is the shared memory; the DB is each machine's local index of it.
- If you want a machine to reflect another's structured state, re-index that machine's `owners-inbox/` markdown locally — do not copy a DB file between machines.
- Numeric database IDs are peer-local and are never portable identity. Shared task records use `project_key`; after pulling task changes, run `tools/task_record.py sync-local` to reconcile names, states, and indexes into that peer's DB.
- Compiled wiki pages may contain numeric IDs as local diagnostic hints, but `source_paths` are the portable provenance. A peer must not treat another peer's absent numeric row as lost source material when the source path resolves.

## 3. What syncs, and how

| Syncs via git | Stays local (gitignored) |
|---|---|
| `tools/` (all CLIs) | `data/pka.db` and `*.db-*` |
| `docs/`, `wiki/` markdown | `config/machine.local.json` |
| `owners-inbox/` markdown and video-project JSON | `discord-bridge/.env` and secrets |
| `team-inbox/` markdown | `data/*.json` volatile state, caches |
| `video-studio/` project definitions (motion briefs, overlay timing, scripts, `.cuecam` Config/Script JSON) | Video media: `*.mov`, `*.mp4`, `*.png`, thumbnails, cuecam-assets (see §5) |
| `tests/`, configs (`machine.json` tracked) | Virtualenvs, `__pycache__`, usage logs |

**Rhythm:** `git pull` → `tools/task_record.py sync-local` when task files changed → work → `git add -p` → `git commit` → `git push`. Pull before substantial work. Only one peer edits a given task or video-project manifest at a time; stage-sized commits are the handoff boundary.

## 4. Machine identity

- `config/machine.local.json` (gitignored) carries each machine's true identity: `machine_id`, `role: "standalone"`, `db_write: true`, `corpus_id`. Set it once per machine.
- `config/machine.json` (git-tracked) carries shared host-description metadata; `machine_role.py` prefers the local override.
- `tools/machine_role.py` is now an identity helper only. The old `require_writer()` / `guard_connection()` guards are no-ops retained for backward compatibility — they no longer block any write on any machine.

## 5. Bidirectional video work

Video pipelines may be updated on any peer and shared with the others.

- **Project definitions travel via git:** `owners-inbox/video-projects/<slug>/project.json`, development interviews/claims/challenge reports/outlines/briefs, `motion_brief.json`, `overlay_timing.json`, room docs, and `.cuecam/Config.json` + `Script.json`. These are small text/JSON.
- **Bulk media travels out-of-band:** raw recordings, renders, thumbnails, and `cuecam-assets` are gitignored. Sync them via rsync, Syncthing, a shared folder, or an external drive — not git. (Git LFS is a possible alternative if you prefer one channel; not currently configured.)
- When you pick up a video project on a new machine, `git pull` for the definitions and fetch the media via whichever out-of-band path the project uses. Document the media location in the project's room notes.
- Run `tools/video_project.py sync-development --slug <slug>` after producing or receiving development-stage files. It attaches the portable interview, claim register, challenge report, outline, sourced figures, balance check, brief, CueCam spec, and image suggestions that exist.
- Media records carry SHA-256 identity. If the same bytes live at a different path on the receiving peer, re-run `video_project.py attach` for that artifact kind; the path changes while the approval remains bound to the unchanged hash.
- Two machines must not edit the *same* video project simultaneously. Pull first, own one stage, commit and push the updated manifest, then hand off.

## 6. LLM access

Each machine may use any combination of LLM frontends and providers. There is no central LLM policy.

- **Interactive frontends** (OpenWorker, Antigravity, Claude app, ChatGPT app, Cursor, etc.) can read and write the PKA markdown corpus directly — it's just files on disk. Point them at the repo root.
- **API calls from tools** route through `tools/llm.py` (`LLM_PROVIDER` env var; providers: `xai`, `lmstudio`, with `anthropic`/`openai` adapters available to add). Per-provider keys live in the environment or `.env`, never in git.
- See `docs/larry/llm-providers.md` (where present) for the provider catalog.

## 7. Discord

Each machine may run its own Discord bridge with its own bot token. Two processes must never share one token (they'd race on inbox writes). Tokens stay in gitignored `discord-bridge/.env`. Use Discord for human-facing delivery and quick mobile input; it is no longer the *transport* between machines (git is).

## 8. Health and freshness

- `python3 tools/pka_health.py` is the per-machine definition of done. Exit 0 = clean.
- Under federation, unindexed `owners-inbox/` markdown is a real warning on every machine (no "queue for the mini" exemption). Each machine is responsible for indexing its own corpus.
- `python3 tools/machine_heartbeat.py` posts a liveness signal with the sha256 of this file as a version stamp.

## 9. Onboarding a new peer

1. `git clone` the repo.
2. Create `config/machine.local.json` with a unique `machine_id`, `role: "standalone"`, `db_write: true`, and a distinct `corpus_id`.
3. `python3 tools/pka_db.py init` to create the local DB.
4. Set up `discord-bridge/.env` with a dedicated bot token (never reuse another machine's).
5. Provide LLM credentials via environment or `.env` as needed.
6. Run `python3 tools/pka_health.py`; expect warnings about unindexed markdown until the corpus is indexed locally.
7. Index the local `owners-inbox/` markdown (or accept that this peer starts with an empty index and grows its own).
8. Run `python3 tools/task_record.py sync-local` so shared task state is represented by local DB rows without trusting another peer's numeric IDs.

## 9a. Python venvs (this MacBook)

This M1 MacBook runs a **native arm64 venv** at `discord-bridge/venv`, built with `uv`-installed CPython 3.13.14 (arm64). It holds torch 2.13.0 (MPS-enabled) and openai-whisper, so the video transcription pipeline runs natively without Rosetta. The legacy Rosetta environment, when needed for diagnosis, is retained separately at `discord-bridge/venv-x86`.

- **Use `discord-bridge/venv/bin/python3` for all tool invocations** on this machine.
- The video-studio shell scripts (`video-studio/bin/run_whisper_edit.sh`) call bare `whisper`; invoke them with the venv on PATH, e.g. `PATH="$PWD/discord-bridge/venv/bin:$PATH" ./video-studio/bin/run_whisper_edit.sh /path/to/raw.mp4`.
- Python venvs are not relocatable. If this directory must move or be renamed, recreate it at the destination rather than moving it; generated console scripts contain absolute interpreter paths.

## 10. Why federation (not the old primary/satellite model)

The Devendorf contract made the laptop a read-only satellite that shipped files home for the mini to index. That fit when there was one capable machine and one road node. It no longer fits: multiple LLM frontends are available on each machine, video work happens on both, and a GitHub remote already exists. Federation removes the single bottleneck (the mini as sole writer) without introducing the unsolvable problem of merging concurrent SQLite writes — because each DB stays local. The cost is that structured state (journal, KB, records) is per-machine; the benefit is that every machine is fully autonomous.
