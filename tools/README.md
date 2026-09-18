# PKA Tools Registry

One line per tool, grouped by domain. Run everything with `discord-bridge/venv/bin/python3 tools/<name>.py` unless a doc says otherwise. When adding a tool, add a line here and give the module a one-line docstring; Dreamer audits this file during weekly refinement.

## Video pipeline (record → publish)

| Tool | Purpose |
|---|---|
| `cuecam_recording_intake.py` | Watch the CueCam recordings folder; send new stable recordings to cleaning (pipeline step 0) |
| `clean_video.py` | Remove silences and stumbles from recordings |
| `transcribe.py` | Whisper transcription of local audio/video |
| `fetch-transcript.py` | Fetch transcript from a YouTube URL/ID or local file (legacy hyphen name — keep for compatibility) |
| `video_project.py` | Manual-first video state, artifact identities, approvals, publication, and metrics |
| `video_qc.py` | Final-master technical QC plus indexed human-review checklist |
| `pipeline.py` | Separate approved-master package and private-upload phases; explicit one-shot exception |
| `generate_thumbnail.py` | YouTube thumbnail via OpenAI Images API |
| `publish_to_youtube.py` | Upload video using a Maven package .md for metadata |
| `watch_video_capture.py` | Frame-aware video watching (scene-detection frames + transcript) |
| `extract_structured.py` | Structured extracts from transcripts and meeting notes |

## CueCam / HyperFrames authoring

| Tool | Purpose |
|---|---|
| `cuecam.py` | Create .cuecam bundles from Google Docs, card specs, or markdown |
| `hyperframes_html_gen.py` | Generate HyperFrames index.html from overlay_timing.json + assets |
| `hyperframes_state.py` | HyperFrames per-recording state manager |
| `codex_hyperframes_render.py` | Fire-and-forget Discord-safe render orchestrator |
| `check_render_jobs.py` | Health check for render jobs stuck in "running" |

## Knowledge base, memory, wiki

| Tool | Purpose |
|---|---|
| `pka_index.py` | Index durable artifacts into data/pka.db (definition-of-done step) |
| `harness_audit.py` | Measure core instruction routes, duplication, plugin/agent inventory, approvals, and inbox startup load |
| `index_sweep.py` | Auto-index sweep for artifacts that missed indexing |
| `memory_retrieval.py` | Retrieve relevant prior context for a new request |
| `conversation_memory.py` | Rolling conversation memory for the Discord bots |
| `wiki_compile.py` | Compile durable source material into wiki pages |
| `editorial_recurrence.py` | Find recurring editorial signals in recent artifacts (Dreamer input) |
| `balance_check.py` | Pre-record check of an outline against the archive: retread risk, contradictions, brand drift |
| `promote_sop.py` | Promote archive learnings into candidate SOP/playbook updates |
| `notebooklm.py` | NotebookLM Q&A with citation receipts |

## Records, tasks, session orientation

| Tool | Purpose |
|---|---|
| `session_orient.py` | All-in-one session startup orientation CLI (task records + inbox + health) |
| `records.py` | Governed owner records in the PKA database |
| `task_record.py` | Canonical shared task records plus peer-local DB reconciliation (`sync-local`) |
| `health_escalation.py` | Deduplicate repeated health findings and manage morning-brief escalation state |
| `journal.py` | Journal entries, glossary, contacts |
| `session_log.py` | Durable session logs for Codex work |

## Google workspace

| Tool | Purpose |
|---|---|
| `gcal.py` / `gdocs.py` / `gdrive.py` / `gsheets.py` / `gmaps.py` | Calendar, Docs, Drive, Sheets, Maps CLIs |
| `morning_briefing.py` | Scheduled daily briefing sender → Discord (LaunchAgent consumer) |
| `auth_doctor.py` | Google auth diagnosis/repair |

## LLM providers

| Tool | Purpose |
|---|---|
| `llm.py` | Provider router (use this, not the helpers directly) |
| `glm.py` / `xai.py` / `lmstudio.py` | GLM, Grok, and local LM Studio helpers with usage logging |
| `rotate_glm_key.py` | Install `GLM_API_KEY` from the clipboard and select GLM as the default provider |


## Ops, health, comms

| Tool | Purpose |
|---|---|
| `pka_health.py` | Health doctor |
| `health_digest.py` | Scheduled daily health digest |
| `backup_pka_db.py` | Nightly pka.db backup |
| `bot_liveness.py` | Alert when the Discord bot is down |
| `machine_heartbeat.py` / `machine_role.py` | Federated-peer identity and operating-agreement heartbeat |
| `discord_send.py` | Send to #larry |
| `notify.py` | Outbound notifier (Larry speaks first) |
| `rotate_discord_token.py` / `rotate_logs.py` | Token and log rotation |
