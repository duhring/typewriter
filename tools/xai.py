#!/usr/bin/env python3
"""
xAI (Grok) helper for PKA — coding-draft sidecar.

The "Grok Builder Lane": cheap first-pass drafts/plans/reviews. Larry
(Claude) stays the arbitrator and decides whether to apply anything
this returns.

Usage:
  discord-bridge/venv/bin/python3 tools/xai.py health
  discord-bridge/venv/bin/python3 tools/xai.py chat --prompt "ping"
  discord-bridge/venv/bin/python3 tools/xai.py chat --prompt '{"q":1}' --json
  discord-bridge/venv/bin/python3 tools/xai.py draft --file path/to/file.py \
      --task "draft a patch that ..." --kind patch

Env:
  XAI_API_KEY        required
  XAI_BASE_URL       default https://api.x.ai/v1
  XAI_CHAT_MODEL     default grok-code-fast-1
  XAI_REASON_MODEL   default grok-4.3 (used with --reason)
  XAI_TIMEOUT        default 180
  XAI_USAGE_LOG      default tools/xai_usage.jsonl (set "" to disable)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv  # type: ignore
    _ENV_PATH = Path(__file__).resolve().parent.parent / "discord-bridge" / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH)
except ImportError:
    pass


DEFAULT_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
DEFAULT_CHAT_MODEL = os.getenv("XAI_CHAT_MODEL", "grok-code-fast-1")
DEFAULT_REASON_MODEL = os.getenv("XAI_REASON_MODEL", "grok-4.3")
DEFAULT_TIMEOUT = float(os.getenv("XAI_TIMEOUT", "180"))
DEFAULT_USAGE_LOG = os.getenv(
    "XAI_USAGE_LOG",
    str(Path(__file__).resolve().parent / "xai_usage.jsonl"),
)

# Approximate per-million-token pricing. Used only for cost-sanity logging.
PRICING = {
    "grok-code-fast-1": {"in": 0.20, "out": 1.50},
    "grok-4.3": {"in": 1.25, "out": 2.50},
    # Claude reference for the "would have cost" comparison column.
    "claude-opus-4-7": {"in": 15.00, "out": 75.00},
}


class XAIError(RuntimeError):
    """Raised when xAI returns an error or cannot be reached."""


def _api_key() -> str:
    key = os.getenv("XAI_API_KEY", "").strip()
    if not key:
        raise XAIError("XAI_API_KEY is not set in the environment.")
    return key


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_api_key()}",
    }


def _request(path: str, payload: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
    url = f"{DEFAULT_BASE_URL}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise XAIError(f"xAI HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise XAIError(f"Could not reach xAI at {DEFAULT_BASE_URL}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise XAIError(
            f"xAI request timed out after {DEFAULT_TIMEOUT}s. "
            f"Set XAI_TIMEOUT to a larger value if generations are long."
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise XAIError(f"xAI returned invalid JSON: {body[:400]}") from exc


def list_models() -> list[dict[str, Any]]:
    data = _request("/models", method="GET")
    return data.get("data", [])


def health() -> dict[str, Any]:
    models = list_models()
    return {
        "ok": True,
        "base_url": DEFAULT_BASE_URL,
        "chat_model": DEFAULT_CHAT_MODEL,
        "reason_model": DEFAULT_REASON_MODEL,
        "models": [item.get("id") for item in models],
    }


def _log_usage(model: str, usage: dict[str, Any], label: str) -> None:
    if not DEFAULT_USAGE_LOG:
        return
    in_tok = int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or 0)
    price = PRICING.get(model, {"in": 0.0, "out": 0.0})
    grok_cost = (in_tok * price["in"] + out_tok * price["out"]) / 1_000_000
    claude = PRICING["claude-opus-4-7"]
    claude_cost = (in_tok * claude["in"] + out_tok * claude["out"]) / 1_000_000
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "label": label,
        "model": model,
        "in_tokens": in_tok,
        "out_tokens": out_tok,
        "grok_usd": round(grok_cost, 6),
        "claude_opus_usd_equiv": round(claude_cost, 6),
        "savings_usd": round(claude_cost - grok_cost, 6),
    }
    try:
        with open(DEFAULT_USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    response_format: dict[str, Any] | None = None,
    label: str = "chat",
) -> dict[str, Any]:
    chosen = model or DEFAULT_CHAT_MODEL
    payload: dict[str, Any] = {
        "model": chosen,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    data = _request("/chat/completions", payload=payload)
    _log_usage(chosen, data.get("usage", {}) or {}, label)
    return data


def chat_text(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    label: str = "chat_text",
) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = chat(messages, model=model, temperature=temperature, max_tokens=max_tokens, label=label)
    choices = data.get("choices", [])
    if not choices:
        raise XAIError("xAI returned no choices.")
    return choices[0].get("message", {}).get("content", "")


def chat_json(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    label: str = "chat_json",
) -> Any:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        label=label,
    )
    choices = data.get("choices", [])
    if not choices:
        raise XAIError("xAI returned no choices.")
    raw = choices[0].get("message", {}).get("content", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise XAIError(f"xAI did not return valid JSON: {raw[:400]}") from exc


DRAFT_SYSTEMS = {
    "patch": (
        "You are a coding assistant producing a unified diff patch. "
        "Output ONLY the patch in standard `diff --git` format. No prose, "
        "no fences. The reviewer (Claude) will decide whether to apply it."
    ),
    "plan": (
        "You are a coding assistant producing a concise implementation plan. "
        "Use short sections: Goal, Key files, Steps, Risks. No code blocks "
        "longer than ~10 lines. The reviewer (Claude) arbitrates."
    ),
    "review": (
        "You are a coding assistant producing a focused code review. "
        "List concrete issues with file:line references and severity "
        "(blocker / nit). Skip generic praise."
    ),
}


def draft(file_path: str | None, task: str, kind: str = "patch", reason: bool = False) -> str:
    system = DRAFT_SYSTEMS.get(kind)
    if not system:
        raise XAIError(f"Unknown draft kind: {kind}. Use one of {list(DRAFT_SYSTEMS)}.")
    parts = [f"Task:\n{task.strip()}"]
    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise XAIError(f"File not found: {file_path}")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise XAIError(f"Could not read {file_path}: {exc}") from exc
        parts.append(f"File: {file_path}\n```\n{content}\n```")
    prompt = "\n\n".join(parts)
    model = DEFAULT_REASON_MODEL if reason else DEFAULT_CHAT_MODEL
    return chat_text(
        prompt,
        system=system,
        model=model,
        max_tokens=2048,
        label=f"draft:{kind}",
    )


def _read_stdin_text() -> str:
    return sys.stdin.read().strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="xAI (Grok) helper for PKA")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="List xAI models")
    sub.add_parser("health", help="Check xAI reachability and config")

    chat_p = sub.add_parser("chat", help="Send a chat prompt")
    chat_p.add_argument("--prompt", help="User prompt. Reads stdin if omitted.")
    chat_p.add_argument("--system", help="Optional system prompt")
    chat_p.add_argument("--model", default=None, help="Override model id")
    chat_p.add_argument("--reason", action="store_true", help="Use the reasoning model (grok-4.3)")
    chat_p.add_argument("--temperature", type=float, default=0.0)
    chat_p.add_argument("--max-tokens", type=int, default=1024)
    chat_p.add_argument("--json", dest="want_json", action="store_true",
                        help="Request JSON output and parse it")
    chat_p.add_argument("--raw", action="store_true", help="Print full JSON response")

    draft_p = sub.add_parser("draft", help="Ask Grok to draft a patch / plan / review")
    draft_p.add_argument("--file", help="File to include as context (optional)")
    draft_p.add_argument("--task", required=True, help="What to do")
    draft_p.add_argument("--kind", choices=list(DRAFT_SYSTEMS), default="patch")
    draft_p.add_argument("--reason", action="store_true", help="Use grok-4.3 instead of grok-code-fast-1")

    args = parser.parse_args()

    try:
        if args.command == "models":
            print(json.dumps({"models": list_models()}, indent=2))
            return 0

        if args.command == "health":
            print(json.dumps(health(), indent=2))
            return 0

        if args.command == "chat":
            prompt = args.prompt if args.prompt is not None else _read_stdin_text()
            model = args.model or (DEFAULT_REASON_MODEL if args.reason else DEFAULT_CHAT_MODEL)
            if args.want_json:
                result = chat_json(
                    prompt,
                    system=args.system,
                    model=model,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                )
                print(json.dumps(result, indent=2))
                return 0
            messages: list[dict[str, str]] = []
            if args.system:
                messages.append({"role": "system", "content": args.system})
            messages.append({"role": "user", "content": prompt})
            data = chat(
                messages,
                model=model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                label="cli:chat",
            )
            if args.raw:
                print(json.dumps(data, indent=2))
            else:
                choices = data.get("choices", [])
                print(choices[0].get("message", {}).get("content", "") if choices else "")
            return 0

        if args.command == "draft":
            print(draft(args.file, args.task, kind=args.kind, reason=args.reason))
            return 0

    except XAIError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
