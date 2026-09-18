#!/usr/bin/env python3
"""Report the visible PKA AI harness without reading secrets or content archives."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
CORE_FILES = [ROOT / "CLAUDE.md", ROOT / "AGENTS.md", ROOT / "llms.txt"]
WORD_RE = re.compile(r"\b[\w'-]+\b")


def file_stats(path: Path) -> dict:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return {
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "exists": path.exists(),
        "characters": len(text),
        "words": len(WORD_RE.findall(text)),
        "lines": len(text.splitlines()),
    }


def count_files(root: Path, pattern: str) -> int:
    return sum(1 for _ in root.glob(pattern)) if root.exists() else 0


def duplicate_paragraphs() -> list[dict]:
    seen: dict[str, list[str]] = defaultdict(list)
    for path in CORE_FILES:
        if not path.exists():
            continue
        for paragraph in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")):
            normalized = re.sub(r"\s+", " ", paragraph.strip().lower())
            if len(normalized) >= 100:
                seen[normalized].append(path.name)
    return [
        {"files": files, "preview": paragraph[:120]}
        for paragraph, files in seen.items()
        if len(set(files)) > 1
    ]


def enabled_codex_plugins() -> list[str]:
    config = HOME / ".codex" / "config.toml"
    if not config.exists():
        return []
    try:
        import tomllib

        data = tomllib.loads(config.read_text(encoding="utf-8"))
        return sorted(
            name for name, spec in data.get("plugins", {}).items()
            if spec.get("enabled") is True
        )
    except Exception:
        return []


def build_report() -> dict:
    stats = {path.name: file_stats(path) for path in CORE_FILES}
    routes = {
        "claude_core_words": stats["CLAUDE.md"]["words"],
        "codex_core_words": stats["AGENTS.md"]["words"] + stats["llms.txt"]["words"],
    }
    warnings = []
    for name, words in routes.items():
        if words > 2000:
            warnings.append(f"{name} exceeds the 2,000-word core budget ({words}).")

    duplicates = duplicate_paragraphs()
    if duplicates:
        warnings.append(f"{len(duplicates)} long paragraph(s) are duplicated across core files.")

    tasks = ROOT / "owners-inbox" / "tasks"
    discord = ROOT / "team-inbox" / "discord"
    processed = discord / "processed"
    return {
        "core_files": list(stats.values()),
        "routes": routes,
        "duplicates": duplicates,
        "inventory": {
            "project_specialists": count_files(ROOT / ".claude" / "agents", "*.md"),
            "procedure_docs": count_files(ROOT / "docs" / "larry", "*.md"),
            "active_task_files": count_files(tasks, "*"),
            "discord_unprocessed_top_level": sum(
                1 for p in discord.iterdir() if p.is_file() and not p.name.startswith(".")
            ) if discord.exists() else 0,
            "discord_processed_files": count_files(processed, "*"),
            "global_claude_agents": count_files(HOME / ".claude" / "agents", "**/*.md"),
            "global_claude_skills": count_files(HOME / ".claude" / "skills", "**/SKILL.md"),
            "codex_plugin_skills": count_files(HOME / ".codex" / "plugins" / "cache", "**/SKILL.md"),
            "enabled_codex_plugins": enabled_codex_plugins(),
            "codex_approval_rules": sum(
                1 for line in (HOME / ".codex" / "rules" / "default.rules").read_text(
                    encoding="utf-8"
                ).splitlines() if line.strip().startswith("prefix_rule(")
            ) if (HOME / ".codex" / "rules" / "default.rules").exists() else 0,
        },
        "warnings": warnings,
        "status": "attention_needed" if warnings else "ok",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Harness audit: {report['status']}")
        for route, words in report["routes"].items():
            print(f"  {route}: {words} words")
        inv = report["inventory"]
        print(
            "  inventory: "
            f"{inv['project_specialists']} project specialists, "
            f"{len(inv['enabled_codex_plugins'])} enabled Codex plugins, "
            f"{inv['discord_unprocessed_top_level']} unprocessed Discord files"
        )
        for warning in report["warnings"]:
            print(f"  WARN: {warning}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
