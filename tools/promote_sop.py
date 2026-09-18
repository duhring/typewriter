#!/usr/bin/env python3
"""
Promote durable archive learnings into candidate SOP/playbook updates.

This tool reviews recent high-signal PKA artifacts, asks the local model to
identify repeatable operational patterns, saves a promotion report to
owners-inbox/sop-promotions/, and indexes that report into PKA.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("LM_STUDIO_TIMEOUT", "90")

import lmstudio
from pka_index import index_markdown_artifact


PKA_ROOT = Path(__file__).resolve().parent.parent
OWNERS_INBOX = PKA_ROOT / "owners-inbox"
SOP_PROMOTION_DIR = OWNERS_INBOX / "sop-promotions"

SOURCE_PATTERNS = [
    "owners-inbox/*.md",
    "owners-inbox/session-logs/*.md",
    "owners-inbox/meeting-extracts/*.md",
    "owners-inbox/transcript-extracts/*.md",
    "owners-inbox/blog/*.md",
    "owners-inbox/youtube/*.md",
    "owners-inbox/research*.md",
    "owners-inbox/research/*.md",
]

TARGET_DOCS = {
    "CLAUDE.md": "Always-loaded core orchestration rules and routing.",
    "docs/larry/operations.md": "Inbox handling, KB maintenance, session loops, Dreamer, and operating defaults.",
    "docs/larry/google-tools.md": "Calendar, brief, Docs, Sheets, and Maps workflows.",
    "docs/larry/youtube-writing.md": "YouTube packaging and blog-writing procedures.",
    "docs/larry/cuecam.md": "CueCam deck generation and Discord media workflows.",
    "docs/larry/journaling.md": "Journaling, contacts, glossary, and confirmation behavior.",
    "docs/larry/sonoma-receipts.md": "Sonoma receipts handling and ledger rules.",
    ".claude/agents/dreamer.md": "Dreamer memory curation and archive refinement instructions.",
    ".claude/agents/vera.md": "Vera QA review standards.",
}

THEME_RULES = [
    {
        "id": "session-archive-loop",
        "title": "Codify session-close archiving as a standard refinement loop",
        "priority": "high",
        "keywords": [
            "close chat",
            "session archive",
            "session log",
            "session close",
            "reply artifacts",
            "codex session",
            "dreamer quick pass",
        ],
        "target_docs": ["docs/larry/operations.md", "CLAUDE.md", ".claude/agents/dreamer.md"],
        "proposed_update": (
            "When a substantial session ends, save both a concise closeout and a fuller session archive, "
            "index them, and treat them as first-class inputs for later recall, QA, and SOP promotion."
        ),
        "trigger_examples": [
            "owner says close chat",
            "a substantial Larry or Codex session ends",
            "a system change created durable learnings worth preserving",
        ],
    },
    {
        "id": "durable-deliverable-indexing",
        "title": "Make indexing part of the definition of done for durable markdown",
        "priority": "high",
        "keywords": [
            "indexed",
            "knowledge base",
            "owners-inbox",
            "file_id",
            "knowledge_base_id",
            "saved to owners-inbox",
        ],
        "target_docs": ["CLAUDE.md", "docs/larry/operations.md"],
        "proposed_update": (
            "Any durable markdown deliverable saved to owners-inbox should be indexed before the workflow is considered complete, "
            "using the stable helper tools instead of ad hoc SQL."
        ),
        "trigger_examples": [
            "a post, report, or session log was just saved",
            "a specialist produced owner-facing markdown",
        ],
    },
    {
        "id": "structured-extracts-first",
        "title": "Prefer structured extracts over raw transcripts for downstream writing",
        "priority": "medium",
        "keywords": [
            "structured transcript extract",
            "meeting extract",
            "timestamp highlights",
            "action items",
            "open questions",
            "transcript extract",
        ],
        "target_docs": ["docs/larry/operations.md", "docs/larry/youtube-writing.md"],
        "proposed_update": (
            "Before drafting from a transcript or meeting note, create or review a structured extract so "
            "timestamps, decisions, action items, and reusable claims are easier to reuse than raw text."
        ),
        "trigger_examples": [
            "a YouTube package starts from a transcript",
            "a meeting note needs durable follow-up value",
        ],
    },
    {
        "id": "mobile-cuecam-grammar",
        "title": "Keep the mobile CueCam grammar explicit and preview-first when ambiguity appears",
        "priority": "medium",
        "keywords": [
            "cuecam",
            "discord",
            "mobile",
            "teleprompter",
            "inline",
            "movie card",
            "attachment order",
        ],
        "target_docs": ["docs/larry/cuecam.md", "CLAUDE.md"],
        "proposed_update": (
            "For mobile CueCam requests, keep defaults explicit, map attachments in order, distinguish image versus movie versus teleprompter-only cards, "
            "and preview parsed cards before build whenever the owner's phrasing is ambiguous."
        ),
        "trigger_examples": [
            "the owner sends mixed media from the phone",
            "a card line is incomplete or layout intent is ambiguous",
        ],
    },
    {
        "id": "vera-before-public-handoff",
        "title": "Use Vera as the default final-pass reviewer for publish-ready writing",
        "priority": "medium",
        "keywords": [
            "vera approved",
            "minor fixes",
            "quality review",
            "fact-sensitive",
            "public-facing",
            "final post",
        ],
        "target_docs": ["CLAUDE.md", "docs/larry/operations.md", ".claude/agents/vera.md"],
        "proposed_update": (
            "Public-facing or fact-sensitive drafts should go through Vera before handoff unless the owner explicitly asks for speed over polish."
        ),
        "trigger_examples": [
            "a blog post, research brief, or YouTube package is ready to send",
        ],
    },
]


@dataclass
class Artifact:
    path: Path
    rel_path: str
    kind: str
    title: str
    headings: list[str]
    excerpt: str
    modified_at: datetime


def _slugify(text: str, fallback: str = "sop-promotion-report") -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:80] or fallback


def _rel_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PKA_ROOT))
    except ValueError:
        return str(resolved)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _extract_headings(text: str, *, limit: int = 8) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                headings.append(heading)
        if len(headings) >= limit:
            break
    return headings


def _excerpt(text: str, limit: int = 900) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _artifact_kind(path: Path) -> str:
    parent = path.parent.name
    if parent == "session-logs":
        return "session-log"
    if parent == "meeting-extracts":
        return "meeting-extract"
    if parent == "transcript-extracts":
        return "transcript-extract"
    if parent == "blog":
        return "blog"
    if parent == "youtube":
        return "youtube-package"
    if parent == "research":
        return "research"
    if path.name.startswith("research-"):
        return "research"
    return "owner-document"


def _is_low_signal(path: Path, text: str) -> bool:
    rel_path = _rel_path(path)
    if "owners-inbox/sop-promotions/" in rel_path:
        return True
    lowered = text.lower()
    if "no substantial work was performed this session" in lowered:
        return True
    return len(" ".join(text.split())) < 120


def _candidate_paths(days: int, limit: int) -> list[Path]:
    cutoff = datetime.now() - timedelta(days=days)
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pattern in SOURCE_PATTERNS:
        for path in PKA_ROOT.glob(pattern):
            if not path.is_file() or path.suffix.lower() != ".md":
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append(resolved)

    recent = [
        path
        for path in candidates
        if datetime.fromtimestamp(path.stat().st_mtime) >= cutoff
    ]
    active = recent if recent else candidates
    active.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return active[:limit]


def collect_artifacts(days: int, limit: int) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for path in _candidate_paths(days, limit):
        try:
            text = _read_text(path)
        except OSError:
            continue
        if _is_low_signal(path, text):
            continue
        artifacts.append(
            Artifact(
                path=path,
                rel_path=_rel_path(path),
                kind=_artifact_kind(path),
                title=_extract_markdown_title(text, path.stem),
                headings=_extract_headings(text),
                excerpt=_excerpt(text),
                modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            )
        )
    return artifacts


def _fit_artifacts_for_prompt(
    artifacts: list[Artifact],
    *,
    max_chars: int = 8500,
    max_excerpt_chars: int = 320,
) -> list[Artifact]:
    packed: list[Artifact] = []
    total_chars = 0
    for artifact in artifacts:
        excerpt = artifact.excerpt
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[: max_excerpt_chars - 3].rstrip() + "..."
        headings = artifact.headings[:5]
        block = (
            f"path: {artifact.rel_path}\n"
            f"kind: {artifact.kind}\n"
            f"title: {artifact.title}\n"
            f"modified_at: {artifact.modified_at.isoformat(timespec='seconds')}\n"
            f"headings: {' | '.join(headings) if headings else 'None'}\n"
            f"excerpt: {excerpt}\n\n"
        )
        if packed and total_chars + len(block) > max_chars:
            break
        packed.append(
            Artifact(
                path=artifact.path,
                rel_path=artifact.rel_path,
                kind=artifact.kind,
                title=artifact.title,
                headings=headings,
                excerpt=excerpt,
                modified_at=artifact.modified_at,
            )
        )
        total_chars += len(block)
    return packed


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for index, char in enumerate(text[start:], start=start):
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break

    repair = lmstudio.chat(
        [
            {
                "role": "system",
                "content": (
                    "Repair malformed JSON. Return valid JSON only. "
                    "Preserve the original intent and fill missing structure conservatively."
                ),
            },
            {"role": "user", "content": text[:5000]},
        ],
        temperature=0.0,
        max_tokens=1200,
    )
    choices = repair.get("choices", [])
    if not choices:
        raise lmstudio.LMStudioError("LM Studio could not repair malformed JSON output.")
    repaired_text = choices[0].get("message", {}).get("content", "")
    return json.loads(repaired_text)


def _dedupe_list(items: list[str], *, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in items:
        value = " ".join((item or "").split()).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
        if len(cleaned) >= limit:
            break
    return cleaned


def _normalize_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": " ".join((item.get("title") or "").split()).strip(),
        "priority": (item.get("priority") or "medium").strip().lower(),
        "target_docs": _dedupe_list(item.get("target_docs", []), limit=5),
        "why_now": " ".join((item.get("why_now") or "").split()).strip(),
        "proposed_update": " ".join((item.get("proposed_update") or "").split()).strip(),
        "trigger_examples": _dedupe_list(item.get("trigger_examples", []), limit=5),
        "evidence_paths": _dedupe_list(item.get("evidence_paths", []), limit=6),
    }


def _normalize_new_sop(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": " ".join((item.get("title") or "").split()).strip(),
        "scope": " ".join((item.get("scope") or "").split()).strip(),
        "why_needed": " ".join((item.get("why_needed") or "").split()).strip(),
        "outline": _dedupe_list(item.get("outline", []), limit=8),
        "evidence_paths": _dedupe_list(item.get("evidence_paths", []), limit=6),
    }


def _heuristic_analysis(artifacts: list[Artifact], *, reason: str) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    hold_off: list[str] = []
    for rule in THEME_RULES:
        evidence: list[str] = []
        for artifact in artifacts:
            haystack = " ".join(
                [
                    artifact.title,
                    " ".join(artifact.headings),
                    artifact.excerpt,
                    artifact.kind,
                    artifact.rel_path,
                ]
            ).lower()
            if any(keyword in haystack for keyword in rule["keywords"]):
                evidence.append(artifact.rel_path)
        unique_evidence = _dedupe_list(evidence, limit=6)
        if len(unique_evidence) >= 2 or (len(unique_evidence) == 1 and rule["priority"] == "high"):
            candidates.append(
                {
                    "title": rule["title"],
                    "priority": rule["priority"],
                    "target_docs": rule["target_docs"],
                    "why_now": (
                        f"Recent archive material shows this pattern in {len(unique_evidence)} artifact(s), "
                        "which is enough signal to tighten the playbook."
                    ),
                    "proposed_update": rule["proposed_update"],
                    "trigger_examples": rule["trigger_examples"],
                    "evidence_paths": unique_evidence,
                }
            )
        elif unique_evidence:
            hold_off.append(
                f"{rule['title']} — present in {len(unique_evidence)} artifact(s), but not broad enough yet to codify confidently."
            )

    candidates = candidates[:4]
    summary = (
        "Archive-to-SOP promotion report generated from recent artifacts. "
        "This pass used heuristic fallback analysis because the local model did not return a stable structured response."
    )
    tags = ["sop-promotion", "playbook", "archive", "workflow"]
    return {
        "summary": summary,
        "promotion_candidates": candidates,
        "new_sop_drafts": [],
        "hold_off": hold_off or ["No additional immature patterns stood out in this review window."],
        "recommended_tags": tags,
    }


def _system_prompt() -> str:
    doc_list = "\n".join(f"- {path}: {description}" for path, description in TARGET_DOCS.items())
    return (
        "You are Dreamer's SOP promotion analyst for PKA.\n"
        "Review recent archived work and identify durable operational patterns that should be codified.\n"
        "Promote only process or operating learnings. Ignore one-off content facts, marketing angles, and topic-specific claims.\n"
        "Prefer updating existing docs over inventing new ones.\n"
        "Existing docs you may target:\n"
        f"{doc_list}\n\n"
        "Return valid JSON only with this shape:\n"
        "{"
        "\"summary\": string,"
        "\"promotion_candidates\": ["
        "{"
        "\"title\": string,"
        "\"priority\": \"high\" | \"medium\" | \"low\","
        "\"target_docs\": [string],"
        "\"why_now\": string,"
        "\"proposed_update\": string,"
        "\"trigger_examples\": [string],"
        "\"evidence_paths\": [string]"
        "}"
        "],"
        "\"new_sop_drafts\": ["
        "{"
        "\"title\": string,"
        "\"scope\": string,"
        "\"why_needed\": string,"
        "\"outline\": [string],"
        "\"evidence_paths\": [string]"
        "}"
        "],"
        "\"hold_off\": [string],"
        "\"recommended_tags\": [string]"
        "}\n"
        "Rules:\n"
        "- At most 4 promotion candidates and at most 1 new SOP draft.\n"
        "- Favor patterns supported by multiple artifacts, or a single strong artifact that reflects a production issue or a system fix.\n"
        "- Target docs should use the exact paths listed above when relevant.\n"
        "- `proposed_update` should read like a concrete addition or refinement to an operating doc.\n"
        "- `hold_off` should capture ideas that are interesting but not yet mature enough to codify.\n"
        "- No markdown fences."
    )


def analyze_artifacts(artifacts: list[Artifact]) -> dict[str, Any]:
    if not artifacts:
        return {
            "summary": "No recent high-signal artifacts were available for SOP promotion.",
            "promotion_candidates": [],
            "new_sop_drafts": [],
            "hold_off": ["No strong archive material was found in the current review window."],
            "recommended_tags": ["sop-promotion"],
        }

    artifact_lines: list[str] = []
    for index, artifact in enumerate(artifacts, start=1):
        headings = " | ".join(artifact.headings[:6]) if artifact.headings else "None"
        artifact_lines.extend(
            [
                f"Artifact {index}",
                f"path: {artifact.rel_path}",
                f"kind: {artifact.kind}",
                f"title: {artifact.title}",
                f"modified_at: {artifact.modified_at.isoformat(timespec='seconds')}",
                f"headings: {headings}",
                f"excerpt: {artifact.excerpt}",
                "",
            ]
        )

    user_prompt = (
        f"Today: {datetime.now().date().isoformat()}\n"
        "Review these recent archived artifacts and propose SOP/playbook promotions.\n\n"
        + "\n".join(artifact_lines)
    )
    try:
        response = lmstudio.chat(
            [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=1100,
        )
        choices = response.get("choices", [])
        if not choices:
            raise lmstudio.LMStudioError("LM Studio returned no choices.")
        data = _extract_json(choices[0].get("message", {}).get("content", ""))
        return {
            "summary": " ".join((data.get("summary") or "").split()).strip(),
            "promotion_candidates": [
                item
                for item in (_normalize_candidate(raw) for raw in data.get("promotion_candidates", []))
                if item["title"] and item["proposed_update"]
            ][:4],
            "new_sop_drafts": [
                item
                for item in (_normalize_new_sop(raw) for raw in data.get("new_sop_drafts", []))
                if item["title"] and (item["scope"] or item["outline"])
            ][:1],
            "hold_off": _dedupe_list(data.get("hold_off", []), limit=8),
            "recommended_tags": _dedupe_list(data.get("recommended_tags", []), limit=10),
        }
    except Exception as exc:
        return _heuristic_analysis(artifacts, reason=str(exc))


def _report_path() -> Path:
    SOP_PROMOTION_DIR.mkdir(parents=True, exist_ok=True)
    base = SOP_PROMOTION_DIR / f"{datetime.now().date().isoformat()}-archive-to-sop-promotion.md"
    if not base.exists():
        return base
    version = 2
    while True:
        candidate = SOP_PROMOTION_DIR / f"{datetime.now().date().isoformat()}-archive-to-sop-promotion-v{version}.md"
        if not candidate.exists():
            return candidate
        version += 1


def _bullet_block(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- None."]


def render_report(
    artifacts: list[Artifact],
    analysis: dict[str, Any],
    *,
    days: int,
) -> str:
    lines = [
        "---",
        f"generated_at: {datetime.now().isoformat(timespec='seconds')}",
        f"review_window_days: {days}",
        f"artifact_count: {len(artifacts)}",
        "source: archive-to-sop-promotion",
        "---",
        "",
        "# Archive-to-SOP Promotion Report",
        "",
        "## Summary",
        "",
        analysis.get("summary") or "No durable promotion candidates were identified.",
        "",
        "## Reviewed Artifacts",
        "",
    ]
    if artifacts:
        for artifact in artifacts:
            lines.append(
                f"- `{artifact.rel_path}` — {artifact.kind} — {artifact.title}"
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Promotion Candidates", ""])
    candidates = analysis.get("promotion_candidates", [])
    if not candidates:
        lines.append("- None.")
    else:
        for index, candidate in enumerate(candidates, start=1):
            lines.extend(
                [
                    f"### {index}. {candidate['title']}",
                    "",
                    f"- Priority: {candidate['priority'].capitalize()}",
                    "- Target Docs: "
                    + (", ".join(f"`{doc}`" for doc in candidate["target_docs"]) or "`NEW SOP`"),
                    f"- Why Now: {candidate['why_now'] or 'Not specified.'}",
                    f"- Proposed Update: {candidate['proposed_update']}",
                    "- Trigger Examples:",
                ]
            )
            lines.extend(_bullet_block(candidate["trigger_examples"]))
            lines.append("- Evidence:")
            lines.extend(_bullet_block([f"`{path}`" for path in candidate["evidence_paths"]]))
            lines.append("")

    lines.extend(["## New SOP Drafts", ""])
    new_sops = analysis.get("new_sop_drafts", [])
    if not new_sops:
        lines.append("- None.")
    else:
        for index, draft in enumerate(new_sops, start=1):
            lines.extend(
                [
                    f"### {index}. {draft['title']}",
                    "",
                    f"- Scope: {draft['scope'] or 'Not specified.'}",
                    f"- Why Needed: {draft['why_needed'] or 'Not specified.'}",
                    "- Draft Outline:",
                ]
            )
            lines.extend(_bullet_block(draft["outline"]))
            lines.append("- Evidence:")
            lines.extend(_bullet_block([f"`{path}`" for path in draft["evidence_paths"]]))
            lines.append("")

    lines.extend(["## Hold Off For Now", ""])
    lines.extend(_bullet_block(analysis.get("hold_off", [])))
    return "\n".join(lines).strip() + "\n"


def save_report(
    markdown: str,
    *,
    tags: list[str],
    summary: str,
) -> dict[str, Any]:
    path = _report_path()
    path.write_text(markdown, encoding="utf-8")
    index_result = index_markdown_artifact(
        path,
        title=f"Archive-to-SOP Promotion Report — {datetime.now().date().isoformat()}",
        category="sop-promotion",
        tags=["sop-promotion", "playbook", "dreamer"] + tags,
        summary=summary or "Archive-driven SOP and playbook promotion recommendations.",
    )
    return {
        "path": str(path),
        "file_id": index_result["file_id"],
        "knowledge_base_id": index_result["knowledge_base_id"],
    }


def run_report(days: int, limit: int, dry_run: bool) -> dict[str, Any]:
    artifacts = _fit_artifacts_for_prompt(collect_artifacts(days, limit))
    analysis = analyze_artifacts(artifacts)
    markdown = render_report(artifacts, analysis, days=days)
    if dry_run:
        return {
            "status": "dry-run",
            "artifact_count": len(artifacts),
            "candidate_count": len(analysis.get("promotion_candidates", [])),
            "markdown": markdown,
        }

    saved = save_report(
        markdown,
        tags=analysis.get("recommended_tags", []),
        summary=analysis.get("summary", ""),
    )
    return {
        "status": "saved",
        "artifact_count": len(artifacts),
        "candidate_count": len(analysis.get("promotion_candidates", [])),
        "new_sop_count": len(analysis.get("new_sop_drafts", [])),
        "report_path": saved["path"],
        "file_id": saved["file_id"],
        "knowledge_base_id": saved["knowledge_base_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote archived work into candidate SOP updates")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="Generate an archive-to-SOP promotion report")
    p_report.add_argument("--days", type=int, default=14, help="Review window in days")
    p_report.add_argument("--limit", type=int, default=14, help="Maximum number of artifacts to review")
    p_report.add_argument("--dry-run", action="store_true", help="Print the report JSON without saving")

    args = parser.parse_args()

    if args.command == "report":
        print(json.dumps(run_report(args.days, args.limit, args.dry_run), indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
