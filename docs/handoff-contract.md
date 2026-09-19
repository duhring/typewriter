# Handoff contract

How PKA hands editorial work to the owner's chosen assistant and takes the result back. This is the one shape every stage uses; the stage tools differ only in what goes in the payload and what an item looks like.

Implementation: `tools/handoff.py`. Tests: `tests/test_handoff.py`.

## The three steps

1. **prepare.** A stage tool writes `<stage>.request.json`. It contains the material to work from, the IDs the response must answer (when the tool knows them), and a SHA-256 of every input file.
2. **respond.** The assistant reads the request and the input files, does the editorial work, and writes `<stage>.response.json` beside the request.
3. **import.** The stage tool validates the response against the request and applies it exactly once. A rejected response changes nothing. Importing the same response a second time is a no-op.

Owner approval is a separate action. Importing a response never creates an approval, and re-importing one never grants or removes one.

## Request file

```json
{
  "schema_version": 1,
  "request_id": "9f1c2a7b3d4e5f60",
  "stage": "challenge.extract-claims",
  "created_at": "2026-09-18T17:02:11+00:00",
  "slug": "coffee-break-test",
  "inputs": {
    "interview": {"path": "owners-inbox/development/coffee-break-test/interview.md", "sha256": "…"}
  },
  "expects": null,
  "payload": {"windows": [[0, 4000], [3500, 7500]]},
  "instructions": "For each window, list the discrete claims the owner makes…"
}
```

- `request_id` is derived from the stage, the input hashes, and the payload. Preparing the same request twice gives the same ID, so a valid response is not orphaned by a re-prepare.
- `inputs` maps a role name to the file the assistant must read. Every file is hashed at prepare time.
- `expects.ids`, when present, is the exact set of IDs the response must answer. It is null when the assistant creates the IDs itself (claim extraction, card specs).
- `payload` is stage-specific: windows, batches, overlaps, timed segments, and so on.
- `instructions` is what the assistant should produce, in plain words.

## Response file

```json
{
  "schema_version": 1,
  "request_id": "9f1c2a7b3d4e5f60",
  "stage": "challenge.extract-claims",
  "inputs": {"interview": "…same sha256 as the request…"},
  "items": [
    {"id": "c1", "quote": "the coffee break test works", "claim": "…"},
    {"id": "c2", "quote": "…", "claim": "…"}
  ]
}
```

- Copy `request_id`, `stage`, and every input hash from the request. The hashes say which version of the material the response was written against.
- `items` is a list. Every item has a unique string `id`. What else an item carries is defined by the stage.
- When the request lists `expects.ids`, answer every one of them and nothing else.

## What import refuses

Checked in this order. The first failure is reported and nothing is applied.

| Code | Meaning |
|---|---|
| `schema_version` | The response does not declare schema version 1. |
| `stage_mismatch` | The response names a different stage than the request. |
| `unknown_request` | The response names a different request ID. |
| `stale_input` | An input hash in the response differs from the request, or an input file has changed on disk since the request was prepared. |
| `bad_items` | `items` is not a list, or an item lacks a string `id`. |
| `duplicate_ids` | An `id` appears more than once. |
| `missing_ids` | The request expected IDs and the response did not answer all of them. |
| `unknown_ids` | The response answered IDs the request did not ask about. |
| `quote_not_found` | A stage that requires verbatim quotes could not find one in the source. |
| `item_invalid` | The stage's own item check failed for some other reason. |
| `missing_stage` | The request depends on an earlier stage whose response has not been imported for this run. |

A failed apply is not recorded. The ledger gains a record only after the stage's work completes, so a crash mid-apply leaves the response importable once the cause is fixed.

To check a response without applying it:

```bash
python3 tools/handoff.py validate --request path/to/<stage>.request.json
```

The result names the rule that failed, or reports `valid` and whether this exact response has already been imported.

## Import ledger

Each directory that holds requests also holds `handoff-imports.json`. Every applied import adds one record: stage, request ID, response path, response hash, and time. Import checks the ledger before applying; a response already recorded for the same request is skipped and reported as `already_imported`. A revised response to the same request has a different hash and is applied.

```bash
python3 tools/handoff.py ledger path/to/directory
```

## Sequential stages

Where a stage depends on an earlier one, the handoffs run in sequence inside one recorded run, each with its own request and response. The challenge gate is three: extract claims, then challenge (after PKA verifies quotes and retrieves evidence), then context review (after the proposed verdicts exist). The YouTube package is two: the package, bound to the transcript and source inputs, then chapter review, bound to the imported package's hash.

A later request records the earlier one under `depends_on` as a stage and request ID. Import refuses the later response with `missing_stage` until the earlier response is in the ledger. The earlier stage's output file is also listed in the later request's `inputs`, so editing it after the later request was prepared makes that request stale.

```json
"depends_on": [{"stage": "pipeline.package", "request_id": "9f1c2a7b3d4e5f60"}]
```

## Approvals stay bound to bytes

An owner approval records the hash of every file it covers. It goes stale when a changed file is re-attached, and also when an approved file is edited in place, deleted, or replaced without a re-attach. Restoring the exact approved bytes restores the approval. No import refreshes a stale approval; only the owner approving again does.

## For stage tool authors

```python
import handoff

request = handoff.prepare(
    stage="balance-check.analysis",
    directory=dev_dir,
    inputs={"outline": outline_path, "overlaps": overlaps_path},
    payload={"top": 8},
    expects_ids=[o["id"] for o in overlaps],
    instructions="For each overlap, say whether the outline retreads it…",
    slug=slug,
)

result = handoff.import_response(
    request.path,
    apply=lambda items: render_report(items),
    item_validator=lambda item: check_analysis_shape(item),
)
```

- Put everything the assistant needs in `inputs` and `payload`. Do not make the assistant guess at file locations.
- Pass `expects_ids` whenever the tool knows the IDs. Leave it out only when the assistant creates them.
- Raise `handoff.HandoffError(code, message)` from the item validator for stage-specific rules. `handoff.require_quote(source_text, item)` is provided for verbatim quotes.
- `apply` does the stage's work and nothing else. It must never call an approval function.
