# Outside research, orientation, and the production brief

Market conditions establish a baseline; the creator's experience, values, and intent supply production direction. Research informs choices without selecting the owner's purpose. People can begin with a frustration before they can specify a finished solution. Conversation and first artifacts help them discover what has value to them.

## Orientation before intake

When the direction is still open, begin with a short orientation conversation: what is the person trying to do, what gets in the way, what would a useful result enable, and where does their own touch belong? Ask one question at a time, following the person's experience rather than completing a questionnaire.

Bring in a proportionate outside-research pass early enough to inform direction: current market conditions, relevant capabilities, how others address similar needs, and reactions to recent publications. First hear enough of the person's situation to make the search relevant. Return examples as possibilities and explain their relevance and limits. Do not impose a niche, commercial goal, or demand test. Someone with a clear topic can proceed directly to intake; a request for an immediate artifact takes precedence over additional discovery.

Research here expands what the person can imagine. After a direction is chosen, the intake develops the person's contribution. Use the discovery pacing, read-back, and optional depth choices in [development.md](development.md). A small private solution, exploratory artifact, and choosing to stop are valid outcomes.

## Preserve the interview brief

Save `interview.md` and an `interview-brief.md` describing the person's intended contribution, experience, audience, purpose, and unresolved questions. Preserve this baseline when proposing new framing. Report personal accounts as personal accounts; distinguish them from verified facts and the assistant's interpretation. Do not invent a story, a preference, or a polished position to fill a gap.

## Research for production direction

For work intended for an audience, examine responses to the owner's recent relevant publications and comparable work by others. Look for what people choose, question, misunderstand, find useful, or say remains unmet. Use public sources and authorized analytics where available. Begin with a small, relevant sample rather than exhaustive research; deepen it when uncertainty would materially change a recommendation.

Save `audience-research.md`. For each important finding record its source URL or local reference, publication and observation dates, observed evidence, audience/sample, interpretation, and limitations. Use original sources where available. Refresh material when resuming if its age could change the decision. Compare publication ages, channel scale, formats, and traffic sources where possible. Mark unavailable analytics as unknown.

Surveys about adoption are background context, not evidence of current consumption. Vendor claims and selected success stories are not proof of demand. Views and comments are observations with competing explanations; they do not establish a guaranteed view floor or a causal recipe. Never claim to have reviewed the owner's audience reactions when those sources were unavailable. Keep any resulting recommendations provisional.

Public web research is part of this workflow; do not publish, contact people, or submit private interviews or unpublished drafts to external search/services merely to perform it. Search with the minimum public or generalized context needed. Use private data only within the user's authorized scope. Keep research records local through the normal durable file/index paths.

## Proposed production brief

Use `brief.md` for the production brief, retaining the existing tracked artifact and brief approval. Include:

1. The original contribution and intended viewer/reader outcome, with a link to the preserved interview brief.
2. A concise, dated research baseline: relevant observations, sources, and uncertainty. Include the decisive evidence and limitations directly in this file so the approved bytes contain the recommendation's basis.
3. Proposed framing, opening, examples, outline, and packaging intent. Explain what changed from the interview brief, why, and any tradeoffs or valuable ideas the external evidence might overlook.
4. A map from each material viewer promise to the explanation, example, or demonstration that would fulfill it. Unverified or unavailable demonstrations remain explicit production needs.
5. Where the owner's finishing pass belongs: delivery and take selection, title and description wording, and other choices the person has said matter. Prepare work that leaves room for those choices; do not infer approval from a fluent draft.

Bring this concrete proposal back to the owner. Their response shapes the brief used for the deck and recording. Preserve the original baseline; a revised production brief does not rewrite interview history. Apply the existing challenge and balance reviews to material new factual claims and substantive outline changes. Outside interest does not clear a claim or replace the owner's position.

## Existing tools and approval limits

This is assistant-led guidance, not a new CLI stage or response schema. Keep the existing hash-bound handoff formats. Save supporting Markdown under `owners-inbox/development/<slug>/` and index it with the existing `pka_index index-markdown` tool using category `research` for research and `reference` for orientation/interview briefs. `sync-development` does not automatically attach the new supporting filenames. Do not claim it does.

The existing `brief` gate binds `brief.md` and supported attached development artifacts; it does not separately hash these new research files. Carry the material research basis in `brief.md`. If a source or research revision changes the proposed direction, update the brief and seek a current brief approval through the existing process. Never invent additional gate coverage or record approval on the owner's behalf. First-mile requested drafts still proceed without creating a new gate.

At final packaging, read the production brief and research alongside the actual master transcript. The master governs what can honestly be promised and timestamped. Keep the existing package response format; put rationale in the project notes rather than adding unrecognized fields. Preserve the owner's wording and finishing role.

## Learn through use

Use the existing manual metrics windows (48 hours, 7 days, 30 days), decisions, observations, and retrospective. Record what was expected, what happened, plausible explanations, and one proposed change to try. Distinguish production convenience, personal value, and audience outcomes. Missing data and small samples remain explicit; no automatic preference or global rule follows from one successful example. Reuse follows [../possibilities-and-reuse.md](../possibilities-and-reuse.md).
