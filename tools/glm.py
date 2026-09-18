#!/usr/bin/env python3
"""
GLM (Zhipu AI / Z.AI) helper for PKA.

Usage:
  discord-bridge/venv/bin/python3 tools/glm.py health
  discord-bridge/venv/bin/python3 tools/glm.py chat --prompt "ping"
  discord-bridge/venv/bin/python3 tools/glm.py chat --prompt '{"q":1}' --json

Env:
  GLM_API_KEY        required
  GLM_BASE_URL       default https://api.z.ai/api/paas/v4
  GLM_CHAT_MODEL     default glm-5.2
  GLM_TIMEOUT        default 180
  GLM_USAGE_LOG      default tools/glm_usage.jsonl (set "" to disable)
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


DEFAULT_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/paas/v4").rstrip("/")
DEFAULT_CHAT_MODEL = os.getenv("GLM_CHAT_MODEL", "glm-5.2")
DEFAULT_TIMEOUT = float(os.getenv("GLM_TIMEOUT", "180"))
DEFAULT_USAGE_LOG = os.getenv(
    "GLM_USAGE_LOG",
    str(Path(__file__).resolve().parent / "glm_usage.jsonl"),
)

# Approximate per-million-token pricing. Used for cost-sanity logging.
PRICING = {
    "glm-5.2": {"in": 1.40, "out": 4.40, "cache_read": 0.26},
    # Claude reference for the "would have cost" comparison.
    "claude-opus-4-7": {"in": 15.00, "out": 75.00},
}


class GLMError(RuntimeError):
    """Raised when GLM API returns an error or cannot be reached."""


def _api_key() -> str:
    key = os.getenv("GLM_API_KEY", "").strip()
    if not key:
        raise GLMError("GLM_API_KEY is not set in the environment.")
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
        raise GLMError(f"GLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GLMError(f"Could not reach GLM at {DEFAULT_BASE_URL}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GLMError(
            f"GLM request timed out after {DEFAULT_TIMEOUT}s."
        ) from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise GLMError(f"GLM returned invalid JSON: {body[:400]}") from exc


def list_models() -> list[dict[str, Any]]:
    # Standard OpenAI models endpoint
    data = _request("/models", method="GET")
    return data.get("data", [])


def health() -> dict[str, Any]:
    try:
        models = list_models()
        model_list = [item.get("id") for item in models]
    except Exception as exc:
        model_list = [f"Error listing: {exc}"]
    return {
        "ok": True,
        "base_url": DEFAULT_BASE_URL,
        "chat_model": DEFAULT_CHAT_MODEL,
        "models": model_list,
    }


def _log_usage(model: str, usage: dict[str, Any], label: str) -> None:
    if not DEFAULT_USAGE_LOG:
        return
    in_tok = int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or 0)
    
    # Handle GLM prompt caching if available
    cached_tok = int(usage.get("prompt_tokens_details", {}).get("cached_tokens") or 0)
    uncached_tok = max(0, in_tok - cached_tok)

    price = PRICING.get(model, {"in": 1.40, "out": 4.40, "cache_read": 0.26})
    glm_cost = (uncached_tok * price["in"] + cached_tok * price["cache_read"] + out_tok * price["out"]) / 1_000_000
    
    claude = PRICING["claude-opus-4-7"]
    claude_cost = (in_tok * claude["in"] + out_tok * claude["out"]) / 1_000_000
    
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "label": label,
        "model": model,
        "in_tokens": in_tok,
        "cached_tokens": cached_tok,
        "out_tokens": out_tok,
        "glm_usd": round(glm_cost, 6),
        "claude_opus_usd_equiv": round(claude_cost, 6),
        "savings_usd": round(claude_cost - glm_cost, 6),
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
        raise GLMError("GLM returned no choices.")
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
        raise GLMError("GLM returned no choices.")
    raw = choices[0].get("message", {}).get("content", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GLMError(f"GLM did not return valid JSON: {raw[:400]}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="GLM (Zhipu AI) helper for PKA")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("models", help="List GLM models")
    sub.add_parser("health", help="Check GLM reachability and config")

    chat_p = sub.add_parser("chat", help="Send a chat prompt")
    chat_p.add_argument("--prompt", help="User prompt. Reads stdin if omitted.")
    chat_p.add_argument("--system", help="Optional system prompt")
    chat_p.add_argument("--model", default=None, help="Override model id")
    chat_p.add_argument("--temperature", type=float, default=0.0)
    chat_p.add_argument("--max-tokens", type=int, default=1024)
    chat_p.add_argument("--json", dest="want_json", action="store_true",
                        help="Request JSON output and parse it")
    chat_p.add_argument("--raw", action="store_true", help="Print full JSON response")

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
            model = args.model or DEFAULT_CHAT_MODEL
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

    except GLMError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


def _read_stdin_text() -> str:
    return sys.stdin.read().strip()


if __name__ == "__main__":
    raise SystemExit(main())
