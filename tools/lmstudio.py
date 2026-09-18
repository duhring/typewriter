#!/usr/bin/env python3
"""
LM Studio helper for PKA.

Usage:
  discord-bridge/venv/bin/python3 tools/lmstudio.py models
  discord-bridge/venv/bin/python3 tools/lmstudio.py chat --prompt "Hello"
  discord-bridge/venv/bin/python3 tools/lmstudio.py embed --text "hello world"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Iterable


DEFAULT_BASE_URL = os.getenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
DEFAULT_CHAT_MODEL = os.getenv("LM_STUDIO_CHAT_MODEL", "gemma-4-e4b-it")
DEFAULT_EMBED_MODEL = os.getenv("LM_STUDIO_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
DEFAULT_TIMEOUT = float(os.getenv("LM_STUDIO_TIMEOUT", "30"))


class LMStudioError(RuntimeError):
    """Raised when LM Studio returns an error or cannot be reached."""


def _headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }
    token = os.getenv("LM_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(path: str, payload: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
    url = f"{DEFAULT_BASE_URL}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=_headers(), method=method)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LMStudioError(f"LM Studio HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LMStudioError(f"Could not reach LM Studio at {DEFAULT_BASE_URL}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LMStudioError(f"LM Studio returned invalid JSON: {body[:400]}") from exc


def list_models() -> list[dict[str, Any]]:
    data = _request("/models", method="GET")
    return data.get("data", [])


def health() -> dict[str, Any]:
    models = list_models()
    return {
        "ok": True,
        "base_url": DEFAULT_BASE_URL,
        "chat_model": DEFAULT_CHAT_MODEL,
        "embed_model": DEFAULT_EMBED_MODEL,
        "models": [item.get("id") for item in models],
    }


def embed_texts(texts: Iterable[str], model: str | None = None) -> list[list[float]]:
    items = list(texts)
    if not items:
        return []
    payload = {
        "model": model or DEFAULT_EMBED_MODEL,
        "input": items,
    }
    data = _request("/embeddings", payload=payload)
    rows = sorted(data.get("data", []), key=lambda item: item.get("index", 0))
    return [row["embedding"] for row in rows]


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model or DEFAULT_CHAT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    return _request("/chat/completions", payload=payload)


def chat_text(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 512,
) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    choices = data.get("choices", [])
    if not choices:
        raise LMStudioError("LM Studio returned no choices.")
    return choices[0].get("message", {}).get("content", "")


def _read_stdin_text() -> str:
    return sys.stdin.read().strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="LM Studio helper for PKA")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("models", help="List models")
    subparsers.add_parser("health", help="Check LM Studio reachability")

    chat_parser = subparsers.add_parser("chat", help="Send a simple chat prompt")
    chat_parser.add_argument("--prompt", help="User prompt. Reads stdin if omitted.")
    chat_parser.add_argument("--system", help="Optional system prompt")
    chat_parser.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    chat_parser.add_argument("--temperature", type=float, default=0.0)
    chat_parser.add_argument("--max-tokens", type=int, default=512)
    chat_parser.add_argument("--raw-json", action="store_true", help="Print full JSON response")

    embed_parser = subparsers.add_parser("embed", help="Generate embeddings")
    embed_parser.add_argument("--text", action="append", help="Text to embed. Reads stdin if omitted.")
    embed_parser.add_argument("--model", default=DEFAULT_EMBED_MODEL)

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
            data = chat(
                [{"role": "system", "content": args.system}] if args.system else []
                + [{"role": "user", "content": prompt}],
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
            )
            if args.raw_json:
                print(json.dumps(data, indent=2))
            else:
                choices = data.get("choices", [])
                print(choices[0].get("message", {}).get("content", "") if choices else "")
            return 0

        if args.command == "embed":
            texts = args.text if args.text else [_read_stdin_text()]
            vectors = embed_texts(texts, model=args.model)
            print(json.dumps({"model": args.model, "vectors": vectors}, indent=2))
            return 0

    except LMStudioError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
