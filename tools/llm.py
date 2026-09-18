#!/usr/bin/env python3
"""
PKA provider router for chat-completions LLM calls.

A thin dispatcher over the existing tools/xai.py and tools/lmstudio.py
helpers. Lets callers stop hardwiring a provider — they pick one
explicitly or fall back to the LLM_PROVIDER env var.

Public API:
  chat_text(prompt, *, system=None, provider=None, model=None,
            temperature=0.0, max_tokens=1024, label="llm.chat_text") -> str
  chat_json(prompt, *, system=None, provider=None, model=None,
            temperature=0.0, max_tokens=1024, label="llm.chat_json") -> Any

Providers:
  xai       — Grok (cheap builder lane). Requires XAI_API_KEY.
  lmstudio  — local model, OpenAI-compatible. Requires LM Studio running.

Env vars:
  LLM_PROVIDER         default provider when caller does not specify
                       (default: "lmstudio" to preserve existing tool behavior)
  LLM_FALLBACK         optional provider to try if the primary raises
                       (default: unset — no fallback)

Out of scope for v1:
  - Embeddings (xAI chat-compat does not imply embed-compat)
  - OpenAI / Anthropic providers
  - Streaming
  - Tool/function calling

CLI:
  discord-bridge/venv/bin/python3 tools/llm.py chat --provider xai --prompt "ping"
  discord-bridge/venv/bin/python3 tools/llm.py chat --provider lmstudio --prompt "ping"
  echo '{"q":1}' | discord-bridge/venv/bin/python3 tools/llm.py chat --provider xai --json
  discord-bridge/venv/bin/python3 tools/llm.py providers
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import glm  # type: ignore
import lmstudio  # type: ignore
import xai  # type: ignore


SUPPORTED_PROVIDERS = ("xai", "lmstudio", "glm")


class LLMError(RuntimeError):
    """Raised when the chosen provider errors and no fallback succeeds."""


def default_provider() -> str:
    name = (os.getenv("LLM_PROVIDER") or "lmstudio").strip().lower()
    if name not in SUPPORTED_PROVIDERS:
        raise LLMError(f"LLM_PROVIDER={name!r} is not one of {SUPPORTED_PROVIDERS}")
    return name


def _fallback_provider() -> str | None:
    name = (os.getenv("LLM_FALLBACK") or "").strip().lower()
    if not name:
        return None
    if name not in SUPPORTED_PROVIDERS:
        raise LLMError(f"LLM_FALLBACK={name!r} is not one of {SUPPORTED_PROVIDERS}")
    return name


def _resolve(provider: str | None) -> str:
    name = (provider or default_provider()).strip().lower()
    if name not in SUPPORTED_PROVIDERS:
        raise LLMError(f"unknown provider: {name!r} (supported: {SUPPORTED_PROVIDERS})")
    return name


def chat(
    messages: list[dict[str, str]],
    *,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    response_format: dict[str, Any] | None = None,
    label: str = "llm.chat",
) -> dict[str, Any]:
    """Raw chat completion. Returns the provider's full response dict."""
    name = _resolve(provider)
    try:
        if name == "glm":
            return glm.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                label=label,
            )
        if name == "xai":
            return xai.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                label=label,
            )
        if name == "lmstudio":
            return lmstudio.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
    except Exception as primary_exc:
        fb = _fallback_provider() if provider is None else None
        if not fb or fb == name:
            raise
        return chat(
            messages,
            provider=fb,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            label=f"{label}.fallback",
        )
    raise LLMError(f"unknown provider: {name}")


def _call_text(provider: str, prompt: str, *, system, model, temperature, max_tokens, label) -> str:
    if provider == "glm":
        return glm.chat_text(
            prompt,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            label=label,
        )
    if provider == "xai":
        return xai.chat_text(
            prompt,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            label=label,
        )
    if provider == "lmstudio":
        return lmstudio.chat_text(
            prompt,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    raise LLMError(f"unknown provider: {provider}")


def chat_text(
    prompt: str,
    *,
    system: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    label: str = "llm.chat_text",
) -> str:
    primary = _resolve(provider)
    try:
        return _call_text(primary, prompt, system=system, model=model,
                          temperature=temperature, max_tokens=max_tokens, label=label)
    except Exception as primary_exc:
        fb = _fallback_provider() if provider is None else None
        if not fb or fb == primary:
            raise
        try:
            return _call_text(fb, prompt, system=system, model=model,
                              temperature=temperature, max_tokens=max_tokens,
                              label=f"{label}.fallback")
        except Exception as fb_exc:
            raise LLMError(
                f"both providers failed: primary={primary} ({primary_exc!r}); "
                f"fallback={fb} ({fb_exc!r})"
            ) from fb_exc


def chat_json(
    prompt: str,
    *,
    system: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    label: str = "llm.chat_json",
) -> Any:
    name = _resolve(provider)
    if name == "glm":
        try:
            return glm.chat_json(
                prompt,
                system=system,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                label=label,
            )
        except Exception:
            fb = _fallback_provider() if provider is None else None
            if not fb or fb == name:
                raise
            return chat_json(
                prompt,
                system=system,
                provider=fb,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                label=f"{label}.fallback",
            )
    if name == "xai":
        try:
            return xai.chat_json(
                prompt,
                system=system,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                label=label,
            )
        except Exception:
            fb = _fallback_provider() if provider is None else None
            if not fb or fb == name:
                raise
            return chat_json(
                prompt,
                system=system,
                provider=fb,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                label=f"{label}.fallback",
            )

    # lmstudio: build response_format, parse manually (matches xai.chat_json shape)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = lmstudio.chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=None,
    )
    choices = data.get("choices", [])
    if not choices:
        raise LLMError("lmstudio returned no choices")
    raw = choices[0].get("message", {}).get("content", "").strip()
    
    # Robustly strip markdown json block formatting if returned by model
    clean_raw = raw
    if clean_raw.startswith("```"):
        first_nl = clean_raw.find("\n")
        if first_nl != -1:
            clean_raw = clean_raw[first_nl:].strip()
        if clean_raw.endswith("```"):
            clean_raw = clean_raw[:-3].strip()
            
    try:
        return json.loads(clean_raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"lmstudio did not return valid JSON: {raw[:400]}") from exc


def _read_stdin_text() -> str:
    return sys.stdin.read().strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="PKA provider router for chat completions")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("providers", help="List supported providers and current default")

    chat_p = sub.add_parser("chat", help="Send a chat prompt")
    chat_p.add_argument("--prompt", help="User prompt (reads stdin if omitted)")
    chat_p.add_argument("--system", help="Optional system prompt")
    chat_p.add_argument("--provider", choices=SUPPORTED_PROVIDERS, help="Override LLM_PROVIDER")
    chat_p.add_argument("--model", default=None)
    chat_p.add_argument("--temperature", type=float, default=0.0)
    chat_p.add_argument("--max-tokens", type=int, default=1024)
    chat_p.add_argument("--json", dest="want_json", action="store_true",
                        help="Request JSON output and parse it")

    args = parser.parse_args()
    try:
        if args.command == "providers":
            print(json.dumps({
                "supported": list(SUPPORTED_PROVIDERS),
                "default": default_provider(),
                "fallback": _fallback_provider(),
            }, indent=2))
            return 0

        if args.command == "chat":
            prompt = args.prompt if args.prompt is not None else _read_stdin_text()
            kwargs = dict(
                system=args.system,
                provider=args.provider,
                model=args.model,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                label="cli:llm.chat",
            )
            if args.want_json:
                print(json.dumps(chat_json(prompt, **kwargs), indent=2))
            else:
                print(chat_text(prompt, **kwargs))
            return 0

    except LLMError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"{exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
