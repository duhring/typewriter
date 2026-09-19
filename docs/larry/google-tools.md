# Larry Google and Tooling Reference

Read this file when handling morning briefs, calendar requests, Google Docs, Google Sheets, or Google Maps.

## Morning Brief

When the owner asks for a brief:

1. Run:

```bash
bin/pka morning-brief
```

2. Return the output directly
3. The brief now includes recent indexed activity such as session logs, blog posts, transcript extracts, meeting extracts, and CueCam deliverables when available
4. When recent saved coding session logs include change metrics, the brief should also include a `Code Activity` section with files changed and lines touched
5. Do not delegate this to a subagent

## Auth Doctor

When the owner wants a quick health check for Google tool auth, run:

```bash
bin/pka auth_doctor
```

You can also check individual services:

```bash
bin/pka gcal doctor
bin/pka gdocs doctor
bin/pka gsheets doctor
```

Use this before a brief or when Calendar, Docs, or Sheets start failing unexpectedly.

## Calendar Management

Larry handles calendar operations directly using `tools/gcal.py`.

### Common Commands

```bash
# List today's events
bin/pka gcal list

# List a date range
bin/pka gcal list --from 2026-03-26 --to 2026-03-28

# Create an event
bin/pka gcal create --title "Meeting with Sarah" --start "2026-03-26 14:00" --end "2026-03-26 15:00" --description "Discuss project"

# Search events
bin/pka gcal search "Sarah"

# Find free time
bin/pka gcal free --from 2026-03-26 --to 2026-03-27

# List calendars
bin/pka gcal calendars
```

Interpret the JSON output naturally for the owner.

After creating calendar events, also log them in the `meetings` table in `data/pka.db`.

When the owner wants structured takeaways from meeting notes or a meeting transcript, use:

```bash
bin/pka extract_structured meeting --file owners-inbox/calendar/meeting-notes.md --title "Meeting Title" --date 2026-04-12 --attendees "Name 1, Name 2"
```

If there is already a `meetings.id` row that should be updated, add `--meeting-id ID` so the extracted summary and action items sync back into `data/pka.db`.

## Google Docs

Larry and team members can create native Google Docs using `tools/gdocs.py`.

### Common Commands

```bash
# One-time auth
bin/pka gdocs auth

# Auth health
bin/pka gdocs doctor

# Create from local file
bin/pka gdocs create --title "My Post" --file owners-inbox/blog/2026-03-23-post.md

# Create from inline content
bin/pka gdocs create --title "Meeting Notes" --content "# Notes\n- Item 1\n- Item 2"

# List docs
bin/pka gdocs list

# Read a doc
bin/pka gdocs get DOC_ID

# Search docs
bin/pka gdocs search "project proposal"

# Share a doc
bin/pka gdocs share DOC_ID --email someone@example.com --role writer

# Create and save the PKA folder as default
bin/pka gdocs mkdir "PKA" --save-as-pka

# List folders
bin/pka gdocs folders

# Move a file into a folder
bin/pka gdocs move FILE_ID FOLDER_ID
```

### Usage Notes

- When the owner says "save to Drive" or "put it in my Drive", create a Google Doc after saving locally
- Reed can push blog posts to Drive after writing them
- New docs and sheets should go into the saved PKA folder when that default is configured

## Google Drive Search

When the owner references a Drive asset by name (e.g., "the 3bird video", "the rough cut from yesterday") and you don't already have the file ID or local path, search Drive instead of guessing or asking the owner to download:

```bash
bin/pka gdrive search --name "3bird"
bin/pka gdrive search --name "for bird" --kind video
```

Returns JSON with matches (id, name, mimeType, size, modifiedTime, webViewLink). Filter by `--kind {video,image,audio,pdf,doc,sheet,slide,folder}` when the owner's intent is clear.

### Downloading for downstream tools

If a single match is unambiguous and you need the file locally (e.g., to pass as `--asset` to `tools/cuecam.py compose`):

```bash
bin/pka gdrive search \
  --name "3bird" --kind video \
  --download owners-inbox/cuecam-assets/
```

The `downloaded` field of the JSON response gives you the local path to use.

### Scope caveat

Current Drive auth uses `drive.file` scope, which only sees files this app created or files the owner explicitly opened/picked with this app. Files the owner uploaded directly to Drive may return zero matches even when they exist. When the search returns empty but the owner insists the file is there, two options:

- Ask the owner to open the file once via a Drive picker integration (this grants the app per-file access).
- Flag the limitation to the owner; broader scope (`drive.metadata.readonly` or `drive.readonly`) requires a re-auth flow we have not yet wired up.

Do not silently fail — surface the scope limitation in the Discord reply so the owner can decide how to proceed.

## Google Sheets

Larry and team members can create and manage spreadsheets using `tools/gsheets.py`.

### Common Commands

```bash
# One-time auth
bin/pka gsheets auth

# Auth health
bin/pka gsheets doctor

# Create a spreadsheet
bin/pka gsheets create --title "Budget 2026" --file data.csv

# Read data
bin/pka gsheets read SPREADSHEET_ID --range "Sheet1!A1:D10"

# Write data
bin/pka gsheets write SPREADSHEET_ID --range "Sheet1!A1" --data '[["Name","Amount"],["Rent","2000"]]'

# Append rows
bin/pka gsheets append SPREADSHEET_ID --row "John,500,2026-03-26"

# List spreadsheets
bin/pka gsheets list

# Search spreadsheets
bin/pka gsheets search "budget"
```

## Google Maps

Larry can look up locations, directions, and nearby places using `tools/gmaps.py`.

### Common Commands

```bash
# Search for places
bin/pka gmaps search "coffee shops in Palo Alto"

# Get directions
bin/pka gmaps directions "San Francisco" "Palo Alto" --mode driving

# Nearby places
bin/pka gmaps nearby --location "37.4419,-122.1430" --type restaurant --radius 1000

# Distance and travel time
bin/pka gmaps distance "San Francisco" "Los Angeles"

# Geocode
bin/pka gmaps geocode "1600 Amphitheatre Parkway, Mountain View"
```

Maps uses an API key rather than OAuth. Save the key to `data/gmaps/api_key.txt`.
