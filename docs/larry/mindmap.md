# Map my thinking

Offer this after an interview or when returning to an interview-only folder.
The assistant assembles meaning; the CLI checks sources and builds the viewer.
No model service, database, wiki, Node installation or server is required to
build, view or export an existing outline. Generating new thinking requires the
owner's chosen assistant. Viewer code ships in `tools/assets/mindmap.html`.

## Assemble from the interview

Use the confirmed read-back as the central topic, then themes, examples,
tensions and open questions. Preserve unanswered questions and contradictions.
Do not invent the owner's position. Every interview-derived node has `by=owner`
and a reference to the source version. Assistant connections have `by=suggested`
and a quoted `basis`. References establish provenance, not correct interpretation.
Ask the owner to confirm or correct the topic and major branches.

Save `owners-inbox/development/<slug>/mindmap.md` with JSON front matter and a
Markdown list. Two spaces indent each level. Each node has a unique, stable
`id`, one of `topic`, `theme`, `example`, `tension`, `open-question`, and `by`.
Use the full interview SHA-256 in front matter and its first 12 characters in
references. `L1-3` references lines; `Q1` requires that Q&A ID in the interview.

Example (replace HASH and PREFIX with the actual full hash and its prefix):

```text
---
{"interview_sha256":"HASH", "draft":true}
---
- Speaking to think {id=root type=topic by=owner src=interview@PREFIX:L1-2}
  - Practice {id=practice type=theme by=owner src=interview@PREFIX:L3-4}
  - Would a deck help? {id=deck type=open-question by=suggested basis="Possible practice aid"}
```

Record owner corrections separately as `edited=owner@YYYY-MM-DD`; never change
suggested attribution just because the owner edited a node. Preserve stable IDs.
Optional `source_versions` maps old hash prefixes to their full hashes. Old
references retain their original version until explicitly accepted refreshes.

```bash
bin/pka mindmap check --outline owners-inbox/development/<slug>/mindmap.md
bin/pka mindmap build --outline owners-inbox/development/<slug>/mindmap.md
```

Build reports indexing independently: indexed, indexed with category fallback,
skipped (no database), or failed (real indexing error). Indexing never blocks
viewing. A missing categories registry retains PKA's existing fallback.
When a manifest exists, `video_project sync-development --slug <slug>` registers
the map; no new approval gate or project is needed solely to make a map.

## View and export offline

Double-click `mindmap.html` in Safari or Chrome. Click or press Enter/Space on a
branch to collapse it; scroll to zoom and drag the background to pan. Fit map
and Expand all reset the view. Select a node to see full text and provenance.
Suggested nodes have dashed outlines and a visible tag; edited nodes and stale
sources are marked. SVG/PNG export captures the currently visible branches;
choose Expand all first to export the complete map. Exports include review state
and map version. Very large PNGs are scaled to an 8192-pixel maximum dimension.

Browser downloads normally land in its configured download folder. Move exports
into the project's development folder when they should be retained and mirrored.
Use `bin/pka session-cleanup --slug <slug>` for safe project copies under
`$HOME/Downloads/<slug>/`; never make direct replacement copies over user edits.
The outline, viewer, SVG/PNG files and version archive retain their relative paths.

## Owner review and exploratory reuse

Maps begin as drafts. Only run this after explicit owner confirmation:

```bash
bin/pka mindmap review --outline owners-inbox/development/<slug>/mindmap.md --by "<owner>"
```

The review binds to the Markdown body hash, excluding front matter to avoid a
self-referential hash. Any body edit, or changed interview, makes it stale.
Record the same review as a project decision when a manifest exists:

```bash
bin/pka video_project decision --slug <slug> --stage development --artifact mindmap --by "<owner>" --summary "Confirmed mindmap topic and major branches" --why "Reviewed map body SHA-256: <hash returned by mindmap review>"
```

The viewer displays Draft — not reviewed or Draft — review stale unless the
review is current. When the owner explicitly requests exploration from a draft,
proceed and label the resulting brief, cards or article outline as draft; cite
the map's version/body hash and source. Do not describe a draft map as an
approved representation of the owner's thinking. This does not change any
existing publication approval gate.

## Extend and refresh

Extend the interview using the existing interview workflow. `check` and reflection
report needs refresh; old references remain identifiable. Write a *separate*
candidate outline against the updated interview; do not silently replace the
current map or owner-edited branches.

```bash
bin/pka mindmap refresh-propose --outline owners-inbox/development/<slug>/mindmap.md --candidate owners-inbox/development/<slug>/candidate.md
# Show the owner the diff and obtain explicit acceptance.
bin/pka mindmap accept --outline owners-inbox/development/<slug>/mindmap.md --by "<owner>"
```

The proposal is bound to the current outline and interview hashes. A changed
input refuses acceptance. Acceptance archives the old outline under
`mindmap.versions/<file-hash>.md`, clears the old review, and rebuilds the viewer.
Changing or deleting an owner-edited node is refused unless the owner explicitly
accepts that specific change and `--allow-edited-changes` is supplied.

## Connect to previous work (opt-in)

Default assembly uses only the interview, even on a rich installation. Offer
**Connect this to my previous work** separately; never run retrieval automatically.
When requested, read relevant records/wiki views through existing read-only
retrieval, inspect original sources, and add `by=suggested` nodes with
`basis="<record path, source hash, and explanation>"`. Use the same proposal and
acceptance workflow. Do not edit prior records or other projects; normal indexing
may update this map's record only. A wiki view is a pointer, not owner testimony.

## Verification

Run `bin/pka test`. Browser acceptance is in
`tests/acceptance/mindmap-browser.cjs`; it opens a fixture via `file://`, blocks
and logs attempted network requests, exercises controls and validates SVG/PNG
downloads. No static scan or Wi-Fi toggle substitutes for that runtime check.
Use a browser automation environment with Playwright available. Test Chrome and
Safari manually on the target Mac as well, particularly local-file downloads.
