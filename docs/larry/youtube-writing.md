# Larry YouTube and Writing Reference

Read this file when a request involves a YouTube URL, transcript, content package, or blog post.

## YouTube Content Packages

When the owner sends a **bare YouTube URL** with no comment:

1. Run:

```bash
discord-bridge/venv/bin/python3 tools/fetch-transcript.py <url>
```

This now saves the fetched transcript to `owners-inbox/transcripts/YYYY-MM-DD-video-slug.md`
and indexes it into both `knowledge_base` and `files`.
It also creates a structured extract in `owners-inbox/transcript-extracts/` with summary, timestamp highlights, reusable claims, action ideas, and tools mentioned.

2. Delegate to **Maven**
   - use the structured extract as the default briefing artifact
   - keep the raw transcript available only as backing detail when needed
   - include relevant `wiki/` page paths when the topic maps to an existing compiled page
   - normalize obvious transcript naming errors before handoff, especially CueCam variants like `QCAM`, `Q Cam`, `Q-Cam`, or `Cue Cam` -> `CueCam`
   - include any `Relevant archive context for drafting` block if one is present
3. Maven should produce:
   - 6 title options
   - YouTube description with clickable timestamps
   - thumbnail prompt
4. Save the package to `owners-inbox/youtube/YYYY-MM-DD-video-slug.md`
5. By default, send the package through Vera before final handoff
   - only skip Vera if the owner clearly asked for a rough draft, fast draft, or no QA
6. Return a concise summary, or the full package if that fits the context

When the owner explicitly asks for a **YouTube content package** such as a description, timestamps, title options, headline options, or a thumbnail prompt:

1. Fetch the transcript unless the owner already pasted one
   - fetched transcripts are saved and indexed automatically
   - fetched transcripts also produce a structured extract automatically, and that extract should be the primary handoff to Maven
2. If the owner pasted a transcript manually, create a structured extract first when the task is substantial enough to benefit from durable reuse
   - if there is no saved transcript file to extract from, distill the pasted transcript into a short structured brief before delegating
3. Delegate to **Maven**
   - lead with the structured extract or distilled brief
   - use the raw transcript only for spot-checking and extra detail
   - include relevant `wiki/` page paths when the topic maps to an existing compiled page
   - normalize obvious transcript naming errors before handoff, especially CueCam variants like `QCAM`, `Q Cam`, `Q-Cam`, or `Cue Cam` -> `CueCam`
   - include any `Relevant archive context for drafting` block if one is present
4. Maven produces the requested YouTube packaging materials, even if the owner added extra comments alongside the URL
5. By default, send the package through Vera before final handoff
   - only skip Vera if the owner clearly asked for a rough draft, fast draft, or no QA

## Blog / Substack Writing

When the owner sends a **YouTube URL with a comment or angle**, or explicitly asks for a blog post:

1. Fetch the transcript
   - this also creates a structured extract automatically
2. Delegate to **Reed** with the structured extract plus the owner's angle
   - keep the transcript available as supporting source material, not the default briefing artifact
   - include relevant `wiki/` page paths when the topic maps to an existing compiled page
   - normalize obvious transcript naming errors before handoff, especially CueCam variants like `QCAM`, `Q Cam`, `Q-Cam`, or `Cue Cam` -> `CueCam`
   - include any `Relevant archive context for drafting` block if one is present
3. Reed writes a structured post:
   - Hook
   - Video summary with timestamps
   - Owner's take
   - Key takeaways
   - Source
4. Reed saves the post to `owners-inbox/blog/YYYY-MM-DD-slug.md`
5. Reed indexes it with `discord-bridge/venv/bin/python3 tools/pka_index.py index-markdown --file <path> --category blog --tags ... --summary ...`
6. By default, send the post through Vera before final handoff
   - only skip Vera if the owner clearly asked for a rough draft, fast draft, or no QA
7. Return a summary and confirm it was saved and indexed

If the owner asks for multiple writing deliverables from the same source in one turn, stage them instead of batching the whole job into one long response.

Recommended order:

1. the main long-form piece first
2. the shorter social or packaging derivative second
3. archiving / journaling / cleanup last

Example:

- "Create a Substack article and an X thread and archive it"

Preferred flow:

1. draft and QA the Substack post
2. send it to the owner
3. complete and deliver the already-requested X version without another continuation question
4. after both are delivered, handle archive or journal updates

Staged delivery does not add a permission gate for requested drafts. Upload and publication remain separately controlled; John uploads and publishes in the owner profile.

When the owner already pasted a transcript manually:

1. Create a structured extract first if the work is substantial or likely to be saved
2. If an on-disk extract is not practical in the moment, distill the transcript into a short structured brief before handing it to Reed
3. Only skip the extract step for quick, low-stakes rough drafting

## Routing Rules

- **Bare URL** -> Maven
- **Explicit request for description / timestamps / titles / thumbnail prompt** -> Maven
- **URL + comment** -> Reed
- **Explicit request for a blog post** -> Reed
- If the owner already pasted a transcript manually, skip `fetch-transcript.py` but still prefer a structured extract or distilled transcript brief before drafting
