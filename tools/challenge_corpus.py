#!/usr/bin/env python3
"""
Passage-level, authority-aware corpus for the challenge gate.

Two problems with scoring claims against whole documents:

  1. A document-level score tells the skeptic that *something* in a 3000-word
     file matches, but the matching passage may sit well past any excerpt the
     prompt can carry. The model then judges a label it cannot see evidence for.
  2. Not every file in the archive is John's published position. The transcripts
     directory is roughly half his own recordings and half transcripts of *other
     people's* videos he watched. Treating René Ritchie's NAB talk as evidence
     of John's prior position produces false contradictions.

So this module indexes **passages**, not documents, and tags every passage with
the authority of its source. Retrieval returns the passage text itself, which
makes "every cited hit has a matching excerpt" structural rather than a
promise the caller has to keep.

Authority tiers
---------------
published   John's own public output — his blog posts and transcripts of his own
            recordings. The only tier admissible as contradiction evidence.
internal    Working material: development docs, plans, notes. Real overlap, but
            it is not prior *public* ground.
derivative  Compiled wiki views. CLAUDE.md is explicit that these are views, not
            sources of truth; counting them double-counts their sources.
third_party Someone else's words — watched-video transcripts and the briefs
            summarizing them. Never evidence of what John thinks.

Scoring
-------
BM25 ranks candidates. Strength is then decided by IDF-weighted **coverage** —
the share of the claim's meaningful terms actually present in the passage. That
is an absolute, interpretable quantity, which matters because the previous
implementation labelled the best hit in each batch "strong" even when every hit
was noise, and a claim's label could change because an unrelated claim was added
to the interview. Coverage depends only on the claim and the passage.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent

PUBLISHED = "published"
INTERNAL = "internal"
DERIVATIVE = "derivative"
THIRD_PARTY = "third_party"

# Only John's own public output can contradict John.
CONTRADICTION_TIERS = frozenset({PUBLISHED})
# Retread is about ground already covered publicly.
RETREAD_TIERS = frozenset({PUBLISHED})

SCAN_DIRS = (
    PKA_ROOT / "owners-inbox" / "blog",
    PKA_ROOT / "owners-inbox" / "transcripts",
    PKA_ROOT / "owners-inbox" / "briefs",
    PKA_ROOT / "wiki",
)
WIKI_SKIP = {"README.md", "index.md", "_manifest.json", "stale-review.md"}

# Absolute coverage bands. A passage below FLOOR is not evidence at all — this
# is the "no meaningful evidence" outcome that a purely relative scheme cannot
# express.
COVERAGE_STRONG = 0.45
COVERAGE_MODERATE = 0.28
COVERAGE_FLOOR = 0.15

TARGET_PASSAGE_WORDS = 120
MIN_PASSAGE_WORDS = 25

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


def normalize_text(text: str) -> str:
    """Fold the variations that make a verbatim quote fail to match itself."""
    text = unicodedata.normalize("NFKC", text)
    text = (
        text.replace("‘", "'").replace("’", "'")
        .replace("“", '"').replace("”", '"')
        .replace("–", "-").replace("—", "-")
        .replace("…", "...")
    )
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalized_with_map(text: str) -> tuple[str, list[int]]:
    """Normalized text plus, for each normalized char, its original offset.

    Lets a match found in normalized space be reported against the real file.
    """
    text = unicodedata.normalize("NFKC", text)
    out: list[str] = []
    offsets: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        mapped = {
            "‘": "'", "’": "'", "“": '"', "”": '"',
            "–": "-", "—": "-",
        }.get(ch, ch)
        if mapped.isspace():
            if prev_space or not out:
                continue
            out.append(" ")
            offsets.append(i)
            prev_space = True
            continue
        prev_space = False
        out.append(mapped.casefold())
        offsets.append(i)
    while out and out[-1] == " ":
        out.pop()
        offsets.pop()
    return "".join(out), offsets


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def doc_title(path: Path, text: str) -> str:
    m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', text[:600], re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return path.stem


def classify_authority(path: Path, raw: str) -> str:
    """Decide what a file is evidence *of*, from its location and its markers."""
    try:
        rel = path.relative_to(PKA_ROOT)
    except ValueError:
        rel = path
    parts = rel.parts
    head = raw[:800]

    if parts and parts[0] == "wiki":
        return DERIVATIVE

    if "blog" in parts:
        return PUBLISHED

    if "briefs" in parts:
        # Briefs summarize external videos (they carry source_url + transcript_path).
        return THIRD_PARTY if re.search(r"^source_url:", head, re.MULTILINE) else INTERNAL

    if "transcripts" in parts:
        # "**Video:** https://…" => someone else's video that John watched.
        # "**Source:** /path/to/local.mp4" => John's own recording.
        if re.search(r"^\*\*Video:\*\*\s*https?://", head, re.MULTILINE):
            return THIRD_PARTY
        if re.search(r"^\*\*Source:\*\*", head, re.MULTILINE):
            return PUBLISHED
        return INTERNAL

    return INTERNAL


def split_passages(body: str) -> list[tuple[str, int]]:
    """Split into ~TARGET_PASSAGE_WORDS chunks on paragraph boundaries.

    Returns (passage_text, char_offset_into_body).
    """
    paras = [(m.group(0), m.start()) for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", body)]
    passages: list[tuple[str, int]] = []
    buf: list[str] = []
    buf_start = 0
    buf_words = 0

    for text, start in paras:
        words = len(text.split())
        if not buf:
            buf_start = start
        buf.append(text)
        buf_words += words
        if buf_words >= TARGET_PASSAGE_WORDS:
            passages.append(("\n".join(buf), buf_start))
            buf, buf_words = [], 0
    if buf:
        passages.append(("\n".join(buf), buf_start))

    merged: list[tuple[str, int]] = []
    for text, start in passages:
        if merged and len(text.split()) < MIN_PASSAGE_WORDS:
            prev_text, prev_start = merged[-1]
            merged[-1] = (prev_text + "\n" + text, prev_start)
        else:
            merged.append((text, start))
    return merged


@dataclass
class Passage:
    doc_path: str
    doc_title: str
    authority: str
    offset: int
    text: str
    tf: Counter = field(default_factory=Counter)
    length: int = 0


class Corpus:
    """BM25 over authority-tagged passages."""

    K1 = 1.5
    B = 0.75

    def __init__(self, passages: list[Passage]):
        self.passages = passages
        self.n = len(passages)
        self.avg_len = (sum(p.length for p in passages) / self.n) if self.n else 0.0
        df: Counter = Counter()
        for p in passages:
            df.update(p.tf.keys())
        self.df = df

    def idf(self, term: str) -> float:
        n_q = self.df.get(term, 0)
        return math.log(1 + (self.n - n_q + 0.5) / (n_q + 0.5))

    def coverage(self, terms: list[str], passage: Passage) -> float:
        """IDF-weighted share of the claim's terms present in the passage.

        Absolute and independent of any other claim in the batch.
        """
        if not terms:
            return 0.0
        weights = {t: self.idf(t) for t in set(terms)}
        total = sum(weights.values()) or 1.0
        hit = sum(w for t, w in weights.items() if passage.tf.get(t))
        return hit / total

    def bm25(self, terms: list[str], passage: Passage) -> float:
        score = 0.0
        for term in set(terms):
            freq = passage.tf.get(term, 0)
            if not freq:
                continue
            denom = freq + self.K1 * (1 - self.B + self.B * passage.length / (self.avg_len or 1.0))
            score += self.idf(term) * (freq * (self.K1 + 1)) / denom
        return score

    def search(
        self,
        query: str,
        *,
        tiers: frozenset[str],
        top: int = 3,
    ) -> list[dict]:
        terms = tokenize(query)
        if not terms:
            return []
        scored = []
        for p in self.passages:
            if p.authority not in tiers:
                continue
            cov = self.coverage(terms, p)
            if cov < COVERAGE_FLOOR:
                continue
            scored.append(
                {
                    "path": p.doc_path,
                    "title": p.doc_title,
                    "authority": p.authority,
                    "offset": p.offset,
                    "excerpt": p.text,
                    "bm25": round(self.bm25(terms, p), 4),
                    "coverage": round(cov, 4),
                    "strength": strength_for(cov),
                }
            )
        scored.sort(key=lambda h: (h["coverage"], h["bm25"]), reverse=True)
        return scored[:top]

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for p in sorted(self.passages, key=lambda x: (x.doc_path, x.offset)):
            h.update(f"{p.doc_path}:{p.offset}:{p.length}".encode())
        return h.hexdigest()[:16]

    def tier_counts(self) -> dict[str, int]:
        counts: Counter = Counter(p.authority for p in self.passages)
        return dict(counts)


def strength_for(coverage: float) -> str:
    if coverage >= COVERAGE_STRONG:
        return "strong"
    if coverage >= COVERAGE_MODERATE:
        return "moderate"
    if coverage >= COVERAGE_FLOOR:
        return "weak"
    return "none"


def load_corpus(scan_dirs=SCAN_DIRS) -> Corpus:
    passages: list[Passage] = []
    for root in scan_dirs:
        if not Path(root).is_dir():
            continue
        for path in sorted(Path(root).rglob("*.md")):
            if path.name in WIKI_SKIP:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                continue
            authority = classify_authority(path, raw)
            body = strip_frontmatter(raw)
            title = doc_title(path, raw)
            try:
                rel = str(path.relative_to(PKA_ROOT))
            except ValueError:
                rel = str(path)
            for text, offset in split_passages(body):
                tokens = tokenize(text)
                if len(tokens) < 8:
                    continue
                passages.append(
                    Passage(
                        doc_path=rel,
                        doc_title=title,
                        authority=authority,
                        offset=offset,
                        text=text,
                        tf=Counter(tokens),
                        length=len(tokens),
                    )
                )
    return Corpus(passages)


def verify_quote(quote: str, source_text: str) -> dict:
    """Verify a quote **in full**, not by prefix.

    Returns status plus the character span in the original source, so a later
    reader can open the file and land on the passage.
    """
    q_norm = normalize_text(quote or "")
    if not q_norm:
        return {"status": "quote_not_found", "start": None, "end": None, "matched": ""}

    src_norm, offsets = normalized_with_map(source_text)
    idx = src_norm.find(q_norm)
    if idx == -1:
        return {"status": "quote_not_found", "start": None, "end": None, "matched": ""}

    start = offsets[idx]
    end_idx = idx + len(q_norm) - 1
    end = offsets[end_idx] + 1 if end_idx < len(offsets) else len(source_text)
    return {
        "status": "verified",
        "start": start,
        "end": end,
        "matched": source_text[start:end],
    }
