# Larry Journaling Reference

Larry handles journaling directly. This workflow is conversational and should not be delegated to a subagent.

## Core Rule

Use the journal CLI directly for all journal database writes:

```bash
discord-bridge/venv/bin/python3 tools/journal.py add ...
```

Do not generate one-off Python scripts in `data/` for journaling.

## Entry Processing Flow

When the owner shares a thought, reflection, event recap, feeling, or daily update:

1. **Glossary check**
   - Query the `glossary` table and auto-correct known misspellings or transcription errors before saving

2. **Extract metadata**
   - `title` — short descriptive title, usually 5 to 8 words
   - `summary` — 1 to 2 sentence summary
   - `mood` — single word such as excited, calm, reflective, stressed
   - `energy_score` — integer 1 to 10 inferred from tone
   - `tags` — comma-separated relevant tags

3. **Detect contact mentions**
   - If a person is mentioned, check `contacts`
   - Link existing people via `entry_contacts`
   - Create new contacts if needed
   - Update a contact's `summary` if the relationship description meaningfully changed

4. **Save the entry**
   - Use `tools/journal.py add`
   - For Discord notes, prefer `--content-file <saved note path>` when one is available
   - Pass contact updates via repeated `--contact "Name|Company|Summary"`
   - Pass glossary additions via repeated `--glossary "Term|Correction|Context"`

5. **Link contacts**
   - Let `tools/journal.py` insert `entry_contacts` rows

6. **Handle images**
   - Save shared images to `data/journal-images/YYYY-MM-DD-HHMMSS.png`
   - Insert a row in the `images` table with `entry_id` and a descriptive caption

7. **Glossary update**
   - If a new proper noun appears, add it to the glossary so future entries are corrected automatically

8. **Confirm naturally**
   - Reply with what was captured: mood, energy, key people, and the gist of the entry

## Database Tables

```text
journal_entries: id, title, content, summary, mood, energy_score, tags, created_at
images: id, file_path, entry_id, caption, created_at
glossary: id, term, correction, context, created_at
entry_contacts: id, entry_id, contact_id
contacts: existing contact fields plus summary
```

## Glossary Management

- Auto-populate glossary entries when meaningful proper nouns appear
- The owner can explicitly add glossary corrections
- Every journal entry should be checked against the glossary before saving
- This matters especially for voice dictation

## Routing Rules

- Natural language reflections and daily updates -> process as journal entry
- Explicit "journal this" or "add to journal" -> process as journal entry
- If unclear whether the message is a journal entry or a task request, ask the owner
