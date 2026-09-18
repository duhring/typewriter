# PKA Starter

A portable copy of the Personal Knowledge Assistance (PKA) machinery: the tools, agent definitions, skills, and workflow docs that turn an interview into a recorded video, a YouTube package, and a written companion piece. It ships **no personal content**: no database, no inbox, no media. You supply your own corpus, keys, and machine identity.

Runs on macOS (Apple Silicon or Intel) inside [Claude Code](https://claude.com/claude-code). Everything durable is Markdown files, one local SQLite database, and the CLIs in `tools/`.

## Install

```bash
git clone <this-repo> PKA && cd PKA
./bootstrap.sh
```

The script installs Homebrew packages from `Brewfile`, builds a Python venv at `discord-bridge/venv`, creates `discord-bridge/.env` and `config/machine.local.json` from their examples, initializes `data/pka.db`, runs the smoke tests, and reports which optional tools are missing. Re-running is safe.

Flags: `--with-transcribe` adds local Whisper (about 2 GB), `--skip-brew` leaves Homebrew alone.

## What you have to do by hand

| Need | Why | Where |
|---|---|---|
| API keys in `discord-bridge/.env` | LLM calls from tools, Discord posting, thumbnail generation | `docs/larry/llm-providers.md` |
| Google Cloud OAuth client | Calendar, Docs, Drive, Sheets, YouTube upload | `tools/GCAL-SETUP.md`, then `tools/auth_doctor.py` |
| Claude Code CLI | The orchestrator (`CLAUDE.md`) and specialists (`.claude/agents/`) run inside it | `npm install -g @anthropic-ai/claude-code` |
| Node (HyperFrames) | HTML-based video renders, captions, TTS; tools call `npx --yes hyperframes` | `Brewfile` installs node |
| CueCam Presenter | Recording stage; live control needs Accessibility and Screen Recording permission | https://cuecam.app |
| Discord bot (optional) | Chat front-end to the orchestrator | `discord-bridge/README.md` |
| Ollama or LM Studio (optional) | Local models for retrieval and drafts | `docs/larry/llm-providers.md` |

## Layout

```
CLAUDE.md            orchestrator constitution (Larry): routing table, work rules
AGENTS.md            contract for coding agents working in the repo
.claude/agents/      specialists: Pax, Maven, Reed, Sable, Dreamer, Vera, Nolan
.claude/skills/      cuecam-deck-builder plus the HyperFrames skill set
tools/               ~60 Python CLIs; tools/README.md is the registry
docs/larry/          one procedure file per workflow
docs/federation.md   how two or more machines share files but not databases
tests/               smoke tests, no network or keys needed
config/              machine.json (shared) and machine.local.json (per machine, gitignored)
data/                pka.db and volatile state (gitignored)
owners-inbox/        your outputs: video projects, transcripts, packages, blog drafts
team-inbox/          your inputs
wiki/                compiled views of the corpus
video-studio/        HyperFrames and motion projects (media gitignored)
```

## The two main workflows

**Video**: `docs/larry/development.md` (interview, outline, balance check, brief) then `docs/larry/video-production.md` (CueCam recording, clean, QC, private YouTube review, publish, Substack adaptation). State lives in `owners-inbox/video-projects/<slug>/project.json`, written only through `tools/video_project.py`.

**Written pieces**: `docs/larry/youtube-writing.md`. A YouTube URL becomes a transcript and structured extract, Maven produces the title, description, and thumbnail prompt, Reed writes the blog post, Vera reviews.

## Working across machines

Clone on each Mac, run `bootstrap.sh` on each, and push and pull through git. Files sync, databases do not. Media travels by rsync or a shared folder. Full rules in `docs/federation.md`.

## Making it yours

- `CLAUDE.md`, the balance checker, and the challenge tool read the owner name from `config/machine.local.json`. Edit the routing table to add or drop workflows.
- Write `owners-inbox/brand-system.md`. Maven and Reed draft against it.
- Tools resolve the repo root from their own location. Paths you may want to override are `PKA_RECORDINGS_DIR`, `PKA_VIDEO_STUDIO`, and `PKA_BACKUP_DIR` in `.env`.
- Add a tool: drop a script in `tools/`, give it a docstring, add a line to `tools/README.md`.
