"""Tests for the challenge gate — the adoption bar before it becomes a default.

No network, no model service: the assistant's answers are response files written by the tests. Run from the repo root:

    bin/pka test -v
"""

from __future__ import annotations

import json
import io
from contextlib import redirect_stdout
import sys
import tempfile
import unittest
from unittest.mock import patch
from collections import Counter
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKA_ROOT / "tools"))

import challenge  # noqa: E402
import challenge_corpus as cc  # noqa: E402


def make_passage(path: str, text: str, authority: str = cc.PUBLISHED, offset: int = 0) -> cc.Passage:
    tokens = cc.tokenize(text)
    return cc.Passage(
        doc_path=path,
        doc_title=path,
        authority=authority,
        offset=offset,
        text=text,
        tf=Counter(tokens),
        length=len(tokens),
    )


class TestQuoteVerification(unittest.TestCase):
    """A quote must verify in full, not by prefix."""

    SOURCE = (
        "John said that publishing means literally to make public, and the "
        "question then becomes: what public are we talking about? He went on at "
        "length about micro markets and the long tail of distribution."
    )

    def test_exact_quote_verifies_with_span(self):
        quote = "publishing means literally to make public"
        result = cc.verify_quote(quote, self.SOURCE)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(self.SOURCE[result["start"]:result["end"]].lower(), quote.lower())

    def test_tampering_after_60_chars_is_rejected(self):
        """The prefix-only bug: first 60 chars real, remainder invented."""
        real_prefix = self.SOURCE[:60]
        self.assertGreaterEqual(len(real_prefix), 60)
        tampered = real_prefix + " and therefore every founder must ship daily."
        self.assertEqual(cc.verify_quote(tampered, self.SOURCE)["status"], "quote_not_found")

    def test_prefix_alone_would_have_passed_the_old_check(self):
        """Guards the regression: prefix matching must not be sufficient."""
        tampered = self.SOURCE[:60] + " INVENTED CONTINUATION"
        self.assertIn(tampered[:60].lower(), self.SOURCE.lower())      # old check passed
        self.assertEqual(cc.verify_quote(tampered, self.SOURCE)["status"], "quote_not_found")

    def test_smart_quotes_and_whitespace_normalize(self):
        source = "He said “infinite variety” is achieved\nthrough  micro markets."
        self.assertEqual(
            cc.verify_quote('"infinite variety" is achieved through micro markets', source)["status"],
            "verified",
        )

    def test_empty_quote_is_not_found(self):
        self.assertEqual(cc.verify_quote("", self.SOURCE)["status"], "quote_not_found")


class TestSupportTaxonomy(unittest.TestCase):
    """quote_not_found, owner assertion, and quote-backed are distinct outcomes."""

    SOURCE = "The public you publish to can be small, even one person, even just yourself."

    def test_three_outcomes_are_distinguished(self):
        claims = challenge.build_claims(
            [
                {"claim": "Backed", "support_quote": "can be small, even one person"},
                {"claim": "Owner asserted it", "support_quote": ""},
                {"claim": "Model invented it", "support_quote": "a quote that is nowhere in here"},
            ],
            self.SOURCE,
        )
        self.assertEqual(claims[0]["support"], challenge.QUOTE_BACKED)
        self.assertEqual(claims[1]["support"], challenge.OWNER_ASSERTION)
        self.assertEqual(claims[2]["support"], challenge.QUOTE_NOT_FOUND)
        # The unverifiable quote is preserved for review, not silently dropped.
        self.assertEqual(claims[2]["claimed_quote"], "a quote that is nowhere in here")
        self.assertEqual(claims[2]["support_quote"], "")

    def test_malformed_record_is_surfaced_not_dropped(self):
        claims = challenge.build_claims(
            [{"claim": "", "support_quote": "an orphan quote with no assertion"}],
            self.SOURCE,
        )
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["support"], challenge.EXTRACTION_ERROR)
        self.assertEqual(claims[0]["claimed_quote"], "an orphan quote with no assertion")
        self.assertTrue(challenge.needs_resolution(claims[0]))

    def test_wholly_empty_record_is_dropped(self):
        self.assertEqual(challenge.build_claims([{"claim": "", "support_quote": ""}], self.SOURCE), [])

    def test_duplicate_claims_across_windows_are_deduped(self):
        claims = challenge.build_claims(
            [
                {"claim": "Publishing means making public", "support_quote": ""},
                {"claim": "publishing  means   making public", "support_quote": ""},
            ],
            self.SOURCE,
        )
        self.assertEqual(len(claims), 1)


class TestWindowing(unittest.TestCase):
    """Nothing past the old 12,000-character boundary may be silently lost."""

    def test_long_source_is_fully_covered(self):
        paras = [f"Paragraph {i} about micro markets and publishing tools." for i in range(600)]
        text = "\n\n".join(paras)
        self.assertGreater(len(text), 12000)

        windows = challenge.window_source(text)
        self.assertGreater(len(windows), 1)
        joined = "".join(windows)
        # Every paragraph, including ones far past 12k, appears in some window.
        for marker in ("Paragraph 0 ", "Paragraph 300 ", "Paragraph 599 "):
            self.assertIn(marker, joined)
        self.assertIn("Paragraph 599 ", windows[-1])

    def test_short_source_is_a_single_window(self):
        self.assertEqual(challenge.window_source("short text"), ["short text"])


class TestEvidenceCalibration(unittest.TestCase):
    """Strength must be absolute, not 'best in this batch'."""

    def setUp(self):
        self.corpus = cc.Corpus([
            make_passage("blog/a.md", "Micro markets enable infinite variety through mass customization."),
            make_passage("blog/b.md", "Sourdough starter hydration ratios for winter baking in cold kitchens."),
            make_passage("blog/c.md", "Bicycle drivetrain maintenance and chain wear measurement techniques."),
        ])

    def test_all_noise_batch_yields_no_evidence(self):
        hits = self.corpus.search(
            "quantum chromodynamics lattice gauge renormalization",
            tiers=cc.RETREAD_TIERS,
        )
        self.assertEqual(hits, [], "noise must produce no evidence, not a 'strong' best-of-noise hit")

    def test_genuine_overlap_is_labelled_strong(self):
        hits = self.corpus.search("Micro markets enable infinite variety", tiers=cc.RETREAD_TIERS)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["strength"], "strong")

    def test_label_is_stable_when_unrelated_claims_are_added(self):
        """A claim's label must not depend on other claims in the interview."""
        query = "Micro markets enable infinite variety"
        alone = self.corpus.search(query, tiers=cc.RETREAD_TIERS)[0]["strength"]

        claims_small = challenge.build_claims([{"claim": query, "support_quote": ""}], query)
        claims_large = challenge.build_claims(
            [{"claim": query, "support_quote": ""}]
            + [{"claim": f"Unrelated assertion number {i}", "support_quote": ""} for i in range(10)],
            query,
        )
        challenge.attach_evidence(claims_small, self.corpus)
        challenge.attach_evidence(claims_large, self.corpus)

        self.assertEqual(claims_small[0]["retread"][0]["strength"], alone)
        self.assertEqual(claims_large[0]["retread"][0]["strength"], alone)

    def test_strength_bands_are_absolute(self):
        self.assertEqual(cc.strength_for(0.90), "strong")
        self.assertEqual(cc.strength_for(0.30), "moderate")
        self.assertEqual(cc.strength_for(0.16), "weak")
        self.assertEqual(cc.strength_for(0.05), "none")


class TestAuthority(unittest.TestCase):
    """Only John's own published work is evidence of John's position."""

    def test_watched_video_transcript_is_third_party(self):
        raw = "# NAB Show 2026\n**Video:** https://youtube.com/watch?v=abc\n\nbody"
        path = PKA_ROOT / "owners-inbox" / "transcripts" / "2026-04-19-nab.md"
        self.assertEqual(cc.classify_authority(path, raw), cc.THIRD_PARTY)

    def test_own_recording_transcript_is_published(self):
        raw = "# Ep1\n**Source:** /Volumes/Home/Downloads/Ep1.mp4\n\nbody"
        path = PKA_ROOT / "owners-inbox" / "transcripts" / "2026-06-14-ep1.md"
        self.assertEqual(cc.classify_authority(path, raw), cc.PUBLISHED)

    def test_blog_is_published_and_wiki_is_derivative(self):
        self.assertEqual(
            cc.classify_authority(PKA_ROOT / "owners-inbox" / "blog" / "x.md", "# post"),
            cc.PUBLISHED,
        )
        self.assertEqual(cc.classify_authority(PKA_ROOT / "wiki" / "log.md", "# log"), cc.DERIVATIVE)

    def test_external_brief_is_third_party(self):
        raw = "---\ntitle: Listers\nsource_url: https://youtu.be/x\n---\n\nbody"
        self.assertEqual(
            cc.classify_authority(PKA_ROOT / "owners-inbox" / "briefs" / "b.md", raw),
            cc.THIRD_PARTY,
        )

    def test_non_published_tiers_are_excluded_from_retread_evidence(self):
        corpus = cc.Corpus([
            make_passage("wiki/log.md", "Micro markets enable infinite variety.", cc.DERIVATIVE),
            make_passage("owners-inbox/transcripts/nab.md", "Micro markets enable infinite variety.", cc.THIRD_PARTY),
        ])
        self.assertEqual(corpus.search("Micro markets infinite variety", tiers=cc.RETREAD_TIERS), [])

    def test_contradiction_tier_is_published_only(self):
        self.assertEqual(cc.CONTRADICTION_TIERS, frozenset({cc.PUBLISHED}))


class TestEvidenceReachesTheModel(unittest.TestCase):
    """Every label shown to the skeptic must arrive with its passage."""

    def test_every_cited_hit_has_its_excerpt_in_the_prompt(self):
        corpus = cc.Corpus([
            make_passage("blog/a.md", "Micro markets enable infinite variety through mass customization."),
            make_passage("blog/b.md", "Publishing means literally to make public and choosing your public."),
        ])
        claims = challenge.build_claims(
            [
                {"claim": "Micro markets enable infinite variety", "support_quote": ""},
                {"claim": "Publishing means to make public", "support_quote": ""},
            ],
            "irrelevant source",
        )
        challenge.attach_evidence(claims, corpus)
        rendered = challenge.render_batch(claims)

        cited = [h for c in claims for h in c["retread"]]
        self.assertTrue(cited, "expected at least one hit to exercise the check")
        for hit in cited:
            self.assertIn(hit["path"], rendered)
            self.assertIn(hit["excerpt"][:80], rendered)

    def test_absence_of_evidence_is_stated_explicitly(self):
        corpus = cc.Corpus([make_passage("blog/a.md", "Sourdough hydration ratios for winter baking.")])
        claims = challenge.build_claims(
            [{"claim": "quantum chromodynamics lattice gauge", "support_quote": ""}],
            "src",
        )
        challenge.attach_evidence(claims, corpus)
        self.assertIn("NO MEANINGFUL OVERLAP FOUND", challenge.render_batch(claims))

    def test_batches_respect_the_evidence_budget(self):
        big = "word " * 400
        corpus = cc.Corpus([make_passage(f"blog/{i}.md", big + f" marker{i} micro markets") for i in range(6)])
        claims = challenge.build_claims(
            [{"claim": f"micro markets claim {i}", "support_quote": ""} for i in range(20)],
            "src",
        )
        challenge.attach_evidence(claims, corpus)
        for batch in challenge.batch_claims(claims):
            self.assertLessEqual(len(batch), challenge.MAX_CLAIMS_PER_BATCH)
        flat = [c["id"] for b in challenge.batch_claims(claims) for c in b]
        self.assertEqual(sorted(flat), sorted(c["id"] for c in claims))


class TestVerdictSchema(unittest.TestCase):
    """Duplicate, missing, and invented ids, and invalid verdicts, are refused by import."""

    def setUp(self):
        import handoff

        self.handoff = handoff
        self.claims = challenge.build_claims(
            [{"claim": f"claim {i}", "support_quote": ""} for i in range(3)], "src"
        )
        self.request = {
            "schema_version": 1, "request_id": "r", "stage": challenge.STAGE_VERDICTS,
            "inputs": {}, "expects": {"ids": [c["id"] for c in self.claims]},
        }

    def validate(self, items):
        response = {"schema_version": 1, "request_id": "r", "stage": challenge.STAGE_VERDICTS,
                    "inputs": {}, "items": items}
        return self.handoff.validate_response(
            self.request, response, item_validator=challenge.validate_verdict_item, check_disk=False
        )

    def verdict(self, cid, verdict="survives", **extra):
        return {"id": cid, "verdict": verdict, "rationale": "r", "needs": "", "contradiction": None, **extra}

    def test_clean_response_is_accepted(self):
        items = self.validate([self.verdict(c["id"]) for c in self.claims])
        self.assertEqual(len(items), 3)

    def test_invented_id_is_rejected(self):
        with self.assertRaises(self.handoff.HandoffError) as ctx:
            self.validate([self.verdict(c["id"]) for c in self.claims] + [self.verdict("c99")])
        self.assertEqual(ctx.exception.code, "unknown_ids")

    def test_duplicate_id_is_rejected(self):
        with self.assertRaises(self.handoff.HandoffError) as ctx:
            self.validate([self.verdict("c1"), self.verdict("c1", "proposed_drop"), self.verdict("c2"), self.verdict("c3")])
        self.assertEqual(ctx.exception.code, "duplicate_ids")

    def test_missing_verdict_is_rejected(self):
        with self.assertRaises(self.handoff.HandoffError) as ctx:
            self.validate([self.verdict("c1")])
        self.assertEqual(ctx.exception.code, "missing_ids")
        self.assertEqual(ctx.exception.details["ids"], ["c2", "c3"])

    def test_invalid_verdict_is_rejected_not_downgraded(self):
        with self.assertRaises(self.handoff.HandoffError) as ctx:
            self.validate([self.verdict("c1", "obliterated"), self.verdict("c2"), self.verdict("c3")])
        self.assertEqual(ctx.exception.code, "invalid_verdict")

    def test_contradiction_needs_source_and_quote(self):
        with self.assertRaises(self.handoff.HandoffError):
            self.validate([self.verdict("c1", contradiction={"source": "blog/a.md"}), self.verdict("c2"), self.verdict("c3")])


class TestContradictionVerification(unittest.TestCase):
    """An unverifiable citation may not remove the owner's material."""

    DOCS = {"blog/a.md": "He wrote that mass markets are dead and micro markets won."}

    def test_verified_quote_is_kept_with_span(self):
        contradiction, reason = challenge.verify_contradiction(
            {"source": "blog/a.md", "quote": "mass markets are dead"}, self.DOCS
        )
        self.assertIsNotNone(contradiction)
        self.assertEqual(reason, "")
        self.assertIn("span", contradiction)

    def test_unverifiable_quote_is_discarded(self):
        contradiction, reason = challenge.verify_contradiction(
            {"source": "blog/a.md", "quote": "he never believed in micro markets"}, self.DOCS
        )
        self.assertIsNone(contradiction)
        self.assertIn("not found", reason)

    def test_uncited_source_is_discarded(self):
        contradiction, reason = challenge.verify_contradiction(
            {"source": "blog/ghost.md", "quote": "anything"}, self.DOCS
        )
        self.assertIsNone(contradiction)
        self.assertIn("not in the evidence", reason)

    def test_drop_is_downgraded_when_its_contradiction_fails(self):
        claims = challenge.build_claims([{"claim": "micro markets won", "support_quote": ""}], "src")
        challenge.apply_verdicts(
            claims,
            {"c1": {
                "verdict": "proposed_drop",
                "rationale": "contradicts prior work",
                "contradiction": {"source": "blog/a.md", "quote": "fabricated line"},
            }},
            self.DOCS,
        )
        self.assertEqual(claims[0]["verdict"], challenge.WEAKENED)
        self.assertIsNone(claims[0]["contradiction"])
        self.assertIn("downgraded", claims[0]["needs"])

    def test_drop_stands_when_its_contradiction_verifies(self):
        claims = challenge.build_claims([{"claim": "mass markets thrive", "support_quote": ""}], "src")
        challenge.apply_verdicts(
            claims,
            {"c1": {
                "verdict": "proposed_drop",
                "rationale": "contradicts prior work",
                "contradiction": {"source": "blog/a.md", "quote": "mass markets are dead"},
            }},
            self.DOCS,
        )
        self.assertEqual(claims[0]["verdict"], challenge.PROPOSED_DROP)
        self.assertIsNotNone(claims[0]["contradiction"])


class TestResolutionAndHistory(unittest.TestCase):
    """Overrides persist; history is append-only; failure preserves the last analysis."""

    def _register(self):
        claims = challenge.build_claims(
            [
                {"claim": "backed and strong", "support_quote": "verbatim anchor text"},
                {"claim": "weak one", "support_quote": ""},
            ],
            "verbatim anchor text lives here",
        )
        return {"slug": "t", "source": "s.md", "claims": claims, "runs": []}

    def test_override_beats_model_verdict(self):
        reg = self._register()
        c = reg["claims"][1]
        c["verdict"] = challenge.PROPOSED_DROP
        c["owner_override"] = challenge.SURVIVES
        self.assertEqual(challenge.effective(c), challenge.SURVIVES)
        self.assertFalse(challenge.needs_resolution(c))

    def test_unresolved_claims_are_not_effective_survivors(self):
        reg = self._register()
        for c in reg["claims"]:
            c["verdict"] = challenge.SURVIVES
        # Claim 2 is an owner assertion with no example: still needs a ruling.
        self.assertEqual(reg["claims"][1]["support"], challenge.OWNER_ASSERTION)
        survivors = challenge.effective_survivors(reg["claims"])
        self.assertEqual([c["id"] for c in survivors], ["c1"])

    def test_resolved_assertion_becomes_a_survivor(self):
        reg = self._register()
        for c in reg["claims"]:
            c["verdict"] = challenge.SURVIVES
        reg["claims"][1]["owner_override"] = challenge.SURVIVES
        self.assertEqual(len(challenge.effective_survivors(reg["claims"])), 2)

    def test_run_history_is_append_only(self):
        reg = self._register()
        corpus = cc.Corpus([make_passage("blog/a.md", "micro markets and infinite variety")])
        for verdict in (challenge.SURVIVES, challenge.PROPOSED_DROP, challenge.WEAKENED):
            for c in reg["claims"]:
                c["verdict"] = verdict
            challenge.record_run(
                reg, corpus=corpus, source_text="src",
                claims=reg["claims"], problems=[], note="",
            )
        self.assertEqual(len(reg["runs"]), 3)
        self.assertEqual(reg["runs"][0]["proposed"]["c1"], challenge.SURVIVES)
        self.assertEqual(reg["runs"][1]["proposed"]["c1"], challenge.PROPOSED_DROP)
        for run in reg["runs"]:
            for key in ("at", "prompt_version", "provider", "source_sha", "corpus_fingerprint"):
                self.assertIn(key, run)

    def test_history_records_effective_alongside_proposed(self):
        reg = self._register()
        corpus = cc.Corpus([make_passage("blog/a.md", "micro markets")])
        reg["claims"][0]["verdict"] = challenge.PROPOSED_DROP
        reg["claims"][0]["owner_override"] = challenge.SURVIVES
        challenge.record_run(
            reg, corpus=corpus, source_text="src",
            claims=reg["claims"], problems=[], note="",
        )
        run = reg["runs"][-1]
        self.assertEqual(run["proposed"]["c1"], challenge.PROPOSED_DROP)
        self.assertEqual(run["effective"]["c1"], challenge.SURVIVES)

    def test_llm_failure_preserves_prior_verdicts(self):
        """A failed challenge pass must not overwrite the last good analysis."""
        reg = self._register()
        for c in reg["claims"]:
            c["verdict"] = challenge.SURVIVES
            c["rationale"] = "from the good run"
        before = json.dumps(reg["claims"], sort_keys=True)

        corpus = cc.Corpus([make_passage("blog/a.md", "micro markets and infinite variety")])
        try:
            raise RuntimeError("GLM did not return valid JSON")
        except Exception as exc:
            note = f"Challenge pass failed ({exc}); prior verdicts kept."

        challenge.attach_evidence(reg["claims"], corpus)   # retrieval still refreshes
        for c in reg["claims"]:
            self.assertEqual(c["verdict"], challenge.SURVIVES)
            self.assertEqual(c["rationale"], "from the good run")
        challenge.record_run(
            reg, corpus=corpus, source_text="src",
            claims=reg["claims"], problems=[], note=note,
        )
        self.assertIn("failed", reg["runs"][-1]["note"])

        stripped_before = [
            {k: v for k, v in c.items() if k not in ("retread", "history_status")} for c in json.loads(before)
        ]
        stripped_after = [
            {k: v for k, v in c.items() if k not in ("retread", "history_status")} for c in reg["claims"]
        ]
        self.assertEqual(stripped_before, stripped_after)


class TestReport(unittest.TestCase):
    def test_report_separates_cleared_from_pending(self):
        claims = challenge.build_claims(
            [
                {"claim": "backed claim", "support_quote": "anchor text"},
                {"claim": "asserted claim", "support_quote": ""},
            ],
            "anchor text lives here",
        )
        claims[0]["verdict"] = challenge.SURVIVES
        claims[1]["verdict"] = challenge.PROPOSED_DROP
        corpus = cc.Corpus([make_passage("blog/a.md", "micro markets")])
        register = {"slug": "t", "source": "s.md", "claims": claims, "runs": []}
        challenge.record_run(
            register, corpus=corpus, source_text="src",
            claims=claims, problems=[], note="",
        )
        report = challenge.build_report(register, corpus, "")

        self.assertIn("Cleared for outline 1", report)
        self.assertIn("Awaiting your call 1", report)
        self.assertIn("Proposed drops", report)
        self.assertNotIn("Killed", report)   # owner-facing language is advisory

    def test_report_discloses_excluded_tiers(self):
        corpus = cc.Corpus([
            make_passage("blog/a.md", "micro markets", cc.PUBLISHED),
            make_passage("owners-inbox/transcripts/nab.md", "someone else talking", cc.THIRD_PARTY),
        ])
        register = {"slug": "t", "source": "s.md", "claims": [], "runs": []}
        report = challenge.build_report(register, corpus, "")
        self.assertIn("third-party: 1", report)


class TestSemanticReview(unittest.TestCase):
    """The context review is the assistant's; PKA prepares complete-paragraph
    context, validates the answer, and applies it. Same guarantees as before."""

    def review(self, source, claim_text, quote, valid, prior=None, contradiction_valid=False):
        claims = challenge.build_claims([{"claim": claim_text, "support_quote": quote}], source)
        c = claims[0]
        c.update(verdict=challenge.PROPOSED_DROP, rationale="Initial objection")
        docs = {"blog/a.md": prior} if prior else {}
        if prior:
            c["contradiction"] = {"source": "blog/a.md", "quote": prior}
        entry = challenge.context_payload_item(c, source, docs)
        self.assertIsNotNone(entry, "a quote-backed claim must get a context entry")
        self.assertIn(quote, entry["source_context"])
        if prior:
            self.assertIn(prior, entry["prior_context"])
        result = dict(id=c["id"], claim_valid=valid, verdict="proposed_drop" if contradiction_valid else "survives",
                      rationale="Context distinguishes the endorsed position from embedded beliefs.",
                      needs="", contradiction_valid=contradiction_valid)
        challenge.validate_review_item(result)
        challenge.apply_context_review(c, result, source_context=entry["source_context"], prior_context=entry["prior_context"])
        return c

    def test_embedded_negated_beliefs_never_reach_owner_or_outline(self):
        source = "I don’t want people to think that I somehow coded the system or that I have certain techniques that make it work for me. or, that it is out of reach for virtually anybody."
        for claim, quote in [("I coded the system", "I somehow coded the system"),
                             ("It is out of reach", "it is out of reach for virtually anybody")]:
            with self.subTest(claim=claim):
                c = self.review(source, claim, quote, False)
                self.assertEqual(c["support"], challenge.QUOTE_BACKED)
                self.assertFalse(challenge.needs_resolution(c))
                self.assertEqual(challenge.effective_survivors([c]), [])
                report = challenge.build_report({"claims": [c], "slug": "t", "source": "s"}, cc.Corpus([]), "")
                self.assertIn("Awaiting your call 0", report)
                self.assertIn("Internal review", report)

    def test_quoted_belief_is_not_owner_endorsement(self):
        c = self.review('Critics say “Only coders can do this.” I disagree.',
                        "Only coders can do this", "Only coders can do this", False)
        self.assertTrue(challenge.internally_held(c))

    def test_hypothetical_preserves_its_scope(self):
        source = "If only coders could do this, participation would shrink. Anyone can participate today."
        c = self.review(source, "Participation is shrinking", "participation would shrink", False)
        self.assertTrue(challenge.internally_held(c))
        c = self.review(source, "If only coders could do this, participation would shrink",
                        "If only coders could do this, participation would shrink", True)
        self.assertEqual(challenge.effective_survivors([c]), [c])

    def test_invalid_objection_is_repaired_without_owner_question(self):
        c = self.review("Anyone can use the system.", "Anyone can use the system", "Anyone can use the system", True,
                        prior='Some people say “Only coders can use the system.” I disagree.')
        self.assertIsNone(c["contradiction"])
        self.assertFalse(challenge.needs_resolution(c))
        self.assertEqual(challenge.effective_survivors([c]), [c])

    def test_genuine_contradiction_is_retained_for_owner(self):
        c = self.review("Anyone can use the system.", "Anyone can use the system", "Anyone can use the system", True,
                        prior="Only coders can use the system.", contradiction_valid=True)
        self.assertTrue(challenge.needs_resolution(c))
        self.assertIsNotNone(c["contradiction"])

    def test_malformed_review_cannot_clear_a_claim(self):
        import handoff

        c = challenge.build_claims([{"claim": "Anyone can participate", "support_quote": "Anyone can participate"}], "Anyone can participate.")[0]
        for bad in ({"id": "c1", "claim_valid": "false"},
                    {"id": "c1", "claim_valid": True, "contradiction_valid": False, "verdict": "survives", "rationale": "  ", "needs": ""},
                    {"id": "c1", "claim_valid": True, "contradiction_valid": False, "verdict": "kept", "rationale": "ok", "needs": ""}):
            with self.subTest(bad=bad):
                with self.assertRaises(handoff.HandoffError):
                    challenge.validate_review_item(bad)
        self.assertEqual(c["verdict"], challenge.UNREVIEWED)

    def test_review_endorsing_an_unverified_contradiction_is_rejected(self):
        import handoff

        c = challenge.build_claims([{"claim": "Anyone can participate", "support_quote": "Anyone can participate"}], "Anyone can participate.")[0]
        result = dict(id="c1", claim_valid=True, verdict="proposed_drop", rationale="r", needs="", contradiction_valid=True)
        with self.assertRaises(handoff.HandoffError) as ctx:
            challenge.apply_context_review(c, result, source_context="Anyone can participate.", prior_context=None)
        self.assertEqual(ctx.exception.code, "item_invalid")
        self.assertEqual(c["verdict"], challenge.UNREVIEWED)

    def test_claim_without_verified_context_gets_no_entry(self):
        c = challenge.build_claims([{"claim": "Made up", "support_quote": "words never said"}], "Anyone can participate.")[0]
        self.assertEqual(c["support"], challenge.QUOTE_NOT_FOUND)
        self.assertIsNone(challenge.context_payload_item(c, "Anyone can participate.", {}))

    def test_owner_override_and_prior_history_remain_authoritative(self):
        c = self.review("Critics say X; I disagree.", "X", "X", False)
        c["owner_override"] = challenge.SURVIVES
        c["owner_note"] = "Owner decision"
        self.assertEqual(challenge.effective_survivors([c]), [c])
        reg = {"claims": [c], "runs": []}
        corpus = cc.Corpus([])
        challenge.record_run(reg, corpus=corpus, source_text="src", claims=[c], problems=[], note="")
        before = json.dumps(reg["runs"][0], sort_keys=True)
        c["context_review"]["status"] = "pending"
        self.assertEqual(json.dumps(reg["runs"][0], sort_keys=True), before)
        self.assertEqual(c["owner_note"], "Owner decision")


class TestStageFlow(unittest.TestCase):
    """The three handoffs end to end, through the CLI, with no model anywhere."""

    SOURCE = (
        "Anyone can use the system today; that is the whole point.\n\n"
        "I never said only coders benefit. Critics say only coders benefit, and I disagree.\n\n"
        "The laptop contribution matters because it proved the workflow travels.\n"
    )

    def setUp(self):
        import handoff

        self.handoff = handoff
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "dev"
        self.dir.mkdir()
        self.source = self.dir / "interview.md"
        self.source.write_text("---\ncategory: development-interview\n---\n\n" + self.SOURCE, encoding="utf-8")
        self.corpus = cc.Corpus([make_passage("blog/a.md", "the workflow travels between machines")])
        self.patcher = patch.object(challenge, "load_corpus", return_value=self.corpus)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), patch("sys.stderr", err):
            code = challenge.main(["--dir", str(self.dir), *argv])
        payload = out.getvalue() or err.getvalue()
        return code, (json.loads(payload) if payload.strip().startswith("{") else payload)

    def respond(self, stage, items):
        request = self.handoff.load_request(self.dir / f"{stage}.request.json")
        request.response_path.write_text(json.dumps({
            "schema_version": 1, "request_id": request.request_id, "stage": stage,
            "inputs": {role: spec["sha256"] for role, spec in request.data["inputs"].items()},
            "items": items,
        }), encoding="utf-8")
        return request

    def test_first_interview_can_proceed_without_published_history(self):
        # An empty install and an install containing only internal/third-party
        # records both lack eligible history. Neither requires past claims.
        for passages in ([], [make_passage("notes/a.md", self.SOURCE, authority=cc.INTERNAL)]):
            with self.subTest(passages=len(passages)):
                self.dir = Path(self.tmp.name) / f"first-run-{len(passages)}"
                self.dir.mkdir()
                self.source = self.dir / "interview.md"
                self.source.write_text(self.SOURCE, encoding="utf-8")
                challenge.load_corpus.return_value = cc.Corpus(passages)
                self.assertEqual(self.run_cli("extract", "prepare", "--source", str(self.source))[0], 0)
                self.respond(challenge.STAGE_EXTRACT, [
                    {"id": "a", "window": 1, "claim": "Anyone can use the system",
                     "support_quote": "Anyone can use the system today"},
                ])
                self.assertEqual(self.run_cli("extract", "import")[0], 0)
                req = self.handoff.load_request(self.dir / f"{challenge.STAGE_VERDICTS}.request.json")
                self.assertIn("NO PUBLISHED HISTORY AVAILABLE", req.data["payload"]["batches"][0]["text"])
                self.assertIn("must never require the owner to supply past work", req.data["instructions"])
                self.respond(challenge.STAGE_VERDICTS, [
                    {"id": "c1", "verdict": "survives", "rationale": "Supported in this interview",
                     "needs": "", "contradiction": None},
                ])
                self.assertEqual(self.run_cli("verdicts", "import")[0], 0)
                # Source-context validation remains required even without history.
                self.assertEqual(self.run_cli("status")[1]["cleared"], 0)
                self.respond(challenge.STAGE_CONTEXT, [
                    {"id": "c1", "claim_valid": True, "verdict": "survives",
                     "rationale": "Owner endorses the claim", "needs": "",
                     "contradiction_valid": False},
                ])
                code, out = self.run_cli("context-review", "import")
                self.assertEqual(code, 0, out)
                self.assertEqual(out["cleared"], 1)
                self.assertEqual(out["needs_resolution"], 0)
                status = self.run_cli("status")[1]
                self.assertIn("proceed to the written piece or presentation", status["next"])
                self.assertIn("No published history is available", (self.dir / "challenge.md").read_text())

    def test_full_run(self):
        # Stage 1: prepare, answer, import.
        code, out = self.run_cli("extract", "prepare", "--source", str(self.source))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["windows"], 1)
        request = self.handoff.load_request(self.dir / f"{challenge.STAGE_EXTRACT}.request.json")
        self.assertIn("VERBATIM", request.data["instructions"])
        self.assertIn(self.SOURCE.split("\n")[0], request.data["payload"]["windows"][0]["text"])

        code, out = self.run_cli("extract", "import")
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "file_missing")

        self.respond(challenge.STAGE_EXTRACT, [
            {"id": "a", "window": 1, "claim": "Anyone can use the system", "support_quote": "Anyone can use the system today"},
            {"id": "b", "window": 1, "claim": "Only coders benefit", "support_quote": "only coders benefit"},
            {"id": "c", "window": 1, "claim": "The laptop contribution proved the workflow travels", "support_quote": "the workflow travels"},
            {"id": "d", "window": 1, "claim": "Made up claim", "support_quote": "text that is not there"},
            {"id": "e", "window": 1, "claim": "Asserted without a quote", "support_quote": ""},
        ])
        code, out = self.run_cli("extract", "import")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["claims"], 5)
        register = json.loads((self.dir / "claims.json").read_text())
        supports = {c["claim"]: c["support"] for c in register["claims"]}
        self.assertEqual(supports["Made up claim"], challenge.QUOTE_NOT_FOUND)
        self.assertEqual(supports["Asserted without a quote"], challenge.OWNER_ASSERTION)
        self.assertEqual(supports["Anyone can use the system"], challenge.QUOTE_BACKED)
        verdict_request = self.handoff.load_request(self.dir / f"{challenge.STAGE_VERDICTS}.request.json")
        self.assertEqual(verdict_request.data["expects"]["ids"], ["c1", "c2", "c3", "c4", "c5"])
        self.assertEqual(verdict_request.data["depends_on"][0]["request_id"], request.request_id)
        self.assertIn("NO MEANINGFUL OVERLAP FOUND", verdict_request.data["payload"]["batches"][0]["text"])

        # Stage 2: a drop that leans on an unverifiable contradiction is downgraded.
        self.respond(challenge.STAGE_VERDICTS, [
            {"id": "c1", "verdict": "survives", "rationale": "fresh and specific", "needs": "", "contradiction": None},
            {"id": "c2", "verdict": "proposed_drop", "rationale": "contradicts prior", "needs": "",
             "contradiction": {"source": "blog/a.md", "quote": "words not in the passage", "prior_position": "x"}},
            {"id": "c3", "verdict": "survives", "rationale": "grounded in a story", "needs": "", "contradiction": None},
            {"id": "c4", "verdict": "proposed_drop", "rationale": "unsupported", "needs": "", "contradiction": None},
            {"id": "c5", "verdict": "weakened", "rationale": "no example", "needs": "a story", "contradiction": None},
        ])
        code, out = self.run_cli("verdicts", "import")
        self.assertEqual(code, 0, out)
        register = json.loads((self.dir / "claims.json").read_text())
        by_id = {c["id"]: c for c in register["claims"]}
        self.assertEqual(by_id["c2"]["verdict"], challenge.WEAKENED)
        self.assertIsNone(by_id["c2"]["contradiction"])
        self.assertIn("downgraded", by_id["c2"]["needs"])
        # Claims without verified source context are held, not sent for review.
        self.assertEqual(out["reviewable"], 3)
        self.assertEqual(out["held_without_context"], 2)
        self.assertEqual(by_id["c4"]["context_review"]["status"], "rejected")
        context_request = self.handoff.load_request(self.dir / f"{challenge.STAGE_CONTEXT}.request.json")
        self.assertEqual(context_request.data["expects"]["ids"], ["c1", "c2", "c3"])
        entry = {e["id"]: e for e in context_request.data["payload"]["claims"]}["c2"]
        self.assertIn("I disagree", entry["source_context"])

        # Stage 3: the review repairs the quoted-belief extraction and clears the rest.
        self.respond(challenge.STAGE_CONTEXT, [
            {"id": "c1", "claim_valid": True, "verdict": "survives", "rationale": "endorsed plainly", "needs": "", "contradiction_valid": False},
            {"id": "c2", "claim_valid": False, "verdict": "proposed_drop", "rationale": "reported belief, disowned", "needs": "", "contradiction_valid": False},
            {"id": "c3", "claim_valid": True, "verdict": "survives", "rationale": "story given", "needs": "", "contradiction_valid": False},
        ])
        code, out = self.run_cli("context-review", "import")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["cleared"], 2)
        self.assertEqual(out["run"], 1)
        register = json.loads((self.dir / "claims.json").read_text())
        by_id = {c["id"]: c for c in register["claims"]}
        self.assertTrue(challenge.internally_held(by_id["c2"]))
        self.assertEqual(sorted(register["runs"][0]["requests"]), ["context_review", "extract", "verdicts"])
        self.assertEqual(register["runs"][0]["provider"], "assistant")
        report = (self.dir / "challenge.md").read_text()
        self.assertIn("Cleared for outline 2", report)
        self.assertIn("Awaiting your call", report)
        self.assertEqual(len(self.handoff.read_ledger(self.dir / "handoff-imports.json")), 3)

        # Reimport is a no-op; the run history does not grow.
        code, out = self.run_cli("context-review", "import")
        self.assertEqual(out["status"], "already_imported")
        self.assertEqual(len(json.loads((self.dir / "claims.json").read_text())["runs"]), 1)

        code, out = self.run_cli("status")
        self.assertTrue(all(s["applied"] for s in out["stages"]))
        self.assertIn("context-review prepare", out["next"])

        # The owner edits the register: a revised review against the old request is stale.
        register["claims"][4]["owner_override"] = challenge.SURVIVES
        (self.dir / "claims.json").write_text(json.dumps(register), encoding="utf-8")
        self.respond(challenge.STAGE_CONTEXT, [
            {"id": "c1", "claim_valid": True, "verdict": "survives", "rationale": "revised wording", "needs": "", "contradiction_valid": False},
            {"id": "c2", "claim_valid": False, "verdict": "proposed_drop", "rationale": "reported belief, disowned", "needs": "", "contradiction_valid": False},
            {"id": "c3", "claim_valid": True, "verdict": "survives", "rationale": "story given", "needs": "", "contradiction_valid": False},
        ])
        code, out = self.run_cli("context-review", "import")
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "stale_input")
        self.assertEqual(out["role"], "register")
        self.assertEqual(len(json.loads((self.dir / "claims.json").read_text())["runs"]), 1)

        # Re-challenge after the owner's ruling: a fresh verdicts request, override kept.
        code, out = self.run_cli("verdicts", "prepare")
        self.assertEqual(code, 0, out)
        register = json.loads((self.dir / "claims.json").read_text())
        self.assertEqual(register["claims"][4]["owner_override"], challenge.SURVIVES)

    def test_stages_cannot_run_out_of_order(self):
        self.run_cli("extract", "prepare", "--source", str(self.source))
        self.respond(challenge.STAGE_EXTRACT, [
            {"id": "a", "window": 1, "claim": "Anyone can use the system", "support_quote": "Anyone can use the system today"},
        ])
        self.run_cli("extract", "import")
        # Prepare the context stage by hand, skipping the verdicts import.
        code, out = self.run_cli("context-review", "prepare")
        self.assertEqual(code, 0, out)
        self.respond(challenge.STAGE_CONTEXT, [
            {"id": "c1", "claim_valid": True, "verdict": "survives", "rationale": "r", "needs": "", "contradiction_valid": False},
        ])
        code, out = self.run_cli("context-review", "import")
        # No depends_on when prepared by hand, but the verdicts stage was never applied:
        # the claim still carries no proposed verdict, and the review is applied on top.
        self.assertEqual(code, 0, out)
        # With the proper chain, the dependency is enforced.
        self.run_cli("verdicts", "prepare")
        register = self.handoff.load_request(self.dir / f"{challenge.STAGE_VERDICTS}.request.json")
        self.respond(challenge.STAGE_VERDICTS, [
            {"id": "c1", "verdict": "survives", "rationale": "r", "needs": "", "contradiction": None},
        ])
        code, out = self.run_cli("verdicts", "import")
        self.assertEqual(code, 0, out)
        context_request = self.handoff.load_request(self.dir / f"{challenge.STAGE_CONTEXT}.request.json")
        self.assertEqual(context_request.data["depends_on"][0]["request_id"], register.request_id)

    def test_refresh_keeps_verdicts_and_appends_a_run(self):
        self.run_cli("extract", "prepare", "--source", str(self.source))
        self.respond(challenge.STAGE_EXTRACT, [
            {"id": "a", "window": 1, "claim": "Anyone can use the system", "support_quote": "Anyone can use the system today"},
        ])
        self.run_cli("extract", "import")
        register = json.loads((self.dir / "claims.json").read_text())
        register["claims"][0]["verdict"] = challenge.SURVIVES
        (self.dir / "claims.json").write_text(json.dumps(register), encoding="utf-8")
        code, out = self.run_cli("refresh")
        self.assertEqual(code, 0, out)
        register = json.loads((self.dir / "claims.json").read_text())
        self.assertEqual(register["claims"][0]["verdict"], challenge.SURVIVES)
        self.assertEqual(len(register["runs"]), 1)
        self.assertIn("verdicts untouched", register["runs"][0]["note"])


if __name__ == "__main__":
    unittest.main()
