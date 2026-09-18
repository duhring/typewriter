# Manual-First Video Production Workflow

Use this reference for a video that moves from owner interview through CueCam,
manual editing, private YouTube review, Substack adaptation, and measurement.
The workflow coordinates existing tools; it does not replace them.

## Durable Project Contract

Create one project before substantive development:

```bash
discord-bridge/venv/bin/python3 tools/video_project.py create \
  --title "Working Title" --slug working-title \
  --summary "What this video is intended to accomplish"
```

The source of truth is:

```text
owners-inbox/video-projects/<slug>/project.json
```

`project.md` is the indexed human-readable view. Both federated peers share the
JSON and markdown through git while maintaining local DB indexes. Media stays
out of git and is referenced by path plus SHA-256 identity. Never hand-edit the
JSON; use the CLI. One peer owns a project stage at a time.

### Generated views

Every mutation regenerates two things from the manifest:

```text
owners-inbox/video-projects/<slug>/project.md          # the run, in pipeline order
owners-inbox/video-projects/<slug>/media/<kind>.md     # one per non-Markdown artifact
```

`project.md` renders artifacts in production order grouped by stage, links every
one as a note, and shows the inputs and outputs the designed pipeline expects for
each. A struck-through kind is one the design expects but the run has no artifact
for.

Read those columns as **expectation, not history**. The map cannot know how a
given run was actually made — imported runs, one-shots, and runs predating a
stage all look the same to it. A gap between expected and present is a divergence
worth opening; it is never, on its own, the reason a run stalled. Recording what
actually produced what is a separate mechanism that does not exist yet.

Artifacts Obsidian cannot open as notes — recordings, masters, `.cuecam` bundles,
thumbnails, `claims.json` — get a wrapper note under `media/` carrying the
absolute location, SHA-256, size, format, duration, gates citing it, and its
links to the expected inputs and outputs that are present on that run. Wrapper
notes are the only place this tool writes expected-flow links into the vault,
because they are the only files it owns; it never writes frontmatter into
artifacts produced by other workflows.

The designed flow is declared once, in `ARTIFACT_FLOW` in
`tools/video_project.py`. `STATE_SEQUENCE` and `GATE_ARTIFACTS` describe
permitted progression and the evidence a gate requires; neither says what feeds
what. Change the flow there, never in a note — and keep it in step with section
7 below, since the two are only as consistent as whoever edits them.

Both files are generated. Never hand-edit them; regenerate with:

```bash
tools/video_project.py render --slug <slug>
```

## State Sequence

```text
developing → brief-review → brief-approved
→ deck-review → deck-approved → recorded → clean-ready → manual-edit
→ master-qc → package-review → package-approved
→ private-upload → youtube-qa → release-approved → published
→ blog-review → blog-approved → complete
```

`advance` only permits the next state and refuses to cross a missing or stale
approval gate.

When a known issue prevents safe progress, make it explicit. Active blockers
prevent every state transition until resolved:

```bash
tools/video_project.py block --slug <slug> \
  --message "Need the licensed source image" --owner John
tools/video_project.py resolve-blocker --slug <slug> --id <id> \
  --resolution "John supplied an owner-created replacement"
```

## 1. Develop and Approve the Brief

Follow `docs/larry/development.md` for interview, challenge, outline, balance
check, and brief creation. Register the standard folder in one pass:

```bash
tools/video_project.py sync-development --slug <slug>
tools/video_project.py advance --slug <slug> --to brief-review
```

After John reviews the exact brief:

```bash
tools/video_project.py approve --slug <slug> --gate brief --by John
tools/video_project.py advance --slug <slug> --to brief-approved
```

If the brief or any attached supporting development artifact changes afterward,
its hash changes and the approval becomes stale.

## 2. Build and Approve the CueCam Deck

Generate the `.cuecam` bundle with `tools/cuecam.py`, then attach and review it:

```bash
tools/video_project.py advance --slug <slug> --to deck-review
tools/video_project.py attach --slug <slug> --kind cuecam_bundle --path <bundle.cuecam>
tools/video_project.py approve --slug <slug> --gate deck --by John
tools/video_project.py advance --slug <slug> --to deck-approved
```

Reference images may be recorded with `--source` and `--rights`. Package
approval later refuses thumbnail references whose rights field is empty.

## 3. Record, Clean, and Edit Manually

Attach the raw recording, advance, then use silence-only cleanup:

```bash
tools/video_project.py attach --slug <slug> --kind raw_recording --path <recording.mov>
tools/video_project.py advance --slug <slug> --to recorded
discord-bridge/venv/bin/python3 tools/clean_video.py <recording.mov> --no-stumbles
tools/video_project.py attach --slug <slug> --kind cleaned_video --path <recording_cleaned.mp4>
tools/video_project.py advance --slug <slug> --to clean-ready
tools/video_project.py advance --slug <slug> --to manual-edit
```

Stop. John edits externally and identifies the final export.

## 4. Final-Master QC and Approval

Run the complete QC scan; `--quick` deliberately cannot pass the master gate:

```bash
discord-bridge/venv/bin/python3 tools/video_qc.py <final-master.mp4> --project <slug>
tools/video_project.py advance --slug <slug> --to master-qc
```

The report checks decode integrity, streams, duration, resolution, codec, frame
rate, sample rate, rotation, and black/silent edges. John must still complete
the human playback checklist, then approve the exact master and QC report:

```bash
tools/video_project.py approve --slug <slug> --gate master --by John
```

For a video whose brief, deck, recording, and manual edit were completed before
a project record existed, create the project, run the same full QC with
`--project`, then enter at the master gate without fabricating upstream
artifacts or approvals:

```bash
tools/video_project.py import-master --slug <slug> \
  --note "Externally produced video imported after manual edit."
```

This command only accepts a new project in `developing` state with no approvals,
an attached final master, and a passing attached QC report. Owner playback
approval is still required afterward.

## 5. Package Without Uploading

```bash
discord-bridge/venv/bin/python3 tools/pipeline.py package \
  --project <slug> \
  --reference-image <path> --reference-rights "owner-created"
```

This transcribes the approved master, creates and indexes the YouTube package,
optionally generates a thumbnail candidate, attaches everything, and stops in
`package-review`. It never uploads.

Review all materials together:

```bash
tools/video_project.py review-package --slug <slug>
tools/video_project.py select-title --slug <slug> --option 2 --title "Chosen title"
tools/video_project.py select-thumbnail --slug <slug> --path <thumbnail.png>
tools/video_project.py approve --slug <slug> --gate package --by John
tools/video_project.py advance --slug <slug> --to package-approved
```

## 6. Private Upload and YouTube QA

```bash
discord-bridge/venv/bin/python3 tools/pipeline.py upload --project <slug>
```

The upload is always private and applies the selected thumbnail. Then review in
YouTube and record the checklist. Required items:

- `playback`
- `hd-processing`
- `title`
- `description-links`
- `chapters`
- `thumbnail-mobile`
- `captions`
- `audience`

Optional/manual Studio items are `monetization`, `playlist`, `end-screen`, and
`cards`. Mark a legitimately irrelevant item with `--na-item`.

```bash
tools/video_project.py youtube-qa --slug <slug> \
  --pass-item playback --pass-item hd-processing --pass-item title \
  --pass-item description-links --pass-item chapters \
  --pass-item thumbnail-mobile --pass-item captions --pass-item audience
tools/video_project.py approve --slug <slug> --gate release --by John
tools/video_project.py advance --slug <slug> --to release-approved
```

Changing privacy in YouTube Studio remains a manual owner action. Afterward,
record the public URL and advance:

```bash
tools/video_project.py publication --slug <slug> --channel youtube --url <url>
tools/video_project.py advance --slug <slug> --to published
```

## 7. Editorial Loop and Substack Adaptation

The published video is the input to a second editorial pass, not the end of the
run. Its stages are registered artifacts so the loop is visible on the project
page instead of living only in a chat.

**7a. Reflective interview.** After the master transcript exists, run a short
five-question interview about what the recording actually turned out to be. This
is Larry interrogating, same as the intake interview — not Reed drafting.

```bash
tools/video_project.py attach --slug <slug> --kind editorial_interview \
  --path owners-inbox/development/<slug>/editorial-interview.md
```

**7b. Thesis, confirmed by John.** Compress the reflection into one page. John
confirms the thesis before anyone drafts against it.

```bash
tools/video_project.py attach --slug <slug> --kind blog_thesis \
  --path owners-inbox/development/<slug>/blog-thesis.md
```

**7c. Substack brief.** A reader-first editorial handoff, not a transcript
conversion:

```bash
tools/video_project.py substack-brief --slug <slug> \
  --pov "John's direction" --reader "Intended reader" \
  --mode companion --adds "What the article adds" --cta "Desired action"
```

**7d. Draft, owner edit, reconciliation.** Reed drafts from the confirmed thesis,
the Substack brief, and the transcript. The first draft is developmental and
deliberately unfinished — no automatic Vera pass; it exists to be edited by hand.
The three drafts stay separate artifacts because the delta between them is the
evidence this process was built to produce.

```bash
tools/video_project.py attach --slug <slug> --kind blog_draft --path <first-draft.md>
tools/video_project.py advance --slug <slug> --to blog-review
tools/video_project.py attach --slug <slug> --kind blog_owner_edit --path <owner-edit.md>
tools/video_project.py attach --slug <slug> --kind blog_final --path <final.md>
tools/video_project.py approve --slug <slug> --gate blog --by John
tools/video_project.py advance --slug <slug> --to blog-approved
```

**7e. Publish.** Attach the published file, then record the URL:

```bash
tools/video_project.py attach --slug <slug> --kind published_blog --path <published.md>
tools/video_project.py publication --slug <slug> --channel substack --url <url>
tools/video_project.py advance --slug <slug> --to complete
```

The `blog` gate still requires only `blog_draft`, and the loop still runs inside
the existing `blog-review` → `blog-approved` window. `blog_owner_edit`,
`blog_final`, and the thesis confirmation are recorded but not yet gated; whether
any of them should become a gate is a question for the first few runs that use
this, not a decision to make in advance.

## 8. Measurement

Record manual 48-hour, 7-day, and 30-day snapshots:

```bash
tools/video_project.py metrics --slug <slug> --window 7d \
  --views 1200 --ctr 4.8 --average-view 3:42 --substack-opens 600
```

YouTube Analytics automation is a later enhancement. The durable metrics shape
exists now without requiring broader OAuth scopes.

## 9. Record Judgment While It Happens

The ledger records what was produced and approved. It has never recorded why a
run turned out the way it did, which is why a finished run can show that it
diverged from the pipeline but never explain it. Two kinds of note close that
gap, and they are different things.

**A decision is a closed fact inside one run.** Record it when the run turns on a
call rather than on the procedure — a re-record, a rejected take, a manual fix,
a stage deliberately skipped. Record it as it happens; a decision reconstructed
weeks later is a guess.

```bash
tools/video_project.py decision --slug <slug> --stage master \
  --summary "Re-exported the master" \
  --why "59px HDMI black bar in the first export" \
  --artifact final_master --by John
```

Repeating a decision is allowed: the same call can genuinely be made twice in
one run, and both are events worth having.

**An observation is a claim about the pipeline itself**, evidenced by this run
but outliving it. It carries a status, and the transitions are enforced rather
than merely described:

```text
proposed -> accepted -> implemented -> verified
   |            |            |
   +------------+------------+--> rejected --> proposed
```

`verified` is terminal — a later regression is a new observation, not a reopened
one. Nothing can skip a step, so an observation cannot present itself as closed
without the evidence for each stage of getting there.

```bash
tools/video_project.py observation --slug <slug> --stage editorial \
  --summary "The editorial interview came too late to shape the YouTube framing" \
  --proposes "Move it immediately after master transcription" \
  --artifact transcript --artifact youtube_package --by John
```

An observation is evidence, not a command. It becomes a change the usual way:
John directs it, the governing doc or tool changes, and the change is tested on
a later run. Closing the loop is enforced, and each flag belongs to exactly one
transition — it cannot be supplied early and banked to satisfy a later step:

- `--changed` is accepted only when moving to `implemented`, and is required there.
- `--verified-by-run` is accepted only when moving to `verified`, and is required
  there. The run must exist, must not be the run that raised the observation, and
  must have been created after the observation was raised.

```bash
tools/video_project.py observation-status --slug <slug> --id <id> --status accepted
tools/video_project.py observation-status --slug <slug> --id <id> --status implemented \
  --changed docs/larry/video-production.md
tools/video_project.py observation-status --slug <slug> --id <id> --status verified \
  --verified-by-run <later-slug>
```

Stages are `development`, `deck`, `production`, `master`, `packaging`, and
`editorial`. Each stage names the procedures that govern it, most specific
first, so an observation about a stage points at the file a change would land in
— see `STAGE_GOVERNANCE` in `tools/video_project.py`. Stages draw on more than
one procedure: `editorial` is defined by sections 7 and 9 of this file and
governed secondarily by `docs/larry/youtube-writing.md`, which is where Reed and
Maven's drafting rules live.

## 10. Retrospective and the Cross-Run View

After a run completes, generate its retrospective:

```bash
tools/video_project.py retrospective --slug <slug>
```

It reports divergence from the designed flow, rework (artifacts replaced after
first attachment), owner intervention, the decisions and observations recorded,
and the open observations as candidate workflow changes. Where nothing was
recorded it says so plainly rather than inferring — an empty decision log means
the judgment was not captured, never that none was exercised.

Generating it also writes `production-map.canvas` beside it: an Obsidian canvas
with one group per pipeline stage and one card per artifact the run touched,
superseded thumbnails included, with publications as link cards and media
outside the vault as path cards. Open it in Obsidian for the visual view of the
run; it is regenerated from the manifest on every retrospective and is never a
source of truth. The retrospective links to it. Generating the retrospective
also re-renders `project.md`, which then carries a forward link to the
retrospective so the run page is not a dead end.

The cross-run view is Dreamer's, compiled from every manifest:

```bash
discord-bridge/venv/bin/python3 tools/wiki_compile.py pipeline
```

That writes `wiki/operations/video-editorial-pipeline.md`: a run table, a stage
coverage matrix, the most frequent divergences, and every open observation with
the procedure it would change. A manifest that cannot be read is never dropped
quietly — it is listed under "Manifests excluded from this page", so the page
cannot claim coverage it does not have. The split of ownership is deliberate —
`video_project.py` owns one run, `wiki_compile.py` owns what accumulates across
runs. Neither writes into the other's pages.

## Explicit One-Shot Exception

For a low-stakes video where John explicitly declines manual gates:

```bash
discord-bridge/venv/bin/python3 tools/pipeline.py one-shot --privacy private --yes
```

Bare `pipeline.py` no longer launches the entire publish path accidentally.

## Requested outputs and owner publication

New CLI projects default to `--outputs video --publication-mode owner`. For a
video and companion article use `--outputs video article`. Recording and manual
editing remain human stages, and master/package review still applies. Complete
requested drafts without another continuation question.

In owner mode (John's “I upload and publish” profile), `package-approved` advances
to `complete` for video-only work, or to `blog-review` and then `complete` for
combined work. Combined completion requires the attached `blog_draft` as well as
the final master, timed transcript, package and selected thumbnail. The article
is a delivered draft for the owner's editing; this path creates no blog or
release approval. `complete` records **materials delivered**, with artifact
identities, and does not mean publication happened. Record actual publication
later with `publish --channel youtube|substack --url ...`; supply `--published-at`
only when the publication date is known. Otherwise the confirmation time is
recorded separately and the publication date remains unknown. Do not prompt the owner
repeatedly for a URL. Platform playback, processing, captions and final settings
belong in the owner's publishing handoff.

`--publication-mode automated-private` retains the reusable private-upload,
YouTube QA and release-approval path. It requires publication confirmation for
each requested output before completion. Video-only work has no blog gates.
Do not use automated upload in the owner profile.

Compatibility: manifests without `requested_outputs` keep the original sequence
and completion contract; reading or rendering them performs no migration. The
Python creation API also retains its legacy default for existing callers. To
explicitly change eligible existing work, use `configure-outputs --slug SLUG
--outputs video [article] --publication-mode owner|automated-private`. This adds
an output-selection event and preserves all prior history, artifact identities,
approvals and publications. Completed projects or states absent from the chosen
path are rejected, never rewound or filled with fictional skipped approvals.

Packaging keeps all supplied final-master timed segments and validates chapter
boundaries plus labels against their corresponding content before saving. The
current transcript formatter groups roughly 30 seconds of speech; chapter times
retain that granularity. Inputs above 60,000 characters fail explicitly and need
an explicit segmented editorial pass preserving the full timeline; nothing is
silently truncated. Semantic checks require the configured model and are not a
guarantee of factual accuracy.
