#!/usr/bin/env python3
"""Track repeated PKA health findings and expose attended escalations.

State is tool-owned JSON under data/healthchecks/state/. Findings are counted
at most once per calendar day, deduplicated by a stable fingerprint, and
automatically resolved when the corresponding check returns clean.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path


PKA_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PKA_ROOT / "data" / "healthchecks" / "state" / "recurrence.json"
DEFAULT_THRESHOLD = 3


def _empty_state() -> dict:
    return {"version": 1, "findings": {}}


def load_state(path: Path = STATE_PATH) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload.get("findings"), dict):
            return payload
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return _empty_state()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _identity(check: dict) -> dict:
    identity = {"check": check.get("check", "unknown")}
    offenders = check.get("offenders")
    if offenders:
        identity["offenders"] = offenders
    return identity


def finding_fingerprint(check: dict) -> str:
    encoded = json.dumps(_identity(check), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def update_recurrence(
    health_payload: dict,
    *,
    state_path: Path = STATE_PATH,
    observed_at: datetime | None = None,
    threshold: int = DEFAULT_THRESHOLD,
) -> dict:
    """Persist one observation and return the updated recurrence state."""
    observed = (observed_at or datetime.now().astimezone()).astimezone()
    observed_day = observed.date().isoformat()
    observed_stamp = observed.isoformat(timespec="seconds")
    state = load_state(state_path)
    findings = state.setdefault("findings", {})
    active_by_check: dict[str, set[str]] = {}
    seen_checks: set[str] = set()

    for check in health_payload.get("checks", []):
        check_name = str(check.get("check", "unknown"))
        seen_checks.add(check_name)
        if check.get("status") not in {"warn", "fail"}:
            active_by_check.setdefault(check_name, set())
            continue
        fingerprint = finding_fingerprint(check)
        active_by_check.setdefault(check_name, set()).add(fingerprint)
        item = findings.get(fingerprint)
        if item is None or item.get("state") == "resolved":
            item = {
                "fingerprint": fingerprint,
                "check": check_name,
                "first_seen": observed_stamp,
                "last_seen": observed_stamp,
                "last_observed_date": observed_day,
                "consecutive_runs": 1,
                "severity": check.get("status", "warn"),
                "detail": check.get("detail", ""),
                "state": "open",
                "escalated_at": None,
                "snoozed_until": None,
                "resolved_at": None,
            }
            findings[fingerprint] = item
        else:
            if item.get("last_observed_date") != observed_day:
                item["consecutive_runs"] = int(item.get("consecutive_runs", 0)) + 1
            item.update(
                {
                    "last_seen": observed_stamp,
                    "last_observed_date": observed_day,
                    "severity": check.get("status", "warn"),
                    "detail": check.get("detail", ""),
                }
            )
        if item["consecutive_runs"] >= threshold and item.get("state") == "open":
            item["state"] = "escalated"
            item["escalated_at"] = observed_stamp

    # A clean result resolves every prior fingerprint for that specific check.
    for fingerprint, item in findings.items():
        check_name = item.get("check")
        if check_name not in seen_checks or item.get("state") == "resolved":
            continue
        if fingerprint not in active_by_check.get(check_name, set()):
            item["state"] = "resolved"
            item["resolved_at"] = observed_stamp

    state["updated_at"] = observed_stamp
    _atomic_write_json(state_path, state)
    return state


def list_escalations(
    *, state_path: Path = STATE_PATH, on_date: date | None = None, include_acknowledged: bool = False
) -> list[dict]:
    today = on_date or date.today()
    visible = []
    for item in load_state(state_path).get("findings", {}).values():
        state = item.get("state")
        if state == "acknowledged" and not include_acknowledged:
            continue
        if state == "snoozed":
            until = item.get("snoozed_until")
            if until and until >= today.isoformat():
                continue
            item["state"] = "escalated"
        if item.get("state") == "escalated":
            visible.append(dict(item))
    return sorted(
        visible,
        key=lambda item: (
            0 if item.get("severity") == "fail" else 1,
            -int(item.get("consecutive_runs", 0)),
            item.get("check", ""),
        ),
    )


def set_disposition(
    fingerprint: str,
    disposition: str,
    *,
    state_path: Path = STATE_PATH,
    snoozed_until: str | None = None,
) -> dict:
    state = load_state(state_path)
    matches = [key for key in state["findings"] if key.startswith(fingerprint)]
    if not matches:
        raise ValueError(f"unknown finding fingerprint: {fingerprint}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous finding fingerprint prefix: {fingerprint}")
    item = state["findings"][matches[0]]
    if disposition == "snoozed":
        if not snoozed_until:
            raise ValueError("snoozed disposition requires --until YYYY-MM-DD")
        date.fromisoformat(snoozed_until)
        item["snoozed_until"] = snoozed_until
    else:
        item["snoozed_until"] = None
    item["state"] = disposition
    item["disposition_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _atomic_write_json(state_path, state)
    return dict(item)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage repeated PKA health escalations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List escalated findings visible to the owner")
    p_ack = sub.add_parser("acknowledge", help="Acknowledge one finding")
    p_ack.add_argument("fingerprint", help="Full fingerprint or unique prefix")
    p_snooze = sub.add_parser("snooze", help="Snooze one finding through a date")
    p_snooze.add_argument("fingerprint", help="Full fingerprint or unique prefix")
    p_snooze.add_argument("--until", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    if args.command == "list":
        print(json.dumps(list_escalations(), indent=2))
    elif args.command == "acknowledge":
        print(json.dumps(set_disposition(args.fingerprint, "acknowledged"), indent=2))
    elif args.command == "snooze":
        print(
            json.dumps(
                set_disposition(args.fingerprint, "snoozed", snoozed_until=args.until),
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
