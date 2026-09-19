---
created_at: 2026-09-18T23:36:32
task_kind: bugfix
source: codex-session
title: "Fix assistant-neutral starter review regressions"
files_changed: 15
---

# Fix assistant-neutral starter review regressions — Codex Session Log

## Summary

Preserved challenge history and owner decisions across re-extraction, bound evidence and chapter reviews to current sources, and made required bootstrap failures stop installation. Updated verification claims. All 329 unit tests passed with one skipped; five scripted acceptance paths passed in an isolated clone with a fresh database and the existing Python environment.

## Change Metrics

- Files changed: 15

## Deliverables

- tools/challenge.py
- tools/pipeline.py
- tools/video_project.py
- bootstrap.sh
- tests/test_review_regressions.py
- docs/plan-assistant-neutral-starter.md

## Durable Learnings

- Tracker approval must require the current chapter-review artifact, not merely successful handoff import at an earlier time.
- Retain previous claim registers and run history; transfer owner decisions only when claim and quote identity match.

## Open Loops

- Real CueCam playback and owner-authorized YouTube upload, QA and release remain unverified.
- Mini upgrade and back-port remain outside this change.
