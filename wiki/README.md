# PKA Wiki

The `wiki/` directory is PKA's compiled context layer.

It is the human-readable synthesis view generated from source material in `data/pka.db`, indexed owner artifacts, task records, session logs, and other durable files. It is meant to help Larry, Dreamer, Pax, Reed, and the rest of the team start from a current narrative instead of reconstructing context from raw rows every time.

## Rules

- Treat `data/pka.db` and source files as the source of truth.
- Treat wiki pages as compiled views, not primary records.
- Keep claims traceable through `source_kb_ids`, `source_journal_ids`, `source_paths`, or linked task records.
- Flag contradictions, uncertainty, and stale claims instead of smoothing them over.
- Do not compile wiki pages from other wiki pages as primary evidence.
- Prefer updating an existing page over creating a near-duplicate page.

## Compiler

Project pages can be refreshed with:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py projects --project "PKA System"
```

All project pages can be refreshed with:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py projects --all
```

Concept pages can be refreshed with:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py concepts --all
```

People pages can be refreshed with:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py people --all
```

Freshness and contradiction audit:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py audit
```

Safe repair preview for stale generated pages and index links:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py audit --repair --dry-run
```

Weekly editorial recurrence report:

```bash
discord-bridge/venv/bin/python3 tools/editorial_recurrence.py --days 7 --limit 12
```

## Page Types

- `projects/` - active or recurring efforts, products, workflows, and initiatives.
- `concepts/` - durable ideas, frameworks, themes, and reusable mental models.
- `people/` - important recurring people and relationship context.
- `operations/` - synthesized operating knowledge that is not quite an SOP.

## Frontmatter

Every compiled page should start with:

```markdown
---
title: Page Title
type: project|concept|person|operation
status: active|reference|stale|needs-review
last_compiled: YYYY-MM-DD
source_kb_ids: []
source_journal_ids: []
source_paths: []
confidence: low|medium|high
compiled_by: Dreamer
---
```

## Expected Page Shape

Use this structure unless a page needs something more specific:

```markdown
# Page Title

## Current Synthesis

Short narrative summary of what PKA currently understands.

## Known Facts

- Fact with provenance.

## Open Questions

- Unresolved issue, ambiguity, or missing source.

## Related Pages

- [[Another Page]]

## Source Notes

- KB #123 - title or source path.
```
