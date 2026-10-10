# Typewriter / PKA Starter

> **Keep Your Personal Data.**

A portable, local-first personal knowledge assistance system: the tools, agent definitions, skills, and workflow docs that turn an interview into a recorded video, a YouTube package, and a written companion piece — without surrendering your private notes, thinking, or creative voice to cloud platforms. It ships **no personal content**: no database, no inbox, no media. You supply your own corpus, keys, and machine identity.

Runs on macOS (Apple Silicon or Intel) with any AI assistant that can read files and run commands ([Claude Code](https://claude.com/claude-code), Codex, or another). Everything durable is Markdown files, one local SQLite database, and the CLIs in `tools/`.

## Start with a new idea

A fresh download starts without your personal interviews, projects, or previous work. Open the Typewriter folder with your assistant, point it to `AGENTS.md`, and say:

> Let’s start a new Typewriter run.

The assistant will ask what you would like to explore. A rough idea, question, or observation is enough; you do not need a video or an existing project. Your chat needs access to this folder and its instructions for the phrase to work.

After an interview you can choose a brief, an image, a storyboard, a presentation, a written seed, or more exploration. After artwork feedback and corrections, you get fresh next-step options, including ending the session. As you continue, saved interviews, decisions, and artifacts provide material for later work; the assistant should show the actual sources it builds on. See [the handoff guide](docs/possibilities-and-reuse.md).

## Updates are an option at the start of a run

Your assistant offers **Start with a new idea**, **Continue saved work** when available, **Check for Typewriter updates**, or **End the session**. You can also say “Update my Typewriter” at any time. Checking does not install anything automatically or delay a topic you already chose.

The [update procedure](docs/updating-typewriter.md) inspects your installation, backs up affected files and your database, preserves personal work and customizations, and verifies the system changes before reporting completion. Users on an older version need to request that update once to receive the new startup menu.

## Philosophy: Keep Your Personal Data

Most AI creation tools operate as cloud silos: they capture your thoughts, log your drafts to external servers, and lock your thinking inside proprietary platforms. Typewriter is built on the opposite principle:

- **Durable Local Spine**: All durable notes, interviews, briefs, and transcripts are plain Markdown files in your local folders (`owners-inbox/`).
- **Local SQLite Database**: The structured index and task tracker live in `data/pka.db` on your Mac.
- **Zero Telemetry or Cloud Database**: Nothing leaves your machine without your explicit command (e.g. when you publish an authorized video or release a blog post).
- **Model Agnostic**: AI assistants read local files and run local CLI tools (`bin/pka`) under your own subscription or local models. No third-party platform retains your creative data.

What If AI Interviewed You for Your Talk?
https://youtu.be/nkq5IdTT2FY
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
bin/pka session-cleanup      # clean scratch files, verify downloads mirror, end session
bin/pka test                 # the test suite
bin/pka check                # re-run the Python and toolchain check
```

Any assistant that can read files and run commands uses the same launcher. No model service, no model keys: your assistant does the thinking, PKA validates and records the results. See `docs/handoff-contract.md`.

Run the offline integration checks on a disposable clone after bootstrap. They clear known model-related variables, run the unit suite, and exercise five scripted paths (develop, two article inputs, and two publication modes) using canned assistant responses:

```bash
tests/acceptance/all.sh
```

These scripts create test projects and invoke approval commands with fixture owner
labels. They simulate YouTube IDs, QA checklists, and publication URLs; they do
not upload, release a video, verify human approval, or open CueCam. Passing proves
local validation and tracker behavior. Real CueCam playback/recording and an
owner-authorized YouTube upload, playback check, and release remain separate
integration checks.

Package and release approval require a current chapter review bound to the
package, transcript, and final master. Existing projects without this review
must prepare/import a new package and chapter review, then obtain fresh owner
approval. Re-extracting an interview retains run history and snapshots of prior
claims; owner decisions transfer only to an unchanged claim and supporting quote.

## Real production trial

On September 19, 2026, the starter was used on an Apple Silicon Mac to open a
CueCam bundle, record a video, remove silences, edit the result, approve its
publishing package, share the video as unlisted, and develop an article through
an owner interview. The owner published [Get Into Production](https://johnduhring.substack.com/p/get-into-production)
from that run.

This trial used an existing local whisper.cpp installation to supply a timed
transcript and YouTube Studio for upload. It did not verify the starter's Python
Whisper installation or Google OAuth upload path. For local transcription through
PKA, install the optional dependency with `./bootstrap.sh --with-transcribe`;
alternatively supply a timed transcript with `pipeline package prepare --transcript`.
For API upload, configure your own Google OAuth client and authorize YouTube.
The trial is evidence of one working Mac setup, not universal hardware coverage.

## What you have to do by hand

| Need | Why | Where |
|---|---|---|
| Keys in `discord-bridge/.env` (optional) | Discord posting only. No model keys: your assistant does the thinking, and thumbnails are artwork you supply | `discord-bridge/README.md` |
| Google Cloud OAuth client | Calendar, Docs, Drive, Sheets, YouTube upload | `tools/GCAL-SETUP.md`, then `tools/auth_doctor.py` |
| An AI assistant with file and command access | Runs the interviews, analysis, and drafting; calls tools through `bin/pka`. Claude Code reads `CLAUDE.md` and `.claude/`; any other assistant reads `AGENTS.md` | Claude Code: `npm install -g @anthropic-ai/claude-code`; or Codex, or another |
| Node (HyperFrames, optional) | HTML-based video renders, captions, TTS; tools call `npx --yes hyperframes`. None of the three core paths need it | `Brewfile` installs node |
| CueCam Presenter | Presenting and recording a deck; live control needs Accessibility and Screen Recording permission. Bundles build without it | https://cuecam-presenter.com |
| Discord bot (optional) | Chat front-end to the orchestrator | `discord-bridge/README.md` |

## Layout

```
AGENTS.md            the primary contract for any assistant: handoffs, entry points, rules
CLAUDE.md            the Claude Code adapter on top of it (Larry persona, routing table)
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

## The workflows and the First-Mile Experience

When starting with a new topic or for a first-time user, Typewriter doesn't force an immediate jump in front of a camera. Rushing directly into recording often creates camera dread and creative friction. Instead, Typewriter offers the **First-Mile Artifact Menu**:

1. **Intake Interview**: A short reflective Q&A (5–8 questions) to extract your unvarnished point of view.
2. **Artifact Menu**: Choose one or more starting mirrors to react to before recording:
   - **Visual Storyboard**: 5-panel concept or narrative progression
   - **Evocative Concept Image**: Visual metaphor capturing your central tension
   - **Structured Brief**: Clean summary of claims, thesis, and takeaways
   - **CueCam Presenter Deck**: Direct `.cuecam` bundle with download link ([CueCam Presenter](https://cuecam-presenter.com))
   - **Article Draft Seed**: Text-first companion draft for newsletter/blog writing
   - **Extend the Interview**: *"Want to explore a new angle or clarify your thinking: extend the interview"* with 3–5 deeper follow-up questions.
3. **Downloads Folder Mirroring**: For each artifact generated, a duplicate copy is automatically placed into `~/Downloads/` for immediate desktop double-click access without digging through repository folders.
4. **Refine, Branch, or Clean Up**: Reacting to concrete surrogate mirrors unlocks your voice. From there, proceed to video recording (`docs/larry/video-production.md`) or companion publishing (`docs/larry/youtube-writing.md`). When finished, say *"I'm good, end the session"* to trigger `bin/pka session-cleanup`, which removes temporary scratch files and verifies your saved deliverables.

**Core paths**:
- **Video**: `docs/larry/development.md` (interview, first-mile menu, outline, balance check, brief) then `docs/larry/video-production.md` (CueCam recording, clean, QC, private YouTube review, publish, Substack adaptation). State lives in `owners-inbox/video-projects/<slug>/project.json`, written only through `tools/video_project.py`.
- **Written pieces**: `docs/larry/youtube-writing.md` or `article` entry point. A video or intake becomes a transcript/extract, Maven shapes packaging, Reed drafts the post, Vera reviews.

## Working across machines

Clone on each Mac, run `bootstrap.sh` on each, and push and pull through git. Files sync, databases do not. Media travels by rsync or a shared folder. Full rules in `docs/federation.md`.

## Making it yours

- `CLAUDE.md`, the balance checker, and the challenge tool read the owner name from `config/machine.local.json`. Edit the routing table to add or drop workflows.
- Write `owners-inbox/brand-system.md`. Maven and Reed draft against it.
- Tools resolve the repo root from their own location. Paths you may want to override are `PKA_RECORDINGS_DIR`, `PKA_VIDEO_STUDIO`, and `PKA_BACKUP_DIR` in `.env`.
- Add a tool: drop a script in `tools/`, give it a docstring, add a line to `tools/README.md`.

- ## More Reference

- On Substack: [What If Your Typewriter Gave You a Menu Before You Write?](https://johnduhring.substack.com/p/what-if-your-typewriter-gave-you)
- On YouTube: [What If Your Typewriter Gave You a Menu Before You Write?](https://youtu.be/sK6pXStMu0Q)
- On Substack: [Built for You](https://johnduhring.substack.com/p/built-for-you)
- On YouTube: [Typewriter Walkthrough](https://youtu.be/ET9Vc8mj8Wk)
