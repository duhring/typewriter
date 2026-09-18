# Larry Records Charter

Use this file when the owner asks for general record keeping, record classification, retention guidance, archive review, or registration of a durable owner record.

This is a phase-1 charter. It adds governance to the current PKA inbox + file + indexing system without replacing the workflows that already work.

## Purpose

PKA already captures and retrieves information well. This charter adds a thin records layer so owner records are:

- captured with the right level of verification
- stored using a controlled vocabulary
- linked back to their source artifacts
- reviewed with a clear lifecycle state

Phase 1 goal:

- govern records conservatively
- do not auto-delete owner records
- make archive and retention review explicit before disposal exists

## Scope

Treat these as distinct:

- **PKA operational artifacts** -- Discord notes, replies, session logs, task records, SOP promotions, and other system-operating artifacts
- **Owner records** -- financial, tax, property, utility, medical, legal, correspondence, meeting, and contact records the owner may later rely on

Operational artifacts are still important, but they should not define the policy for owner records.

## Core Rules

### 1. Capture accurately

- For low-risk records, capture once and store normally
- For high-risk records, use `preview -> confirm -> commit`
- High-risk means any financial, tax, legal, medical, property, or externally sourced record that could materially matter later
- If a photo or dictation is ambiguous, do not silently guess; ask or mark the record as `unreviewed`

### 2. Store logically and consistently

Every governed record should have:

- `record_class`
- `record_series`
- `event_date` when known
- `verification_status`
- `retention_class`
- `record_state`
- `sensitivity`

Use a stable `record_series` for recurring ledgers or families of records.

Examples:

- `sheila-checkbook-2026`
- `sonoma-receipts-2026`
- `utility-meters-2026`
- `taxes-2025`
- `property-correspondence-sonoma-2026`

### 3. Retrieve quickly and reliably

Each governed record should point back to at least one durable source:

- a `files` row
- a `knowledge_base` row when indexed content exists
- a stable source path when the file is external or not yet indexed

When the same person, property, tenant, payee, or meeting appears across records, use consistent names so later linking is possible.

### 4. Preserve or dispose according to sound policy

Phase 1 policy:

- allowed states are `active`, `reference`, `archived`, and `hold`
- disposal is manual review only
- no automated deletion or purge jobs
- when in doubt, archive rather than dispose

`hold` means do not dispose, even if a normal review would otherwise suggest it.

## Controlled Vocabulary

### Record Classes

Allowed `record_class` values:

- `pka-operational-artifact`
- `financial-transaction`
- `financial-statement`
- `tax`
- `property`
- `utility`
- `correspondence`
- `contact`
- `meeting`
- `medical`
- `legal`
- `reference`

### Verification Status

Allowed `verification_status` values:

- `unreviewed`
- `previewed`
- `confirmed`
- `imported`

Use:

- `unreviewed` when capture happened but no confirmation has occurred
- `previewed` when Larry showed a table or summary but final owner confirmation has not happened yet
- `confirmed` when the owner approved or the workflow has an explicit confirmation step
- `imported` when the record was created outside PKA and preserved here as source material

### Retention Classes

Allowed `retention_class` values:

- `manual-review`
- `current-year`
- `7-years`
- `permanent`
- `until-superseded`

Phase 1 defaults:

- financial transactions -> `7-years`
- financial statements -> `7-years`
- tax -> `7-years`
- property -> `permanent`
- utility -> `current-year` unless tied to property or city reporting
- correspondence -> `manual-review`
- contact -> `until-superseded`
- meeting -> `manual-review`
- medical -> `permanent`
- legal -> `permanent`
- reference -> `manual-review`
- pka operational artifacts -> keep under existing PKA rhythms unless explicitly registered as owner records

### Record States

Allowed `record_state` values:

- `active`
- `reference`
- `archived`
- `hold`

### Sensitivity

Allowed `sensitivity` values:

- `normal`
- `confidential`
- `restricted`

Use `restricted` for records that should be handled with extra care because of financial, medical, legal, or identity-sensitive contents.

## First-Phase Workflow

Preferred pattern:

1. Capture the source into `team-inbox/`, `team-inbox/discord/`, or another stable input path
2. Create or update the durable artifact
3. If the artifact is markdown, index it with `tools/pka_index.py`
4. Register the record with `tools/records.py`, or use `pka_index.py index-markdown` with record arguments
5. Mark verification status honestly
6. Leave lifecycle state conservative unless the owner asks for archive review

## Pilot Workflows

### Sheila Checkbook

- `record_class`: `financial-transaction`
- `record_series`: `sheila-checkbook-2026`
- `verification_status`: `previewed` or `confirmed`
- `retention_class`: `7-years`
- `record_state`: `active`
- `sensitivity`: `confidential`

Rule:

- For handwritten register transcription, prefer showing the extracted rows before appending them to the ledger
- Use `tools/checkbook.py` so the preview and commit steps leave durable artifacts

### Sonoma Receipts

- `record_class`: `financial-transaction` for individual receipts
- `record_class`: `financial-statement` for summary artifacts
- `record_series`: `sonoma-receipts-2026`
- `verification_status`: `confirmed` when totals are checked
- `retention_class`: `7-years`
- `record_state`: `active` or `reference`
- `sensitivity`: `confidential`

## Tooling

Use the records CLI for governed registration:

```bash
discord-bridge/venv/bin/python3 tools/records.py ensure-schema
discord-bridge/venv/bin/python3 tools/records.py upsert \
  --title "Sonoma 2026 Receipts Ledger" \
  --record-class financial-statement \
  --series sonoma-receipts-2026 \
  --event-date 2026-01-01 \
  --verification-status confirmed \
  --retention-class 7-years \
  --state active \
  --sensitivity confidential \
  --source-path owners-inbox/Sonoma-2026-Receipts.xlsx
```

For markdown artifacts, prefer the existing indexing flow with record metadata:

```bash
discord-bridge/venv/bin/python3 tools/pka_index.py index-markdown \
  --file owners-inbox/taxes/2025-sonoma-income-statement.md \
  --category finance \
  --tags taxes,sonoma \
  --record-class tax \
  --record-series taxes-2025 \
  --event-date 2025-12-31 \
  --verification-status confirmed \
  --retention-class 7-years \
  --record-state reference \
  --sensitivity confidential
```

## Phase-1 Boundaries

Do not do these yet:

- automatic disposal
- silent record-class inference for high-risk materials
- broad backfill of old records without a series-by-series review
- treating all KB entries as governed records

The right first move is small: start with the few recurring record series the owner actually uses, then expand deliberately.
