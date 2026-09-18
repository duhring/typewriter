#!/usr/bin/env python3
"""
Pre-record balance check: compare an outline or draft against the published
archive before recording.

Reads the file-based archive (owners-inbox/blog, owners-inbox/transcripts,
owners-inbox/briefs, wiki/) so it behaves identically on the mini and on
Devendorf, then reports three things:

  1. Retread risk    — prior artifacts that cover the same ground (lexical
                       tf-idf similarity, no LLM required)
  2. Contradictions  — positions in the outline that conflict with prior
                       published positions (LLM pass over the top overlaps)
  3. Brand drift     — outline vs the canonical brand-system document
                       (same LLM pass)

Usage:
  discord-bridge/venv/bin/python3 tools/balance_check.py --outline path/to/outline.md
  ... --slug my-video            # write report to owners-inbox/development/<slug>/balance-check.md
  ... --no-llm                   # lexical retread scan only
  ... --provider glm --top 8

The report is a durable deliverable: on the mini, index it after review; on
Devendorf it queues for home like any other owners-inbox file. Read-only with
respect to shared state.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

ARCHIVE_DIRS = (
    ("blog", PKA_ROOT / "owners-inbox" / "blog"),
    ("transcript", PKA_ROOT / "owners-inbox" / "transcripts"),
    ("brief", PKA_ROOT / "owners-inbox" / "briefs"),
    ("wiki", PKA_ROOT / "wiki"),
)
BRAND_SYSTEM = PKA_ROOT / "owners-inbox" / "brand-system.md"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from machine_role import load_machine  # noqa: E402
OWNER = load_machine().get("owner") or "the owner"
DEVELOPMENT_DIR = PKA_ROOT / "owners-inbox" / "development"
WIKI_SKIP = {"README.md", "index.md", "_manifest.json", "stale-review.md"}

STOPWORDS = set(
    """a about above after again all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during
    each few for from further had has have having he her here hers him his how i
    if in into is it its just like me more most my no nor not now of off on once
    only or other our out over own same she should so some such than that the
    their them then there these they this those through to too under until up
    very was we were what when where which while who whom why will with would you
    your really going want know think thing things get got make made one two way
    lot kind sort actually basically say said see look right okay yeah""".split()
)

TOKEN_RE = re.compile(r"[a-z][a-z0-9'-]+")


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 2]


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def doc_title(path: Path, text: str) -> str:
    m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text[:600], re.MULTILINE)
    if m:
        return m.group(1)
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return path.stem


def load_archive() -> list[dict]:
    docs = []
    for kind, root in ARCHIVE_DIRS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if path.name in WIKI_SKIP:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                continue
            body = strip_frontmatter(raw)
            tokens = tokenize(body)
            if len(tokens) < 40:
                continue
            docs.append(
                {
                    "kind": kind,
                    "path": str(path.relative_to(PKA_ROOT)),
                    "title": doc_title(path, raw),
                    "body": body,
                    "tf": Counter(tokens),
                    "length": len(tokens),
                }
            )
    return docs


def rank_overlaps(outline_text: str, docs: list[dict], top: int) -> list[dict]:
    """tf-idf cosine of the outline against each archive doc."""
    outline_tf = Counter(tokenize(outline_text))
    if not outline_tf or not docs:
        return []

    n_docs = len(docs) + 1
    df: Counter = Counter()
    for d in docs:
        df.update(d["tf"].keys())
    df.update(outline_tf.keys())

    def idf(term: str) -> float:
        return math.log(n_docs / (1 + df[term])) + 1.0

    def vec(tf: Counter) -> dict[str, float]:
        return {t: (1 + math.log(c)) * idf(t) for t, c in tf.items()}

    ovec = vec(outline_tf)
    onorm = math.sqrt(sum(w * w for w in ovec.values())) or 1.0

    scored = []
    for d in docs:
        dvec = vec(d["tf"])
        dnorm = math.sqrt(sum(w * w for w in dvec.values())) or 1.0
        common = set(ovec) & set(dvec)
        if not common:
            continue
        cos = sum(ovec[t] * dvec[t] for t in common) / (onorm * dnorm)
        shared = sorted(common, key=lambda t: ovec[t] * dvec[t], reverse=True)[:8]
        scored.append({**d, "score": round(cos, 4), "shared_terms": shared})
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top]


def brand_excerpt(limit: int = 3500) -> str:
    try:
        text = strip_frontmatter(BRAND_SYSTEM.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return text[:limit]


def llm_analysis(outline_text: str, overlaps: list[dict], provider: str | None) -> dict:
    """One JSON pass: contradiction candidates + brand-drift notes."""
    import llm

    excerpts = "\n\n".join(
        f"### Source {i + 1}: {d['title']} ({d['path']}, similarity {d['score']})\n"
        f"{d['body'][:1500]}"
        for i, d in enumerate(overlaps[:5])
    )
    prompt = f"""You are the editorial balance checker for {OWNER}'s publishing system.
Compare a new video OUTLINE against excerpts of his PRIOR PUBLISHED work and his BRAND SYSTEM.

Return strict JSON with this shape:
{{
  "contradictions": [
    {{"outline_position": "...", "prior_position": "...", "source": "<path>",
      "quote": "<short verbatim quote from the prior source>",
      "severity": "high|medium|low"}}
  ],
  "retread_notes": [
    {{"source": "<path>", "note": "what ground is being retread and what would make it fresh"}}
  ],
  "brand_drift": [
    {{"note": "...", "brand_rule": "<the brand-system expectation it drifts from>"}}
  ]
}}

Rules:
- Only report a contradiction when you can quote the prior source verbatim; no quote, no claim.
- Empty arrays are the correct answer when nothing qualifies. Do not pad.
- Retread notes are for genuine overlap, not shared vocabulary.

OUTLINE:
{outline_text[:6000]}

PRIOR PUBLISHED WORK:
{excerpts}

BRAND SYSTEM (excerpt):
{brand_excerpt()}
"""
    # GLM-5.2 reasons before answering; the budget must cover reasoning
    # tokens plus the JSON, or content comes back empty.
    return llm.chat_json(
        prompt,
        provider=provider,
        max_tokens=8000,
        label="balance_check.analysis",
    )


def build_report(
    outline_path: Path,
    outline_text: str,
    overlaps: list[dict],
    analysis: dict | None,
    llm_note: str,
    corpus_size: int,
) -> str:
    today = date.today().isoformat()
    lines = [
        "---",
        f'title: "Balance Check: {doc_title(outline_path, outline_text)}"',
        f"date: {today}",
        "category: development-balance-check",
        f"outline: {outline_path}",
        f"archive_docs_scanned: {corpus_size}",
        "tags: [development, balance-check, editorial]",
        "---",
        "",
        f"# Balance Check — {today}",
        "",
        f"Outline: `{outline_path}` · Archive scanned: {corpus_size} documents "
        "(blog, transcripts, briefs, wiki)",
        "",
        "## Retread Risk",
        "",
    ]
    if not overlaps:
        lines.append("No meaningful overlap with the published archive. Clear runway.")
    else:
        lines.append("| Similarity | Prior artifact | Shared ground |")
        lines.append("|---|---|---|")
        for d in overlaps:
            lines.append(
                f"| {d['score']:.2f} | [{d['title']}]({d['path']}) ({d['kind']}) "
                f"| {', '.join(d['shared_terms'][:6])} |"
            )
        lines.append("")
        lines.append(
            "Scores above ~0.30 usually mean the same territory; 0.15–0.30 is "
            "adjacent ground worth differentiating on purpose."
        )

    lines += ["", "## Contradiction Candidates", ""]
    if analysis is None:
        lines.append(f"_LLM pass skipped: {llm_note}_")
    else:
        contradictions = analysis.get("contradictions") or []
        if not contradictions:
            lines.append("None found against the top-overlap sources.")
        for c in contradictions:
            lines.append(
                f"- **[{c.get('severity', '?')}]** Outline says: {c.get('outline_position', '?')}\n"
                f"  - Prior position ({c.get('source', '?')}): {c.get('prior_position', '?')}\n"
                f"  - Quote: “{c.get('quote', '')}”"
            )
        retread_notes = analysis.get("retread_notes") or []
        if retread_notes:
            lines += ["", "### Retread Notes", ""]
            for r in retread_notes:
                lines.append(f"- {r.get('source', '?')}: {r.get('note', '')}")

    lines += ["", "## Brand Drift", ""]
    if analysis is None:
        lines.append(f"_LLM pass skipped: {llm_note}_")
    else:
        drift = analysis.get("brand_drift") or []
        if not drift:
            lines.append("On-brand against the brand-system document.")
        for b in drift:
            lines.append(f"- {b.get('note', '')} (brand rule: {b.get('brand_rule', '?')})")

    lines += [
        "",
        "---",
        "",
        "Verification note: contradiction quotes are model-extracted. Before "
        "acting on a high-severity item, open the cited source and confirm the "
        "quote exists (the NotebookLM Performed/Verified/Rejected discipline "
        "applies). Route the reviewed report through Vera when the video is "
        "publish-bound.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-record balance check against the PKA archive")
    parser.add_argument("--outline", required=True, help="Path to the outline/draft markdown")
    parser.add_argument("--slug", help="Development slug; writes owners-inbox/development/<slug>/balance-check.md")
    parser.add_argument("--out", help="Explicit output path, or '-' for stdout")
    parser.add_argument("--top", type=int, default=8, help="How many overlaps to report (default 8)")
    parser.add_argument("--no-llm", action="store_true", help="Lexical retread scan only")
    parser.add_argument("--provider", help="LLM provider override (glm, xai, lmstudio)")
    args = parser.parse_args()

    outline_path = Path(args.outline).expanduser()
    if not outline_path.exists():
        print(f"Outline not found: {outline_path}", file=sys.stderr)
        return 1
    outline_text = strip_frontmatter(outline_path.read_text(encoding="utf-8"))

    docs = load_archive()
    overlaps = rank_overlaps(outline_text, docs, args.top)

    analysis = None
    llm_note = "disabled with --no-llm"
    if not args.no_llm:
        try:
            analysis = llm_analysis(outline_text, overlaps, args.provider)
        except Exception as exc:
            llm_note = f"provider unavailable ({exc}); lexical results only"
            print(f"⚠️  LLM pass failed, continuing lexical-only: {exc}", file=sys.stderr)

    report = build_report(outline_path, outline_text, overlaps, analysis, llm_note, len(docs))

    if args.out == "-":
        print(report)
        return 0
    if args.out:
        out_path = Path(args.out)
    elif args.slug:
        out_path = DEVELOPMENT_DIR / args.slug / "balance-check.md"
    else:
        print(report)
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out_path), "archive_docs": len(docs),
                      "top_overlap": overlaps[0]["path"] if overlaps else None,
                      "top_score": overlaps[0]["score"] if overlaps else 0.0}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
