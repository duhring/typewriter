#!/usr/bin/env python3
"""
Handoff contract: how PKA hands editorial work to the owner's assistant and
takes the result back.

Every stage that used to call a model now follows one shape:

  1. prepare  — the tool writes <stage>.request.json: the material, the IDs
                the response must address, and a hash of every input.
  2. respond  — the assistant reads the request and writes <stage>.response.json
                beside it.
  3. import   — the tool validates the response against the request and
                applies it. A rejected response changes nothing.

This module owns the file formats and the validation. Stage tools supply the
payload, the expected IDs, and a per-item validator; they never re-implement
the checks.

Request file (schema_version 1):
  {
    "schema_version": 1,
    "request_id": "<16 hex, derived from stage + input hashes + payload>",
    "stage": "challenge.extract-claims",
    "created_at": "<iso utc>",
    "slug": "<project or development slug, optional>",
    "inputs": {"interview": {"path": "...", "sha256": "..."}},
    "expects": {"ids": ["c1", "c2"]} | null,
    "payload": {...stage specific...},
    "instructions": "<what the assistant should write, optional>"
  }

Response file (schema_version 1):
  {
    "schema_version": 1,
    "request_id": "<same as the request>",
    "stage": "<same as the request>",
    "inputs": {"interview": "<sha256, same as the request>"},
    "items": [{"id": "c1", ...}, ...]
  }

Import ledger (handoff-imports.json beside the request):
  one record per applied response: stage, request_id, response_sha256,
  imported_at. Importing a response already in the ledger is a no-op.

Rules this module enforces, in the order they are checked:
  schema_version   the response declares schema_version 1
  stage_mismatch   the response names the request's stage
  unknown_request  the response names the request's request_id
  stale_input      every input hash in the response equals the request's, and
                   every input file on disk still hashes to the same value
  bad_items        items is a list of objects with string ids
  duplicate_ids    no id appears twice
  missing_ids      every expected id is answered (when the request expects ids)
  unknown_ids      no id outside the expected set (when the request expects ids)
  missing_stage    every stage this request depends on has been imported
                   (recorded in the ledger) — a context review cannot land
                   before its challenge stage
  item_invalid     the stage's item validator accepted every item
                   (a stage may raise its own code, e.g. quote_not_found)

A failed apply is not recorded: the ledger gains a record only after the
stage's work completes, so a crash mid-apply leaves the response importable
once the cause is fixed.

Owner approval is a separate action and lives in tools/video_project.py.
Nothing in this module reads or writes an approval, and an import never
creates one. Idempotent reimport preserves whatever approvals exist.

CLI:
  python3 tools/handoff.py validate --request X.request.json --response X.response.json
  python3 tools/handoff.py ledger <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 1
LEDGER_NAME = "handoff-imports.json"

ItemValidator = Callable[[dict], None]


class HandoffError(ValueError):
    """A request or response that cannot be used. `code` names the rule."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self), **self.details}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise HandoffError("file_missing", f"{what} not found: {path}", path=str(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HandoffError("bad_json", f"{what} is not valid JSON: {exc}", path=str(path))
    if not isinstance(data, dict):
        raise HandoffError("bad_json", f"{what} must be a JSON object", path=str(path))
    return data


# --------------------------------------------------------------------------
# Prepare
# --------------------------------------------------------------------------

@dataclass
class Request:
    path: Path
    data: dict

    @property
    def request_id(self) -> str:
        return self.data["request_id"]

    @property
    def response_path(self) -> Path:
        return response_path_for(self.path)


def request_id_for(stage: str, inputs: dict[str, dict], payload: Any) -> str:
    """Deterministic: the same stage over the same inputs and payload is the
    same request, so an identical re-prepare does not orphan a valid response."""
    basis = canonical_json({
        "stage": stage,
        "inputs": {name: spec["sha256"] for name, spec in sorted(inputs.items())},
        "payload": payload,
    })
    return sha256_text(basis)[:16]


def response_path_for(request_path: Path) -> Path:
    name = request_path.name
    if name.endswith(".request.json"):
        return request_path.with_name(name[: -len(".request.json")] + ".response.json")
    return request_path.with_name(request_path.stem + ".response.json")


def prepare(
    *,
    stage: str,
    directory: Path,
    inputs: dict[str, Path],
    payload: Any = None,
    expects_ids: Iterable[str] | None = None,
    instructions: str = "",
    slug: str = "",
    name: str | None = None,
    depends_on: Iterable["Request | dict"] = (),
) -> Request:
    """Write <directory>/<name or stage>.request.json and return it.

    `inputs` maps a role name to the file the assistant must read. Every file
    is hashed now; a response is stale if any of them changes later.
    `expects_ids`, when given, is the exact set of ids the response must
    answer. Leave it None for stages where the assistant creates the ids
    (claim extraction, card specs).
    `depends_on` lists earlier requests (or {"stage", "request_id"} dicts)
    whose responses must already be imported before this one can be. Import
    refuses with `missing_stage` otherwise. Put the earlier stage's output
    file in `inputs` as well, so a change to it makes this request stale.
    """
    dependencies = []
    for dep in depends_on:
        data = dep.data if isinstance(dep, Request) else dep
        if not isinstance(data, dict) or not data.get("stage") or not data.get("request_id"):
            raise HandoffError("bad_request", "depends_on entries need a stage and a request_id")
        dependencies.append({"stage": data["stage"], "request_id": data["request_id"]})
    if not stage or "/" in stage:
        raise HandoffError("bad_stage", "stage must be a non-empty name without slashes")
    input_specs: dict[str, dict] = {}
    for role, path in inputs.items():
        path = Path(path)
        if not path.is_file():
            raise HandoffError("file_missing", f"input {role!r} not found: {path}", path=str(path))
        input_specs[role] = {"path": str(path), "sha256": sha256_file(path)}
    payload = payload if payload is not None else {}
    expects = None
    if expects_ids is not None:
        ids = list(expects_ids)
        if len(ids) != len(set(ids)):
            raise HandoffError("duplicate_ids", "expects_ids contains duplicates")
        expects = {"ids": ids}
    data = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id_for(stage, input_specs, payload),
        "stage": stage,
        "created_at": _now(),
        "slug": slug,
        "inputs": input_specs,
        "expects": expects,
        "depends_on": dependencies,
        "payload": payload,
        "instructions": instructions,
    }
    directory = Path(directory)
    path = directory / f"{name or stage}.request.json"
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return Request(path=path, data=data)


def load_request(path: Path) -> Request:
    path = Path(path)
    data = _load_json(path, "request")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise HandoffError(
            "schema_version",
            f"request schema_version must be {SCHEMA_VERSION}",
            found=data.get("schema_version"),
        )
    for key in ("request_id", "stage", "inputs"):
        if key not in data:
            raise HandoffError("bad_request", f"request is missing {key!r}")
    return Request(path=path, data=data)


# --------------------------------------------------------------------------
# Validate
# --------------------------------------------------------------------------

def validate_response(
    request: dict,
    response: dict,
    *,
    item_validator: ItemValidator | None = None,
    check_disk: bool = True,
) -> list[dict]:
    """Return the response's items if every rule holds; raise HandoffError otherwise.

    Nothing is applied here. A stage tool calls this (or import_response, which
    calls it) before touching any artifact.
    """
    if response.get("schema_version") != SCHEMA_VERSION:
        raise HandoffError(
            "schema_version",
            f"response schema_version must be {SCHEMA_VERSION}",
            found=response.get("schema_version"),
        )
    if response.get("stage") != request["stage"]:
        raise HandoffError(
            "stage_mismatch",
            f"response is for stage {response.get('stage')!r}, request is {request['stage']!r}",
        )
    if response.get("request_id") != request["request_id"]:
        raise HandoffError(
            "unknown_request",
            "response does not name this request",
            expected=request["request_id"],
            found=response.get("request_id"),
        )

    echoed = response.get("inputs")
    if not isinstance(echoed, dict):
        raise HandoffError("stale_input", "response must echo the input hashes it was written against")
    for role, spec in request["inputs"].items():
        if echoed.get(role) != spec["sha256"]:
            raise HandoffError(
                "stale_input",
                f"response was written against a different {role!r} than this request",
                role=role,
                expected=spec["sha256"],
                found=echoed.get(role),
            )
        if check_disk:
            path = Path(spec["path"])
            current = sha256_file(path) if path.is_file() else None
            if current != spec["sha256"]:
                raise HandoffError(
                    "stale_input",
                    f"{role!r} has changed on disk since the request was prepared",
                    role=role,
                    path=str(path),
                )

    items = response.get("items")
    if not isinstance(items, list):
        raise HandoffError("bad_items", "response items must be a list")
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise HandoffError("bad_items", f"item {index} must be an object with a string id", index=index)
        if item["id"] in seen:
            raise HandoffError("duplicate_ids", f"id {item['id']!r} appears more than once", id=item["id"])
        seen.add(item["id"])

    expects = request.get("expects")
    if expects and isinstance(expects.get("ids"), list):
        expected = set(expects["ids"])
        missing = sorted(expected - seen)
        unknown = sorted(seen - expected)
        if missing:
            raise HandoffError("missing_ids", "response does not answer every expected id", ids=missing)
        if unknown:
            raise HandoffError("unknown_ids", "response answers ids the request did not ask about", ids=unknown)

    if item_validator is not None:
        for item in items:
            try:
                item_validator(item)
            except HandoffError:
                raise
            except Exception as exc:  # a stage validator's own failure is still a rejection
                raise HandoffError("item_invalid", f"item {item['id']!r}: {exc}", id=item["id"])
    return items


def find_quote(source_text: str, quote: str) -> tuple[int, int] | None:
    """Verbatim span of `quote` in `source_text`, or None. Whitespace runs
    are treated as equivalent so a wrapped line still matches."""
    if not quote or not quote.strip():
        return None
    start = source_text.find(quote)
    if start >= 0:
        return start, start + len(quote)
    # Tolerate differences in whitespace only.
    import re

    pattern = r"\s+".join(re.escape(part) for part in quote.split())
    match = re.search(pattern, source_text)
    if match:
        return match.start(), match.end()
    return None


def require_quote(source_text: str, item: dict, field: str = "quote") -> tuple[int, int]:
    """Item validator helper: the item's quote must appear verbatim in the source."""
    span = find_quote(source_text, item.get(field, ""))
    if span is None:
        raise HandoffError(
            "quote_not_found",
            f"item {item.get('id')!r}: {field} is not in the source",
            id=item.get("id"),
        )
    return span


# --------------------------------------------------------------------------
# Import (with ledger)
# --------------------------------------------------------------------------

@dataclass
class ImportResult:
    status: str  # "applied" | "already_imported"
    stage: str
    request_id: str
    response_sha256: str
    items: list[dict]
    result: Any = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "stage": self.stage,
            "request_id": self.request_id,
            "response_sha256": self.response_sha256,
            "item_count": len(self.items),
        }


def ledger_path_for(request_path: Path) -> Path:
    return Path(request_path).parent / LEDGER_NAME


def read_ledger(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("imports", []) if isinstance(data, dict) else []


def _append_ledger(path: Path, record: dict) -> None:
    imports = read_ledger(path)
    imports.append(record)
    _atomic_write(path, json.dumps({"imports": imports}, indent=2) + "\n")


def check_dependencies(request: dict, ledger: Path) -> None:
    """Every stage the request depends on must already be in the ledger."""
    entries = read_ledger(ledger)
    for dep in request.get("depends_on") or []:
        if not any(
            entry.get("stage") == dep["stage"] and entry.get("request_id") == dep["request_id"]
            for entry in entries
        ):
            raise HandoffError(
                "missing_stage",
                f"stage {dep['stage']!r} has not been imported for this run",
                stage=dep["stage"],
                request_id=dep["request_id"],
            )


def already_imported(ledger: Path, request_id: str, response_sha256: str) -> bool:
    return any(
        entry.get("request_id") == request_id and entry.get("response_sha256") == response_sha256
        for entry in read_ledger(ledger)
    )


def import_response(
    request_path: Path,
    response_path: Path | None = None,
    *,
    apply: Callable[[list[dict]], Any],
    item_validator: ItemValidator | None = None,
) -> ImportResult:
    """Validate the response against the request, then apply it exactly once.

    `apply` receives the validated items and does the stage's work: writing
    the register, rendering a report, attaching an artifact. It is not called
    when the same response was already applied to the same request. It must
    never approve anything; approvals are the owner's separate action.
    """
    request = load_request(request_path)
    response_path = Path(response_path) if response_path else request.response_path
    response = _load_json(response_path, "response")
    response_sha = sha256_text(canonical_json(response))
    ledger = ledger_path_for(request.path)

    items = validate_response(request.data, response, item_validator=item_validator)

    check_dependencies(request.data, ledger)

    if already_imported(ledger, request.request_id, response_sha):
        return ImportResult(
            status="already_imported",
            stage=request.data["stage"],
            request_id=request.request_id,
            response_sha256=response_sha,
            items=items,
        )

    result = apply(items)
    _append_ledger(ledger, {
        "stage": request.data["stage"],
        "request_id": request.request_id,
        "response_path": str(response_path),
        "response_sha256": response_sha,
        "imported_at": _now(),
    })
    return ImportResult(
        status="applied",
        stage=request.data["stage"],
        request_id=request.request_id,
        response_sha256=response_sha,
        items=items,
        result=result,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate assistant handoff files.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Check a response against its request; apply nothing")
    p_validate.add_argument("--request", required=True)
    p_validate.add_argument("--response", help="Defaults to <request>.response.json")

    p_ledger = sub.add_parser("ledger", help="List applied imports in a directory")
    p_ledger.add_argument("directory")

    args = parser.parse_args(argv)
    if args.command == "validate":
        try:
            request = load_request(Path(args.request))
            response_path = Path(args.response) if args.response else request.response_path
            response = _load_json(response_path, "response")
            items = validate_response(request.data, response)
            check_dependencies(request.data, ledger_path_for(request.path))
        except HandoffError as exc:
            print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
            return 1
        print(json.dumps({
            "status": "valid",
            "stage": request.data["stage"],
            "request_id": request.request_id,
            "item_count": len(items),
            "already_imported": already_imported(
                ledger_path_for(request.path), request.request_id, sha256_text(canonical_json(response))
            ),
        }, indent=2))
        return 0
    if args.command == "ledger":
        print(json.dumps({"imports": read_ledger(Path(args.directory) / LEDGER_NAME)}, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
