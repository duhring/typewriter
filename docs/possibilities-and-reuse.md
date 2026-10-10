# Recognize work, offer possibilities, preserve reusable choices

This is a conversation behavior shared by the develop, article, and publish workflows. It also applies to untracked first-mile artifacts. Its purpose is to make capabilities and accumulation visible while leaving the user's intent open.

## Fresh start

A new installation has no personal corpus. Make the conversational entry visible in README and bootstrap completion: “Let’s start a new Typewriter run.” The assistant must have access to the folder and `AGENTS.md`; an unrelated chat does not automatically acquire it. Begin with “What would you like to explore? A rough idea, question, or observation is enough.” Do not imply that earlier projects, preferences, or accumulated learning exist.

At an open-ended run start, include **Check for Typewriter updates** alongside starting a new idea, continuing actual saved work, and ending the session. Follow [updating-typewriter.md](updating-typewriter.md). Do not check or install automatically; do not interrupt a topic already chosen.

## Feedback before the next-step menu

When returning artwork, first ask “How does this look? What would you like to change?” Wait for the owner’s response. If they request changes, complete them and ask for feedback on the corrected artifact. After acceptance or a request to consider possibilities, offer a small relevant menu: extend the interview, create a new interpretive image, create a new storyboard or visual sequence, continue toward publication when useful, or end the session. Do not assume acceptance from silence or offer unrelated options while a correction is still pending. An explicit next task is authorization to do that task without an extra menu.

Completing packaging or publication is also a handoff. Recognize the saved work and offer fresh possibilities; completion of one artifact does not require the creator to end the run. Conversely, ending the session is always valid and must not trigger more questions.

## Publication links

When the owner says they published, ask “Would you share the published link so I can connect it to this work?” Ask only for missing URLs; do not request the known YouTube link again when the blog link is missing. Preserve the supplied URL and source of confirmation. A Substack Note or announcement is distinct from a full article; invite the article URL too if there is a separate article.

Use the normal publication tool where its prerequisites are actually met. Otherwise index a publication receipt and record the owner’s report with a project decision, preserving any missing final-file or approval evidence. Never mark an unseen local draft as the exact published version, invent checklist results, or infer final approval from artwork acceptance. If retrieval is unavailable, report publication as owner-confirmed rather than independently verified.

## Natural checkpoints

Offer a brief reflection after delivering the first useful artifact, after incorporating a meaningful owner correction, when resuming earlier work, or at a requested session close. Do not interrupt an active interview, insert a menu after each command, or repeat a menu the user has declined. A concrete instruction from the user takes precedence: complete it before offering unrelated possibilities. A session-end instruction means stop; any reflection at close is informational and must not invite another task that delays closing.

Use this shape:

1. **What is here.** Name the actual source material, artifact, correction, or decision with a source link. Distinguish saved files, changed files, missing files, drafts, and recorded approvals. A project state alone does not prove an artifact exists or is approved.
2. **What it enables.** Offer two to four optional possibilities relevant to the material, each in terms the creator understands. Include “leave it here” when asking for a choice. If useful, offer the full capability menu on request. Do not present the recorded next_action as the user's intention.
3. **What remains available.** Explain concretely what can be used in a later run. Mention a prior contribution influencing this run only when the actual source or decision was used and can be cited. More files do not by themselves prove improved outcomes.

Example:

> Your interview, brief, and storyboard are saved. The brief is still a draft, and you have not approved a presentation direction.
>
> You could refine the brief, explore another angle in an interview, try an article seed, or leave it here. Nothing is selected automatically.
>
> When you return, these materials provide a starting point—you do not have to reconstruct the interview.

## Read-only evidence helper

```
bin/pka project_reflection --slug S
bin/pka project_reflection --slug S --format json
```

This reports project artifacts and visible development files, their availability, recorded project decisions, and optional capabilities. It works before project creation when a development folder exists. The full output is evidence for the assistant, not a requirement to show every option in chat. The assistant should select a small relevant set while remaining open about the user's intent.

The helper never indexes files, advances state, creates approval, records a preference, or invokes an offered capability. Small tracked files are hash-checked. Large media availability is reported without revalidating its hash. Recorded approval gates are historical records, not a claim that approval remains current; use the project approval checks when executing a gated workflow.

If the artifact exists but the registered next_action says to create it, show the artifact and identify the stale workflow hint. Do not repeat it as an instruction. An available file is not necessarily a reviewed or indexed artifact.

For work outside tracked editorial projects, use the same reflection shape with the actual source files and canonical task record; do not create a video project merely to obtain a reflection.

## Preservation is separate from codification

- **Saved project material:** interview, draft, image, evidence, or decision preserved in its existing location. This is automatically available to build on after normal indexing.
- **Proposed reusable material:** the user or assistant identifies an artifact or pattern that might help again. This remains a proposal.
- **Accepted reusable material:** the user explicitly chooses what to reuse and reviews the intended scope. Only then may it become an example, template, procedure, or preference.

One run never establishes a permanent preference. Repeated behavior is evidence for an invitation, not authorization to promote a rule. Explicitly chosen one-off examples or templates do not require repeated runs. Do not say the model has learned or trained on these records.

At an appropriate checkpoint, ask a concrete question such as:

> Would you like to keep this brief's structure as a template for this project, or simply preserve it as an example?

Do not ask for additional approval if the user has already explicitly requested that exact reusable artifact and scope. The user's answer determines the action; silence does not authorize codification.

## Durable codification procedure

After an explicit choice:

1. Identify **kind** (example, template, procedure, preference), **scope** (this project, a named workflow, or wider), the exact source paths and hashes, and who chose it. A request to reuse an example does not authorize changing global instructions.
2. Prepare the reusable artifact and an accompanying receipt under `owners-inbox/reusable/<slug>/`. Keep project-specific facts out of generalized templates; retain the source example separately. The receipt must state `status: proposed` until the user accepts the concrete result, unless their existing instruction already authorizes its exact content and scope.
3. The receipt records the chosen kind and scope, source paths and hashes, result path and hash, the user's actual instruction or acceptance, and any limitations. Do not manufacture acceptance from a project approval gate that covered a different artifact.
4. Index both Markdown artifacts using `bin/pka pka_index index-markdown --file PATH --category reference --tags reusable,kind-KIND,scope-SCOPE`. Use normal project decision or task-record tools to record the actual user choice. Requests or suggestions must not be recorded as completed decisions.
5. For future use, retrieve and read the receipt, verify its accepted status and scope, and inspect the linked result. Cite its influence when it materially shapes an output. Changed source material does not silently change an accepted template; changed result bytes require review before claiming the receipt still covers it.
6. Procedure or preference changes must be routed through the existing SOP/owner-instruction process. Keep the proposal and its evidence available. Editing owner-controlled `CLAUDE.md` still requires the owner's authorization for that edit.

A useful receipt contains:

```
kind: template
scope: project:example-project
status: proposed
chosen_by: <actual user>
source_paths: <paths and hashes>
result_path: <path>
result_sha256: <hash>
acceptance: <actual instruction or later acceptance; never invented>
limitations: <what this does not generalize>
```

No new preference store or automatic promotion is introduced. The durable spine remains files, indexing tools, and local SQLite.

## Verification examples

- A newcomer with only an interview can discover several first-mile artifacts and continue interviewing without recording video.
- A returning creator sees their actual saved work rather than a guessed new task.
- Missing or changed artifacts are not presented as current deliverables.
- A correction is described and preserved without becoming a global rule.
- No reflection selects a possibility, advances a gate, or modifies the database.
- Stopping is a valid choice. Reusable behavior applies only after the user's explicit choice within the recorded scope.
