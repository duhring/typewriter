# Assistant workflows: the three entry points

Command sequences for the owner's assistant, one per entry point. The role and the handoff rules are in `AGENTS.md`; the contract itself is `docs/handoff-contract.md`. Each sequence has a worked example under `tests/acceptance/` that writes every request and response file involved.

## Develop: topic to CueCam bundle

Procedure and file formats: `docs/larry/development.md`. Files live in `owners-inbox/development/<slug>/`. Worked example with every response file: `tests/acceptance/develop.sh`.

```
(interview the owner; save interview.md)
(first-mile option: present the Artifact Menu — Storyboard, Concept Art, Brief, Deck, or Article Seed)
bin/pka challenge --slug S extract prepare --source owners-inbox/development/S/interview.md
(write challenge.extract-claims.response.json)      bin/pka challenge --slug S extract import
(write challenge.verdicts.response.json)            bin/pka challenge --slug S verdicts import
(write challenge.context-review.response.json)      bin/pka challenge --slug S context-review import
(owner rules; owner_override in claims.json)        bin/pka challenge --slug S refresh
(write outline.md from cleared claims)
bin/pka balance_check --slug S prepare --outline owners-inbox/development/S/outline.md
(write balance-check.analysis.response.json)        bin/pka balance_check --slug S import
(write brief.md and the card spec)                  bin/pka cuecam compose --spec-file <spec> --title "<title>"
bin/pka video_project create --title "<title>" --slug S --entry develop
bin/pka video_project sync-development --slug S; advance; approve --gate brief; advance; advance;
  attach --kind cuecam_bundle --path <bundle>; approve --gate deck; advance; advance   # -> complete
```

`bin/pka challenge --slug S status` and `bin/pka video_project status --slug S` say what to do next. The project completes on a current deck approval and the delivered bundle; no video material is required.

## Article: video or intake to a written piece

Procedure: `docs/larry/video-production.md` section 7 and `docs/larry/youtube-writing.md`. Worked examples: `tests/acceptance/article.sh intake|transcript`.

```
(transcript source)  bin/pka fetch-transcript <url or file>
                     bin/pka extract_structured transcript prepare --file owners-inbox/transcripts/<f>.md
                     (write transcript-extract.response.json; every cited timestamp must be in the transcript)
                     bin/pka extract_structured transcript import --dir owners-inbox/transcript-extracts/requests/<f>
(intake source)      interview the owner; save owners-inbox/development/S/interview.md
bin/pka video_project create --title "<title>" --slug S --entry article
bin/pka video_project attach --slug S --kind transcript|interview --path <source>
(reflective interview, thesis confirmed by the owner)  attach --kind editorial_interview; attach --kind blog_thesis
bin/pka video_project substack-brief --slug S --pov ... --reader ... --mode standalone|companion --adds ... --cta ...
(developmental draft; left unfinished for the owner)   attach --kind blog_draft; owner: approve --gate blog; advance
(owner edits by hand; you reconcile into the final)    attach --kind blog_owner_edit; attach --kind blog_final
owner: approve --gate blog_final; advance; advance     # -> complete
```

The final approval is bound to the exact file: editing `blog_final` after approval makes it stale until the owner approves again. The project delivers the final article; no video, YouTube, or publication record is needed.

## Publish: edited master to YouTube

Procedure: `docs/larry/video-production.md` sections 3 to 6. Worked example: `tests/acceptance/publish.sh owner|automated`.

```
bin/pka video_project create --title "<title>" --slug S --entry publish [--publication-mode owner|automated-private]
bin/pka video_qc <master.mp4> --project S                       # attaches the master and its QC report
owner: approve --gate master; advance                            # -> package-review needs the package first:
bin/pka pipeline package prepare --project S [--transcript <timed.md>]   # local Whisper unless a transcript is supplied
(write pipeline.package.response.json: one item, id "package", markdown = the whole package)
bin/pka pipeline package import --project S                     # chapters checked against the timeline
(write pipeline.chapter-review.response.json: one item per chapter id, supported, reason)
bin/pka pipeline chapter-review import --project S              # -> package-review
attach --kind thumbnail --path <artwork you supply>; select-title; select-thumbnail
owner: approve --gate package; advance                          # -> package-approved
owner mode:      owner uploads and releases; publication --channel youtube --url <public url>; advance; advance
automated mode:  bin/pka pipeline upload --project S (Google auth); youtube-qa ...; owner: approve --gate release;
                 advance; owner flips privacy in YouTube Studio; publication --url; advance; advance
```

The thumbnail is artwork the owner supplies or you make with an image-capable tool; nothing generates one. The project completes on the delivered materials, the recorded public URL, and (automated mode) a release approval that is still current. Uploading needs the owner's Google authorization; everything before it needs no key.
