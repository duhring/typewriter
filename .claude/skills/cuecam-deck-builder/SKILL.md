---
name: cuecam-deck-builder
description: Build high-legibility CueCam presenter presentation decks (.cuecam bundles) from natural language notes, markdown briefs, or transcripts.
---

# CueCam Deck Builder Skill

## Overview

Use this skill whenever building a `.cuecam` presentation bundle for John's video presentations. This workflow converts Markdown outlines, Google Docs, Discord notes, or dictation transcripts into ready-to-present CueCam bundles optimized for live recording.

---

## High-Readability Visual Themes

The deck builder supports 5 standardized design themes tuned for **Maximum Video Legibility**:

| Theme Key | Visual Style | Typography | Background Palette | Default Layout |
| :--- | :--- | :--- | :--- | :--- |
| `cyber-glass-max` | Dark Glassmorphism & Emerald Glow | `HelveticaNeue-Bold` (112pt) | Midnight Slate (`#0D1117`) | `center` |
| `swiss-max` | International Typographic / Amber Badges | `AvenirNext-Heavy` (112pt/144pt) | Deep Charcoal (`#18181B`) | `lower-third` |
| `warm-editorial` | Espresso & Cream Serifs | `Georgia-Bold` (112pt) | Deep Espresso (`#1C1917`) | `center` |
| `broadcast-alpha` | Transparent Lower-Third Overlay | `SFProText-Bold` (108pt) | Transparent (`null`) | `lower-third` |
| `sage-workshop` | Sage Slate & Ochre Yellow | `Futura-Medium` (112pt) | Forest Sage (`#1A2E26`) | `center` |

---

## Green-Screen & Unadjusted Camera Contract

Unless explicitly overridden by the owner:
- **`pictureInPicture`: `false`** — The main camera remains 100% unadjusted in center frame so CueCam Presenter handles chromakey / green screen keying directly.
- **`backgroundStyle`: `"fullContentArea"`** — Graphic card backdrops fill the 2:3 vertical canvas behind the presenter.
- **`padding`: `{"bottom": 10, "leading": 10, "top": 10, "trailing": 10}`** — Minimal margins, maximizing usable image and text scale.

---

## How to Invoke via `tools/cuecam.py`

### 1. Build from Mobile Card Spec or Markdown
```bash
discord-bridge/venv/bin/python3 tools/cuecam.py compose \
  --spec-file owners-inbox/my-talk.md \
  --theme cyber-glass-max \
  --title "My Presentation Title"
```

### 2. Output Locations
- **Default Presentations Inbox**: `owners-inbox/presentations/<DATE>-<SLUG>.cuecam`
- **Downloads Folder**: Move or copy a duplicate to `~/Downloads/<SLUG>.cuecam` for immediate local double-click preview.

---

## Card Markdown Syntax

Each card entry in a spec file follows:

```markdown
theme: cyber-glass-max
layout: center
pip: off

1. "Headline Title", Teleprompter line to speak out loud
Background-
! 01-card-image.jpg
```
