# PKA Starter

A portable copy of the Personal Knowledge Assistance (PKA) machinery: the tools, agent definitions, skills, and workflow docs that turn an interview into a recorded video, a YouTube package, and a written companion piece. It ships **no personal content**: no database, no inbox, no media. You supply your own corpus, keys, and machine identity.

Runs on macOS (Apple Silicon or Intel) inside [Claude Code](https://claude.com/claude-code). Everything durable is Markdown files, one local SQLite database, and the CLIs in `tools/`.

## Install

```bash
git clone https://github.com/duhring/typewriter.git PKA && cd PKA
./bootstrap.sh
```

The script installs Homebrew packages from `Brewfile`, checks the Python it selects (path, version, and architecture, and whether the developer tools can build for it), builds a Python venv, creates `discord-bridge/.env` and `config/machine.local.json` from their examples, initializes `data/pka.db`, runs the smoke tests, and reports which optional tools are missing and which workflow needs each. Re-running is safe: an existing venv is validated before it is reused, and one built by an incompatible interpreter is reported with the command to remove it, never reused silently.

Flags: `--with-transcribe` adds local Whisper (about 2 GB, only for transcribing local video files), `--skip-brew` leaves Homebrew alone, `--python PATH` picks the interpreter (or set `PKA_PYTHON`), `--allow-rosetta` accepts an Intel Python on Apple Silicon when the developer tools can still build for it.

Run every tool through the launcher, which hides where the environment lives:

```bash
bin/pka list                 # tools available
bin/pka challenge --help     # tools/challenge.py
bin/pka test                 # the test suite
bin/pka check                # re-run the Python and toolchain check
```

Any assistant that can read files and run commands uses the same launcher. No model service, no model keys: your assistant does the thinking, PKA validates and records the results. See `docs/handoff-contract.md`.

## What you have to do by hand

| Need | Why | Where |
|---|---|---|
| Keys in `discord-bridge/.env` (optional) | Discord posting only. No model keys: your assistant does the thinking, and thumbnails are artwork you supply | `discord-bridge/README.md` |
| Google Cloud OAuth client | Calendar, Docs, Drive, Sheets, YouTube upload | `tools/GCAL-SETUP.md`, then `tools/auth_doctor.py` |
| An AI assistant with file and command access | Runs the interviews, analysis, and drafting; calls tools through `bin/pka`. Claude Code reads `CLAUDE.md` and `.claude/`; any other assistant reads `AGENTS.md` | Claude Code: `npm install -g @anthropic-ai/claude-code`; or Codex, or another |
| Node (HyperFrames, optional) | HTML-based video renders, captions, TTS; tools call `npx --yes hyperframes`. None of the three core paths need it | `Brewfile` installs node |
| CueCam Presenter | Presenting and recording a deck; live control needs Accessibility and Screen Recording permission. Bundles build without it | https://cuecam.app |
| Discord bot (optional) | Chat front-end to the orchestrator | `discord-bridge/README.md` |

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
