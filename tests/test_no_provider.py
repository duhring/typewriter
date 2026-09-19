"""The starter ships no model provider: no router, no clients, no keys, no
local model server, and nothing that references them.

Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
REMOVED_MODULES = ("llm", "lmstudio", "xai", "glm", "rotate_glm_key", "promote_sop", "generate_thumbnail")
REMOVED_SETTINGS = ("LLM_PROVIDER", "LLM_FALLBACK", "XAI_API_KEY", "GLM_API_KEY", "OPENAI_API_KEY", "LM_STUDIO_BASE_URL")
SKIP_DIRS = {".git", "venv", "__pycache__", "node_modules", "tests", "data"}
SKIP_FILES = {"docs/plan-assistant-neutral-starter.md"}
# Vendored reference material for an optional external tool (HyperFrames); not PKA's own.
SKIP_PREFIXES = (".claude/skills/hyperframes", ".claude/skills/hyperframes-cli", ".claude/skills/hyperframes-registry",
                 ".claude/skills/website-to-hyperframes", ".claude/skills/gsap")


def repo_files(suffixes=(".py", ".md", ".json", ".sh", ".example", ".txt")):
    for path in PKA_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes and path.name not in ("Brewfile", ".env.example"):
            continue
        rel = path.relative_to(PKA_ROOT)
        if any(part in SKIP_DIRS for part in rel.parts) or str(rel) in SKIP_FILES:
            continue
        if str(rel).startswith(SKIP_PREFIXES):
            continue
        yield rel, path


class TestNoProvider(unittest.TestCase):
    def test_provider_modules_are_gone(self):
        for name in REMOVED_MODULES:
            self.assertFalse((PKA_ROOT / "tools" / f"{name}.py").exists(), name)
        self.assertFalse((PKA_ROOT / "docs" / "larry" / "llm-providers.md").exists())

    def test_nothing_imports_or_configures_a_provider(self):
        import_pattern = re.compile(r"^\s*(?:import|from)\s+(?:llm|lmstudio|xai|glm)\b", re.M)
        offenders = []
        for rel, path in repo_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix == ".py" and import_pattern.search(text):
                offenders.append(f"{rel}: provider import")
            for setting in REMOVED_SETTINGS:
                if setting in text:
                    offenders.append(f"{rel}: {setting}")
            for word in ("llm-providers.md", "promote_sop", "rotate_glm_key", "ollama"):
                if word in text.lower():
                    offenders.append(f"{rel}: {word}")
        self.assertEqual(offenders, [])

    def test_bootstrap_and_config_mention_no_model_service(self):
        bootstrap = (PKA_ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        self.assertNotIn("ollama", bootstrap.lower())
        self.assertIn("installs no model service", bootstrap)
        machine = (PKA_ROOT / "config" / "machine.json").read_text(encoding="utf-8")
        self.assertNotIn("ollama", machine)
        env_example = (PKA_ROOT / "discord-bridge" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("No model keys", env_example)


if __name__ == "__main__":
    unittest.main()
