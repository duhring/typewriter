# Development Stage — Interview, Outline, Brief, Balance Check

Read this file when the owner wants to develop a new video before recording: "develop a video about X", "interview me", "help me figure out what to say", "run the intake", "outline this idea", or "balance check this outline".

This is the upstream stage the 2026-07-01 brief ("I Am Letting AI Into My Creative Process") added. It sits entirely in front of CueCam: the output of this stage feeds `tools/cuecam.py from-file` and the recording session. CueCam remains the engine; the owner remains the operator. Larry never writes the script or picks the topic — he interrogates, assembles, and checks.

For a full production-bound project, create and maintain the durable workflow
with `tools/video_project.py` and continue through
`docs/larry/video-production.md`. The brief is not accepted merely because it
exists; attach it and record John's `brief` approval against its file hash.

## Folder Layout

Each video under development gets one folder:

```
owners-inbox/development/<slug>/
  interview.md       # Q&A transcript from the intake interview
  claims.json        # claim register with verdicts (tools/challenge.py)
  challenge.md       # the challenge gate report — survivors, weakened, killed
  outline.md         # assembled outline (Winston-framed), built from survivors
  brief.md           # the video brief: outline + packaging intent + stage log
  balance-check.md   # output of tools/balance_check.py
```

The slug is short-kebab-case for the working title. Either federated peer may
produce these files and indexes them into its own local DB. After a development
pass, register every artifact that exists in the shared project manifest:

```
discord-bridge/venv/bin/python3 tools/video_project.py sync-development --slug <slug>
```

## Stage 1 — Intake Interview ("expert extraction")

Larry runs the interview directly, one question at a time, mobile-friendly. The goal is to surface what the owner actually thinks, not what he thinks he should say. Draw from this question bank, adapting to the topic; 5–8 questions is typical, stop when the position is clear:

1. What do most people get wrong about this?
2. What experience caused you to believe that?
3. What would you tell someone who disagrees?
4. What changed your mind recently, or almost did?
5. Who is this for, and what should they be able to *do* afterward? (this becomes the empowerment promise)
6. What's the one thing viewers must remember if they remember nothing else?
7. What are you tempted to include that probably doesn't belong?
8. What's the story or concrete example that proves the point?

Rules:
- One question per turn on Discord. Follow up on interesting answers before moving on.
- Do not suggest answers. Silence and "I don't know" are data — note them.
- Close by reading back the owner's position in 2–3 sentences and getting a confirm.
- Save the full Q&A to `interview.md` with a frontmatter block (`category: development-interview`, date, slug, tags).

## Stage 2 — Challenge Gate

Challenge the material *before* it is assembled, so the outline is built only from claims that survived. Producing first and challenging last (Vera at the end) means weak material is already load-bearing by the time anyone objects; this stage moves the kill-step upstream. It is the editorial sibling of the video-studio project room, which already gates on source authority before authoring.

```
discord-bridge/venv/bin/python3 tools/challenge.py --source owners-inbox/development/<slug>/interview.md --slug <slug>
```

**Status: in trial, not yet the default gate.** Run it when developing a video and judge the output; do not treat a verdict as authoritative until it has earned that on more material. The owner decides when it becomes routine.

The run does five things:

1. **Extract** — discrete claims, each with the verbatim quote backing it. The source is read in overlapping windows, so nothing past a truncation boundary is lost. Every quote is verified *in full* against the interview and given a character span.
2. **Retrieve** — each claim is matched against authority-tagged *passages*, so every piece of evidence shown to the skeptic arrives with the passage it refers to.
3. **Challenge** — bounded batches, with the response schema validated: every claim id exactly once, no invented ids, valid verdicts.
4. **Verify and adjudicate internally** — mechanically check contradiction quotes, then independently review every claim and objection using complete source paragraphs and the cited prior-position context. Quote matching does not establish endorsement. Preserve negation, quoted/reported beliefs, and hypothetical scope; a genuine contradiction requires incompatible endorsed positions under the same conditions. Reject invalid extractions internally and repair invalid objections before preparing owner questions. Failed or missing context reviews stay in the internal queue and cannot enter the outline.
5. **Resolve** — the owner rules on what is left (see below).

Verdicts:

- **survives** — quote-backed, specific, fresh ground or a deliberate sharpening of old ground
- **weakened** — worth making but currently asserted without a story; the report says exactly what it needs
- **proposed_drop** — unsupported and generic, flatly retread with nothing added, or contradicting a prior published position

### What counts as evidence

Only **John's own published work** — his blog posts and transcripts of his own recordings. Deliberately excluded:

- **watched-video transcripts** — roughly half of `owners-inbox/transcripts/` is other people's videos he watched. René Ritchie's talk is not evidence of what John thinks, and an early version of this tool cited exactly that as his "prior position."
- **compiled wiki pages** — views, not sources; counting them double-counts their sources.
- **internal working material** — real overlap, but not prior *public* ground.

Every report states the tier counts so the corpus it judged against is visible. Overlap strength is an absolute, per-claim measure (IDF-weighted coverage), not "best in this batch" — a batch of pure noise correctly yields *no* evidence rather than a "strong" best-of-noise hit.

### Stage 2b — Owner resolution (required before outlining)

The gate proposes; it does not decide. Before the outline is assembled, the owner rules on semantically validated `weakened`, `proposed_drop`, and unsupported claims. Invalid extractions and pending internal reviews remain recorded separately; they are not owner questions. The report's "Needs your call" section is that queue.

- **Record the decision** by setting `owner_override` on the claim in `claims.json` (`survives` / `weakened` / `proposed_drop`), with an optional `owner_note`. Or answer with new evidence — a story, an example — and re-run.
- **Surface findings as questions, not verdicts:** "The skeptic wants to drop the laptop-contribution claim as a platitude — do you have the story that grounds it, or does it go?"
- **The "Unsupported" section is the editorial analogue of `missing_context.md`.** Do not invent around it — ask a follow-up interview question, or drop the claim. Note the taxonomy: `owner_assertion_without_example` means the owner asserted it with no story; `quote_not_found` means the *extractor* cited a quote that isn't in the interview, which is a model error and makes the claim unverified.
- **Nothing is deleted.** Proposed drops stay in the record, same as the project room's `duplicate_log.md` rule.
- **Advisory, never blocking.** Same rule as publishing on the road: this informs the outline, it does not gate recording. If the owner wants to keep everything, the register records that choice and the work proceeds. Resolution is required before *outlining*, not before *recording*.
- If nothing clears, check extraction and context review failures internally before concluding that the interview needs another intake pass.

### Reading the run history

Verdicts are **not stable across runs** — the model is nondeterministic, and the same interview can shift several claims between runs. So:

- Every run appends to an immutable `runs` list in `claims.json` (timestamp, provider, prompt version, source hash, corpus fingerprint, proposed verdict, effective verdict). Prior runs are never rewritten.
- `owner_override` is what makes a decision durable. Treat a single run as a prompt for discussion, not a result.
- A failed challenge pass never overwrites the last successful analysis; retrieval refreshes and prior verdicts are kept, with the failure noted in the run record. Current claims are held for internal review until a complete review succeeds; owner overrides remain authoritative.

## Stage 3 — Outline

Assemble `outline.md` **from the cleared claims in `claims.json`** — claims that survived *and* have no pending owner ruling ("Cleared for outline" in the report). Not the raw interview, and not merely the latest model survivors: a claim the model liked but the owner has not resolved is still pending. Weakened claims go in only once the owner supplies what the report said they needed, or overrides. Frame it with the Patrick Winston rules the owner has internalized (`owners-inbox/presentations/presentation-prep-rules.md`):

- **Empowerment promise first.** The owner's known failure mode is saying the most important thing at the end. The answer to question 5/6 opens the outline, verbatim if possible.
- Body sections follow the owner's position from the interview, each anchored to a story or example he gave.
- Close by paying off the opening promise, not by introducing new material.
- Keep it an outline: beats and talking points in the owner's own words from the interview, never scripted prose.

## Stage 4 — Balance Check

The challenge gate and the balance check are complementary, not redundant: the challenge gate works **per claim, before assembly**, and removes material; the balance check works on the **assembled outline, after** it exists, and catches problems that only appear in the arrangement — an outline that is individually fine claim by claim but collectively retreads a prior post, or drifts on brand. Run both.

Before the brief is final, run:

```
discord-bridge/venv/bin/python3 tools/balance_check.py --outline owners-inbox/development/<slug>/outline.md --slug <slug>
```

It reports retread risk against the archive (blog, transcripts, briefs, wiki), contradiction candidates with verbatim quotes, and brand drift against the brand-system doc. Then:

- Verify any high-severity contradiction quote against the cited source before surfacing it (Performed/Verified/Rejected discipline).
- Surface the findings to the owner as questions, not verdicts: "You covered this ground in the March PKM post — same take, or has it moved?"
- Deliberate retreads are fine when chosen on purpose; record the choice in the brief.
- If the video is publish-bound, the reviewed report goes through Vera with the rest of the package.

## Stage 5 — Video Brief

`brief.md` is what the owner takes into the recording session. It bundles:

1. **Empowerment promise** (one or two sentences, up top)
2. **Outline** (or a pointer to `outline.md`)
3. **Packaging intent** — 2–3 working titles and a thumbnail concept, drafted Maven-style *before* recording so packaging shapes the talk instead of being retrofitted. Maven still produces the real package downstream from the transcript; this is intent, not the deliverable.
4. **Visual identity constraints** — pull the relevant rules from `owners-inbox/brand-system.md` (colors, lower-third, thumbnail conventions) instead of deciding ad hoc per video. If the video gets a HyperFrames motion pass, seed the project's `motion_brief.json` from these same constraints.
5. **Balance decisions** — one line per accepted retread/contradiction finding and what the owner decided.
6. **Stage log** (see below).

Handoff to CueCam when the owner is ready:

```
discord-bridge/venv/bin/python3 tools/cuecam.py from-file --file owners-inbox/development/<slug>/outline.md --title "<working title>"
```

## Stage Log — measuring the experiment

The whole point of the brief is a baseline-vs-after comparison, so every developed video records its own timeline. Keep this table in `brief.md` and fill it as stages complete:

| Stage | Timestamp | Notes |
|---|---|---|
| idea captured | | |
| interview done | | # questions, minutes |
| challenge gate run | | cleared / weakened / proposed drops, overrides |
| outline done | | |
| balance check run | | findings accepted/rejected |
| recorded | | # takes |
| published | | video URL |

After publishing, Larry adds one canonical task-record note comparing this video's stage log against the 2026-07-01 baseline (ad hoc prep, outline-in-head). Retention/view data gets appended when the owner shares it. Dreamer's weekly refinement pass may promote recurring findings into SOP updates.

## Boundaries

- Every stage runs on either standalone peer. Portable files and project manifests sync through git; each peer maintains its own local index.
- Pull before taking ownership of a stage; commit and push the manifest at the stage boundary. Do not edit one project simultaneously on both machines.
- Larry asks questions and assembles; he does not invent positions, write scripted prose, or soften the owner's take. If an interview answer is thin, ask again rather than padding.
- Skip stages when the owner says so ("just outline this", "skip the interview") — the folder layout still applies to whatever is produced.
