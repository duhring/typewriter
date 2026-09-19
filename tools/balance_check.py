#!/usr/bin/env python3
"""
Pre-record balance check: compare an outline or draft against the published
archive before recording.

Reads the file-based archive (owners-inbox/blog, owners-inbox/transcripts,
owners-inbox/briefs, wiki/) so it behaves identically on every peer, then
reports three things:

  1. Retread risk    — prior artifacts that cover the same ground (lexical
                       tf-idf similarity; PKA computes this)
  2. Contradictions  — positions in the outline that conflict with prior
                       published positions (the assistant's analysis of the
                       top overlaps; every quote is verified verbatim on import)
  3. Brand drift     — outline vs the canonical brand-system document
                       (same analysis)

The analysis is one handoff (docs/handoff-contract.md):

  bin/pka balance_check prepare --outline owners-inbox/development/<slug>/outline.md --slug <slug>
      -> lexical scan; balance-check.md written with the analysis pending;
         balance-check.analysis.request.json prepared with the overlap
         excerpts and the brand-system excerpt
  (the assistant writes balance-check.analysis.response.json beside it)
  bin/pka balance_check import --slug <slug>
      -> quotes verified against the cited sources; balance-check.md rewritten

  bin/pka balance_check prepare ... --lexical-only     # retread scan only, no request
  --top N   how many overlaps to report (default 8; the top 5 are sent for analysis)
  --dir D   explicit directory instead of owners-inbox/development/<slug>/

Response items, one per finding, ids of the assistant's choosing:
  {"id": "...", "kind": "contradiction", "outline_position": "...", "prior_position": "...",
   "source": "<path shown>", "quote": "<verbatim from that source>", "severity": "high|medium|low"}
  {"id": "...", "kind": "retread_note", "source": "<path shown>", "note": "..."}
  {"id": "...", "kind": "brand_drift", "note": "...", "brand_rule": "..."}
An empty items list is the correct answer when nothing qualifies.

The report is a durable deliverable. Read-only with respect to shared state.
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

import handoff  # noqa: E402

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

STAGE = "balance-check.analysis"
ANALYSIS_SOURCES = 5
EXCERPT_CHARS = 1500
OUTLINE_CHARS = 6000
KINDS = ("contradiction", "retread_note", "brand_drift")
SEVERITIES = ("high", "medium", "low")

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


# --------------------------------------------------------------------------
# The handoff
# --------------------------------------------------------------------------

def analysis_instructions() -> str:
    return f"""You are the editorial balance checker for {OWNER}'s publishing system.
Compare the new OUTLINE (payload.outline) against excerpts of the owner's PRIOR
PUBLISHED work (payload.sources; the files are listed under inputs if you want
more than the excerpt) and the BRAND SYSTEM (payload.brand_system).

Write one item per finding, of one of three kinds:
  {{"id": "...", "kind": "contradiction",
    "outline_position": "...", "prior_position": "...",
    "source": "<a path from payload.sources>",
    "quote": "<short VERBATIM quote from that source>",
    "severity": "high|medium|low"}}
  {{"id": "...", "kind": "retread_note", "source": "<a path from payload.sources>",
    "note": "what ground is being retread and what would make it fresh"}}
  {{"id": "...", "kind": "brand_drift", "note": "...",
    "brand_rule": "<the brand-system expectation it drifts from>"}}

Rules:
- Only report a contradiction when you can quote the prior source verbatim; the
  quote is checked mechanically on import. No quote, no claim.
- An empty items list is the correct answer when nothing qualifies. Do not pad.
- Retread notes are for genuine overlap, not shared vocabulary.
"""


def overlap_summary(d: dict) -> dict:
    return {k: d[k] for k in ("kind", "path", "title", "score", "shared_terms")}


def prepare(*, outline_path: Path, directory: Path, slug: str, top: int, lexical_only: bool) -> dict:
    outline_text = strip_frontmatter(outline_path.read_text(encoding="utf-8"))
    docs = load_archive()
    overlaps = rank_overlaps(outline_text, docs, top)
    summaries = [overlap_summary(d) for d in overlaps]
    report_path = directory / "balance-check.md"
    directory.mkdir(parents=True, exist_ok=True)

    if lexical_only:
        report = build_report(outline_path, outline_text, summaries, None, "lexical scan only", len(docs))
        report_path.write_text(report, encoding="utf-8")
        return {"status": "lexical_only", "report": str(report_path), "archive_docs": len(docs),
                "top_overlap": summaries[0]["path"] if summaries else None,
                "top_score": summaries[0]["score"] if summaries else 0.0}

    sources = overlaps[:ANALYSIS_SOURCES]
    inputs = {"outline": outline_path}
    for i, d in enumerate(sources, 1):
        inputs[f"source_{i}"] = PKA_ROOT / d["path"]
    if BRAND_SYSTEM.is_file():
        inputs["brand_system"] = BRAND_SYSTEM
    request = handoff.prepare(
        stage=STAGE,
        directory=directory,
        inputs=inputs,
        payload={
            "outline": outline_text[:OUTLINE_CHARS],
            "outline_path": str(outline_path),
            "archive_docs": len(docs),
            "overlaps": summaries,
            "sources": [
                {**overlap_summary(d), "excerpt": d["body"][:EXCERPT_CHARS]} for d in sources
            ],
            "brand_system": brand_excerpt(),
        },
        instructions=analysis_instructions(),
        slug=slug,
    )
    note = f"analysis pending: answer {request.response_path.name}, then run `bin/pka balance_check import`"
    report_path.write_text(
        build_report(outline_path, outline_text, summaries, None, note, len(docs)), encoding="utf-8"
    )
    return {"status": "prepared", "stage": STAGE, "request": str(request.path),
            "response": str(request.response_path), "report": str(report_path),
            "archive_docs": len(docs), "sources": [d["path"] for d in sources],
            "top_overlap": summaries[0]["path"] if summaries else None,
            "top_score": summaries[0]["score"] if summaries else 0.0}


def source_bodies(paths: list[str]) -> dict[str, str]:
    bodies = {}
    for path in paths:
        p = PKA_ROOT / path
        if p.is_file():
            bodies[path] = strip_frontmatter(p.read_text(encoding="utf-8"))
    return bodies


def make_item_validator(bodies: dict[str, str]):
    def validate(item: dict) -> None:
        cid = item["id"]
        kind = item.get("kind")
        if kind not in KINDS:
            raise handoff.HandoffError("item_invalid", f"{cid}: kind must be one of {', '.join(KINDS)}", id=cid)
        if kind in ("contradiction", "retread_note"):
            source = item.get("source")
            if source not in bodies:
                raise handoff.HandoffError(
                    "item_invalid", f"{cid}: source must be one of the paths shown in the request", id=cid,
                    source=source, allowed=sorted(bodies),
                )
        if kind == "contradiction":
            for key in ("outline_position", "prior_position"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise handoff.HandoffError("item_invalid", f"{cid}: {key} must be a nonempty string", id=cid)
            if item.get("severity") not in SEVERITIES:
                raise handoff.HandoffError("item_invalid", f"{cid}: severity must be high, medium, or low", id=cid)
            handoff.require_quote(bodies[item["source"]], item)
        elif kind == "retread_note":
            if not isinstance(item.get("note"), str) or not item["note"].strip():
                raise handoff.HandoffError("item_invalid", f"{cid}: note must be a nonempty string", id=cid)
        else:
            for key in ("note", "brand_rule"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise handoff.HandoffError("item_invalid", f"{cid}: {key} must be a nonempty string", id=cid)
    return validate


def analysis_from_items(items: list[dict]) -> dict:
    return {
        "contradictions": [it for it in items if it["kind"] == "contradiction"],
        "retread_notes": [it for it in items if it["kind"] == "retread_note"],
        "brand_drift": [it for it in items if it["kind"] == "brand_drift"],
    }


def import_analysis(*, directory: Path) -> dict:
    request = handoff.load_request(directory / f"{STAGE}.request.json")
    payload = request.data["payload"]
    outline_path = Path(payload["outline_path"])
    outline_text = strip_frontmatter(outline_path.read_text(encoding="utf-8"))
    bodies = source_bodies([s["path"] for s in payload["sources"]])
    report_path = directory / "balance-check.md"

    def apply(items: list[dict]) -> dict:
        analysis = analysis_from_items(items)
        report = build_report(outline_path, outline_text, payload["overlaps"], analysis, "", payload["archive_docs"])
        report_path.write_text(report, encoding="utf-8")
        return analysis

    result = handoff.import_response(request.path, apply=apply, item_validator=make_item_validator(bodies))
    if result.status == "already_imported":
        return {"status": result.status, "stage": STAGE, "report": str(report_path)}
    analysis = result.result
    return {"status": "applied", "stage": STAGE, "report": str(report_path),
            "contradictions": len(analysis["contradictions"]),
            "retread_notes": len(analysis["retread_notes"]),
            "brand_drift": len(analysis["brand_drift"])}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

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
        lines.append(f"_Analysis not applied: {llm_note}_")
    else:
        contradictions = analysis.get("contradictions") or []
        if not contradictions:
            lines.append("None found against the top-overlap sources.")
        for c in contradictions:
            lines.append(
                f"- **[{c.get('severity', '?')}]** Outline says: {c.get('outline_position', '?')}\n"
                f"  - Prior position ({c.get('source', '?')}): {c.get('prior_position', '?')}\n"
                f"  - Quote (verified): “{c.get('quote', '')}”"
            )
        retread_notes = analysis.get("retread_notes") or []
        if retread_notes:
            lines += ["", "### Retread Notes", ""]
            for r in retread_notes:
                lines.append(f"- {r.get('source', '?')}: {r.get('note', '')}")

    lines += ["", "## Brand Drift", ""]
    if analysis is None:
        lines.append(f"_Analysis not applied: {llm_note}_")
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
        "Verification note: every contradiction quote above was checked verbatim "
        "against the cited source on import; a finding whose quote could not be "
        "found was refused. The judgment behind each finding is still the "
        "assistant's — read the cited source before acting on a high-severity "
        "item. Route the reviewed report through Vera when the video is "
        "publish-bound.",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-record balance check against the PKA archive")
    parser.add_argument("--slug", help="Development slug; files live in owners-inbox/development/<slug>/")
    parser.add_argument("--dir", help="Explicit directory instead of the slug's development folder")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prep = sub.add_parser("prepare", help="Lexical scan, pending report, and the analysis request")
    p_prep.add_argument("--outline", required=True, help="Path to the outline/draft markdown")
    p_prep.add_argument("--top", type=int, default=8, help="How many overlaps to report (default 8)")
    p_prep.add_argument("--lexical-only", action="store_true", help="Retread scan only; no analysis request")
    sub.add_parser("import", help="Validate the analysis response and write the report")
    args = parser.parse_args(argv)

    if args.dir:
        directory = Path(args.dir).expanduser().resolve()
        slug = args.slug or directory.name
    elif args.slug:
        slug = args.slug
        directory = DEVELOPMENT_DIR / slug
    elif args.command == "prepare":
        outline = Path(args.outline).expanduser().resolve()
        slug = outline.parent.name
        directory = outline.parent
    else:
        parser.error("--slug or --dir is required")

    try:
        if args.command == "prepare":
            outline_path = Path(args.outline).expanduser().resolve()
            if not outline_path.exists():
                print(json.dumps({"error": f"Outline not found: {outline_path}"}), file=sys.stderr)
                return 1
            out = prepare(outline_path=outline_path, directory=directory, slug=slug,
                          top=args.top, lexical_only=args.lexical_only)
        else:
            out = import_analysis(directory=directory)
    except handoff.HandoffError as exc:
        print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
