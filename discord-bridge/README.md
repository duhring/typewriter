# PKA Discord Bridge

A Discord bot that bridges messages from a `#larry` channel into the PKA team-inbox so Larry and the team can process them.

Text messages are saved as timestamped Markdown files with YAML frontmatter. Attachments (images, PDFs, etc.) are downloaded alongside them.

---

## Setup

### 1. Create (or choose) a Discord server

If you don't have one yet, open Discord and click the **+** button on the left sidebar to create a new server. A personal/private server works fine.

### 2. Create a Discord Application and Bot

1. Go to <https://discord.com/developers/applications>.
2. Click **New Application**. Give it a name (e.g. "PKA Bridge") and click **Create**.
3. In the left sidebar click **Bot**.
4. Click **Reset Token** (or **Copy** if this is a fresh bot) to get your bot token. Save it — you will only see it once.

### 3. Enable Message Content Intent

Still on the **Bot** settings page:

1. Scroll down to **Privileged Gateway Intents**.
2. Toggle **Message Content Intent** to ON.
3. Click **Save Changes**.

This is required for the bot to read message text.

### 4. Configure the .env file

```bash
cd <pka-root>/discord-bridge
cp .env.example .env
```

Open `.env` and paste your bot token:

```
DISCORD_BOT_TOKEN=paste-your-token-here
```

Optional:

```
# Only set this if you explicitly want the bot to bypass Claude permission prompts
CLAUDE_DANGEROUS_SKIP_PERMISSIONS=1
```

### 5. Create a #larry channel and get its ID

1. In your Discord server, create a text channel called `#larry` (or whatever you prefer).
2. Enable **Developer Mode** in Discord: User Settings > App Settings > Advanced > Developer Mode.
3. Right-click the channel name and select **Copy Channel ID**.
4. Paste it into your `.env` file:

```
LARRY_CHANNEL_ID=123456789012345678
```

### 6. Invite the bot to your server

1. Back in the Developer Portal, go to your application > **OAuth2** > **URL Generator**.
2. Under **Scopes**, check `bot`.
3. Under **Bot Permissions**, check:
   - Read Messages/View Channels
   - Send Messages
   - Read Message History
   - Attach Files
4. Copy the generated URL at the bottom and open it in your browser.
5. Select your server and click **Authorize**.

### 7. Install Python dependencies

```bash
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Python 3.11+ is recommended for the bridge and Google tools.

### 8. Run the bot

```bash
python3 bot.py
```

You should see log output confirming the bot connected and is monitoring your channel. Send a test message in `#larry` — a confirmation reply and a new file in `team-inbox/discord/` means everything is working.

By default the bridge keeps Claude permission checks enabled. That is safer for a long-running bot, but requests that need local approvals may need to be run from the terminal instead of Discord.

On reconnect, the bot also backfills recent missed channel history so a short disconnect does not silently drop messages. Messages with attachments get an immediate receipt reply before Larry starts processing them.

### 9. (Optional) Run as a background service with launchd

To have the bot start automatically when you log in:

```bash
cp com.pka.discord-bot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.pka.discord-bot.plist
```

Check that it is running:

```bash
launchctl list | grep pka
```

View logs:

```bash
tail -f /tmp/pka-discord-bot.log
tail -f /tmp/pka-discord-bot.err
```

To stop the service:

```bash
launchctl unload ~/Library/LaunchAgents/com.pka.discord-bot.plist
```

> **Note:** If you installed Python packages in a virtual environment or via Homebrew, you may need to update the `ProgramArguments` path in the plist to point to the correct `python3` binary (e.g. `/opt/homebrew/bin/python3` or `/path/to/venv/bin/python3`).

---

## How it works

| What you send in #larry | What gets saved |
|---|---|
| A text message | `YYYY-MM-DD_HHMMSS_note.md` with YAML frontmatter (timestamp, author, source) |
| A file attachment | `YYYY-MM-DD_HHMMSS_originalfilename.ext` downloaded to disk |
| Both at once | One `.md` note + each attachment as a separate file |

All files land in `PKA/team-inbox/discord/`. Larry checks this folder at the start of each session.
