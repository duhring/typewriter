#!/usr/bin/env python3
"""Install GLM_API_KEY and select GLM in discord-bridge/.env.

Usage:
  1. Copy the new Z.AI/GLM API key.
  2. Without copying anything else, run:
     discord-bridge/venv/bin/python3 tools/rotate_glm_key.py

The key is never printed. The .env file is restricted to its owner.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ENV_NAME = "GLM_API_KEY"
PROVIDER_NAME = "LLM_PROVIDER"
PROVIDER_VALUE = "glm"
ENV_PATH = Path(__file__).resolve().parent.parent / "discord-bridge" / ".env"


def _clipboard_key() -> str:
    raw = subprocess.check_output(["pbpaste"]).decode("utf-8")
    key = raw.strip()
    if not 20 <= len(key) <= 300:
        raise ValueError(f"clipboard does not look like an API key (length={len(key)})")
    if any(character.isspace() for character in key):
        raise ValueError("clipboard API key contains whitespace")
    if any(ord(character) < 33 or ord(character) > 126 for character in key):
        raise ValueError("clipboard API key contains non-printable characters")
    return key


def _set_env_value(lines: list[str], name: str, value: str) -> list[str]:
    replacement = f"{name}={value}"
    output: list[str] = []
    replaced = False

    for line in lines:
        if line.startswith(f"{name}="):
            if not replaced:
                output.append(replacement)
                replaced = True
        else:
            output.append(line)

    if not replaced:
        if output and output[-1]:
            output.append("")
        output.append(replacement)
    return output


def _updated_env(contents: str, key: str) -> str:
    lines = contents.splitlines()
    lines = _set_env_value(lines, ENV_NAME, key)
    lines = _set_env_value(lines, PROVIDER_NAME, PROVIDER_VALUE)
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        key = _clipboard_key()
        original = ENV_PATH.read_text(encoding="utf-8")
        updated = _updated_env(original, key)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=ENV_PATH.parent,
            prefix=f".{ENV_PATH.name}.",
            delete=False,
        ) as handle:
            handle.write(updated)
            temporary_path = Path(handle.name)

        os.chmod(temporary_path, 0o600)
        temporary_path.replace(ENV_PATH)
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        print(f"Could not update {ENV_NAME}: {exc}", file=sys.stderr)
        return 1

    print(
        f"{ENV_NAME} updated securely (length={len(key)}); "
        f"{PROVIDER_NAME}={PROVIDER_VALUE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
