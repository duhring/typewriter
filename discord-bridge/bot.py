"""
PKA Discord Bridge Bot — Two-Way Larry Chat
=============================================
Monitors a Discord channel, sends messages to Larry (via Claude Code CLI),
and replies with Larry's response. Also saves all messages to team-inbox
for the record.
"""

import asyncio
import json
import os
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

import discord
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("LARRY_CHANNEL_ID")
CLAUDE_PATH = os.getenv("CLAUDE_PATH", "/opt/homebrew/bin/claude")
ALLOW_DANGEROUS_SKIP_PERMISSIONS = os.getenv(
    "CLAUDE_DANGEROUS_SKIP_PERMISSIONS", ""
).strip().lower() in {"1", "true", "yes", "on"}
FORCE_CLAUDE_OAUTH_TOKEN = os.getenv(
    "CLAUDE_FORCE_OAUTH_TOKEN", ""
).strip().lower() in {"1", "true", "yes", "on"}

if not BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is not set. Check your .env file.")
if not CHANNEL_ID:
    raise RuntimeError("LARRY_CHANNEL_ID is not set. Check your .env file.")

CHANNEL_ID = int(CHANNEL_ID)

# PKA root folder (parent of discord-bridge/)
PKA_ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = PKA_ROOT / "team-inbox" / "discord"
INBOX_DIR.mkdir(parents=True, exist_ok=True)

# Timeout for Claude CLI — 10 minutes to accommodate full pipeline runs
CLAUDE_TIMEOUT = 600

# Session persistence for conversation memory
SESSION_FILE = PKA_ROOT / "data" / "discord-session.json"
SESSION_MAX_AGE_HOURS = 12
CLAUDE_MD_FILE = PKA_ROOT / "CLAUDE.md"
MESSAGE_STATE_FILE = PKA_ROOT / "data" / "discord-message-state.json"
SESSION_LOG_DIR = PKA_ROOT / "owners-inbox" / "session-logs"
INITIAL_HISTORY_LOOKBACK_MINUTES = 60
HISTORY_BACKFILL_LIMIT = 50
MEMORY_RETRIEVAL_TOOL = PKA_ROOT / "tools" / "memory_retrieval.py"
MEMORY_RETRIEVAL_TIMEOUT = int(os.getenv("PKA_MEMORY_RETRIEVAL_TIMEOUT", "20"))
MEMORY_RETRIEVAL_LIMIT = int(os.getenv("PKA_MEMORY_RETRIEVAL_LIMIT", "4"))

# grok-brief command — Discord-triggered Grok briefing of a YouTube video.
GROK_BRIEF_PREFIX = "grok-brief"
BRIEFS_DIR = PKA_ROOT / "owners-inbox" / "briefs"
TRANSCRIPT_TOOL = PKA_ROOT / "tools" / "fetch-transcript.py"
INDEX_TOOL = PKA_ROOT / "tools" / "pka_index.py"
TRANSCRIPT_FETCH_TIMEOUT = int(os.getenv("PKA_TRANSCRIPT_FETCH_TIMEOUT", "120"))
GROK_BRIEF_TIMEOUT = int(os.getenv("PKA_GROK_BRIEF_TIMEOUT", "180"))
sys.path.insert(0, str(PKA_ROOT / "tools"))

from conversation_memory import ConversationMemory
MEMORY = ConversationMemory(PKA_ROOT / "data" / "conversation-history.json")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pka-discord")

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

# Queue is created in on_ready() so it's on the correct event loop
message_queue: asyncio.Queue = None
queued_message_ids: set[int] = set()
last_seen_message_id: Optional[int] = None
# Ring buffer of message IDs we've finished processing. Persisted across restarts
# so a crash mid-process (or a second bot instance racing the first) cannot cause
# a duplicate ack + dispatch. Sized larger than any reasonable in-flight queue.
PROCESSED_RING_MAX = 500
processed_message_ids: list[int] = []  # ordered oldest -> newest
processed_message_ids_set: set[int] = set()  # O(1) lookup mirror
TOPIC_SWITCH_MARKERS = (
    "new topic",
    "switching gears",
    "different question",
    "separately",
    "another thing",
)
STRONG_TASK_KINDS = {
    "brief",
    "calendar",
    "cuecam",
    "first_principles",
    "google_docs",
    "google_sheets",
    "journal",
    "maps",
    "research",
    "video_watch",
    "viewer",
    "youtube_blog",
    "youtube_package",
}
WRITING_TASK_KINDS = {"youtube_blog", "youtube_package", "research"}
CLOSE_CHAT_COMMANDS = {"close chat", "close session", "session close", "!close"}

# A "watch this" request is a *shape*: a video link (or attached video clip) plus
# an explicit ask to see what is on screen. Bare URLs and "write the description"
# stay with Maven (youtube_package) — only an explicit visual-analysis intent
# routes to video_watch. See docs/larry/video-watching.md.
WATCH_INTENT_MARKERS = (
    "watch this video",
    "watch the video",
    "watch this",
    "watch it",
    "what's on screen",
    "whats on screen",
    "what's on-screen",
    "on-screen",
    "on screen",
    "what's shown",
    "what is shown",
    "what's displayed",
    "look at this video",
    "look at the video",
    "analyze the video",
    "analyze this video",
    "visual analysis",
    "frame by frame",
    "frame-by-frame",
    "what does it show",
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _slugify(text: str, fallback: str = "session") -> str:
    cleaned = re.sub(r"[^\w\s-]", "", (text or "").lower())
    cleaned = re.sub(r"[\s_]+", "-", cleaned).strip("-")
    return cleaned[:60] or fallback


def _rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PKA_ROOT))
    except ValueError:
        return str(path.resolve())


def _resolve_saved_path(path_str: str | None) -> Optional[Path]:
    if not path_str:
        return None
    path = Path(path_str)
    if path.is_absolute():
        return path
    return PKA_ROOT / path


def _append_unique(existing: list[str], new_values: list[str]) -> list[str]:
    merged = list(existing)
    seen = set(existing)
    for value in new_values:
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def _parse_frontmatter_body(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.splitlines()
    metadata: dict[str, str] = {}
    end_index = None
    for index, line in enumerate(parts[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip('"')
    if end_index is None:
        return {}, text.strip()
    return metadata, "\n".join(parts[end_index + 1:]).strip()


def _index_saved_artifact(path: Path) -> None:
    try:
        tools_dir = PKA_ROOT / "tools"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        from pka_index import index_discord_artifact

        index_discord_artifact(path)
    except Exception as exc:
        log.warning("Could not index saved artifact %s: %s", path, exc)


def _index_session_report(path: Path) -> None:
    try:
        tools_dir = PKA_ROOT / "tools"
        if str(tools_dir) not in sys.path:
            sys.path.insert(0, str(tools_dir))
        from pka_index import upsert_file_record

        upsert_file_record(
            path,
            category="session-log",
            description="Larry session close report",
            file_type=".md",
        )
    except Exception as exc:
        log.warning("Could not index session report %s: %s", path, exc)


def _move_to_processed(paths: list[Optional[Path]]) -> None:
    """Move handled inbox files into team-inbox/discord/processed/ and keep
    files.filepath / knowledge_base.source_file pointing at the new location."""
    import sqlite3

    processed_dir = INBOX_DIR / "processed"
    processed_dir.mkdir(exist_ok=True)
    db_path = PKA_ROOT / "data" / "pka.db"
    moved = []
    for path in paths:
        if not path:
            continue
        src = Path(path)
        if not src.exists() or src.parent != INBOX_DIR:
            continue
        dest = processed_dir / src.name
        try:
            src.rename(dest)
            moved.append((_rel_path(src), _rel_path(dest)))
        except Exception as exc:
            log.warning("Could not move %s to processed/: %s", src, exc)
    if not moved:
        return
    try:
        conn = sqlite3.connect(db_path)
        for old_rel, new_rel in moved:
            conn.execute(
                "update files set filepath = ? where filepath = ?", (new_rel, old_rel)
            )
            conn.execute(
                "update knowledge_base set source_file = ? where source_file = ?",
                (new_rel, old_rel),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("Moved files but could not update DB provenance: %s", exc)


def _save_larry_reply(
    response_text: str,
    *,
    session_id: Optional[str],
    task_kind: str,
    replying_to_message_id: int,
) -> Optional[Path]:
    if not response_text.strip():
        return None
    ts = _timestamp()
    path = INBOX_DIR / f"{ts}_reply.md"
    lines = [
        "---",
        f"timestamp: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}",
        f"replying_to_message_id: {replying_to_message_id}",
        f"task_kind: {task_kind}",
        f"session_id: {json.dumps(session_id or '')}",
        "author: Larry",
        "source: discord-larry",
        "---",
        "",
        response_text.strip(),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    _index_saved_artifact(path)
    return path


def _build_note(message: discord.Message) -> str:
    ts = message.created_at.strftime("%Y-%m-%dT%H:%M:%S")
    author = str(message.author)
    lines = [
        "---",
        f"timestamp: {ts}",
        f"message_id: {message.id}",
        f"channel_id: {message.channel.id}",
        f"author: {author}",
        "source: discord",
        "---",
        "",
        message.content,
        "",
    ]
    return "\n".join(lines)


def _load_message_state():
    """Load the most recent Discord message state from disk."""
    global last_seen_message_id, processed_message_ids, processed_message_ids_set
    try:
        if not MESSAGE_STATE_FILE.exists():
            return
        data = json.loads(MESSAGE_STATE_FILE.read_text())
        raw_message_id = data.get("last_seen_message_id")
        if raw_message_id is not None:
            last_seen_message_id = int(raw_message_id)
        raw_processed = data.get("processed_message_ids") or []
        processed_message_ids = [int(x) for x in raw_processed][-PROCESSED_RING_MAX:]
        processed_message_ids_set = set(processed_message_ids)
    except Exception as exc:
        log.warning("Could not load Discord message state: %s", exc)


def _save_message_state():
    """Persist the most recent Discord message state to disk."""
    try:
        payload = {
            "last_seen_message_id": last_seen_message_id,
            "processed_message_ids": processed_message_ids[-PROCESSED_RING_MAX:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        MESSAGE_STATE_FILE.write_text(json.dumps(payload))
    except Exception as exc:
        log.warning("Could not save Discord message state: %s", exc)


def _mark_message_seen(message_id: int):
    """Persist the highest Discord message ID we've observed."""
    global last_seen_message_id
    if last_seen_message_id is None or message_id > last_seen_message_id:
        last_seen_message_id = message_id
        _save_message_state()


def _mark_message_processed(message_id: int):
    """Record that we've finished processing this message. Persisted across restarts."""
    if message_id in processed_message_ids_set:
        return
    processed_message_ids.append(message_id)
    processed_message_ids_set.add(message_id)
    if len(processed_message_ids) > PROCESSED_RING_MAX:
        # Trim oldest
        drop_count = len(processed_message_ids) - PROCESSED_RING_MAX
        for old_id in processed_message_ids[:drop_count]:
            processed_message_ids_set.discard(old_id)
        del processed_message_ids[:drop_count]
    _save_message_state()


def _load_recent_note_texts(limit: int = 200) -> list[str]:
    """Load recent Discord note bodies for initial backfill dedupe."""
    notes = []
    note_files = sorted(
        INBOX_DIR.glob("*_note.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:limit]
    for path in note_files:
        try:
            notes.append(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return notes


def _message_already_archived(message: discord.Message, note_texts: list[str]) -> bool:
    """Best-effort check to avoid replaying already archived messages."""
    ts = message.created_at.strftime("%Y-%m-%dT%H:%M:%S")
    author = str(message.author)
    content = message.content.strip()

    for note in note_texts:
        if f"message_id: {message.id}" in note:
            return True
        if (
            f"timestamp: {ts}" in note
            and f"author: {author}" in note
            and content
            and content in note
        ):
            return True
    return False


def _message_preview(message: discord.Message) -> str:
    """Return a short preview for logs."""
    if message.content.strip():
        return message.content[:80]
    if message.attachments:
        count = len(message.attachments)
        noun = "attachment" if count == 1 else "attachments"
        return f"({count} {noun})"
    return "(empty message)"


def _read_session_data() -> Optional[dict]:
    try:
        if not SESSION_FILE.exists():
            return None
        return json.loads(SESSION_FILE.read_text())
    except Exception as exc:
        log.warning("Could not read session file: %s", exc)
        return None


def _build_session_turn(
    *,
    message_id: int,
    note_path: Optional[str],
    attachment_paths: list[str],
    reply_path: Optional[str],
    prompt_preview: str,
) -> dict:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "message_id": message_id,
        "note_path": note_path,
        "attachment_paths": attachment_paths,
        "reply_path": reply_path,
        "prompt_preview": prompt_preview[:160],
    }


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def _has_youtube_url(text: str) -> bool:
    lowered = text.lower()
    return "youtube.com/" in lowered or "youtu.be/" in lowered


def _has_video_url(text: str) -> bool:
    """Video sources watch_video_capture.py can fetch via yt-dlp."""
    lowered = text.lower()
    return (
        _has_youtube_url(lowered)
        or "loom.com/" in lowered
        or "zoom.us/" in lowered
        or "vimeo.com/" in lowered
    )


def _infer_task_kind(message_text: str, attachment_paths: list[str] | None = None) -> str:
    """Return a coarse task family so unrelated work can start fresh sessions."""
    lowered = message_text.strip().lower()
    attachments = attachment_paths or []
    has_visual_media = any(
        path.lower().endswith((
            ".jpg", ".jpeg", ".png", ".heic", ".gif", ".webp", ".mov", ".mp4", ".m4v"
        ))
        for path in attachments
    )

    if _contains_any(lowered, ("first principles", "break this down", "challenge my assumptions")):
        return "first_principles"
    if lowered.startswith("!brief") or lowered in {"brief", "morning brief"} or "morning brief" in lowered:
        return "brief"
    if (
        "cuecam" in lowered
        or "presentation" in lowered
        or "slides" in lowered
        or "hyperframes" in lowered
        or "hyperframe" in lowered
        or "motion graphics" in lowered
        or "lower-third" in lowered
        or "lower third" in lowered
        or len(re.findall(r'(?m)^\s*\d+\.\s+.*"', message_text)) >= 2
        or (has_visual_media and _contains_any(lowered, ("card 1", "card 2", "\"", "build it", "teleprompter")))
    ):
        return "cuecam"
    has_video_media = any(
        path.lower().endswith((".mov", ".mp4", ".m4v", ".webm", ".mkv"))
        for path in attachments
    )
    if (_has_video_url(lowered) or has_video_media) and _contains_any(lowered, WATCH_INTENT_MARKERS):
        return "video_watch"
    if _has_youtube_url(lowered):
        if _contains_any(lowered, ("blog post", "substack", "write a post", "write an article")):
            return "youtube_blog"
        return "youtube_package"
    if _contains_any(lowered, ("description with timestamps", "thumbnail prompt", "title options", "headline options")):
        return "youtube_package"
    if _contains_any(lowered, ("blog post", "substack", "write a post", "write an article")):
        return "youtube_blog"
    if _contains_any(lowered, ("journal this", "add to journal", "journal entry")):
        return "journal"
    if _contains_any(lowered, ("calendar", "on my calendar", "schedule this", "add this to the calendar", "reschedule", "free time")):
        return "calendar"
    if _contains_any(lowered, ("google doc", "google docs", "save to drive", "put it in my drive", "google drive")):
        return "google_docs"
    if _contains_any(lowered, ("spreadsheet", "google sheet", "google sheets", "sheet1", "append row")):
        return "google_sheets"
    if _contains_any(lowered, ("directions", "nearby", "geocode", "distance to", "map to", "google maps")):
        return "maps"
    if _contains_any(lowered, ("viewer.html", "dashboard", "viewer", "visualize the database")):
        return "viewer"
    if _contains_any(lowered, ("research", "look into", "look up", "find out", "investigate")):
        return "research"
    return "general"


def _should_reset_session(
    *,
    previous_kind: str | None,
    current_kind: str,
    message_text: str,
) -> tuple[bool, str | None]:
    """Return whether a new session should start for this message."""
    if not previous_kind:
        return False, None
    if _contains_any(message_text, TOPIC_SWITCH_MARKERS):
        return True, "owner signaled a topic switch"
    if current_kind == previous_kind:
        return False, None
    if previous_kind in STRONG_TASK_KINDS and current_kind in STRONG_TASK_KINDS:
        return True, f"task family changed from {previous_kind} to {current_kind}"
    return False, None


async def _queue_message(message: discord.Message, source: str, acknowledge: bool = False) -> bool:
    """Queue a Discord message once, skipping duplicates we've already seen."""
    if message.author == client.user:
        return False
    if message.channel.id != CHANNEL_ID:
        return False
    if message.id in queued_message_ids:
        log.info("Skipping duplicate %s message %s (in-flight)", source, message.id)
        return False
    if message.id in processed_message_ids_set:
        log.info("Skipping duplicate %s message %s (already processed)", source, message.id)
        return False
    if last_seen_message_id is not None and source != "history" and message.id <= last_seen_message_id:
        log.info("Skipping already seen live message %s", message.id)
        return False

    queued_message_ids.add(message.id)
    _mark_message_seen(message.id)
    await message_queue.put(message)
    log.info("Queued %s message from %s: %s", source, message.author, _message_preview(message))

    if acknowledge and message.attachments:
        count = len(message.attachments)
        noun = "attachment" if count == 1 else "attachments"
        try:
            await message.reply(
                f"Received {count} {noun}. Processing now.",
                mention_author=False,
            )
        except Exception as exc:
            log.warning("Could not send attachment receipt: %s", exc)

    return True


async def _resolve_channel():
    """Return the configured Discord channel, fetching it if needed."""
    channel = client.get_channel(CHANNEL_ID)
    if channel is not None:
        return channel
    try:
        return await client.fetch_channel(CHANNEL_ID)
    except Exception as exc:
        log.warning("Could not fetch channel %s: %s", CHANNEL_ID, exc)
        return None


async def _backfill_missed_messages(channel):
    """Recover recent messages that may have been missed during disconnects."""
    recent_messages = []
    highest_seen_id = last_seen_message_id

    if last_seen_message_id is not None:
        after = discord.Object(id=last_seen_message_id)
        async for message in channel.history(limit=HISTORY_BACKFILL_LIMIT, after=after, oldest_first=True):
            if message.author == client.user or message.channel.id != CHANNEL_ID:
                continue
            recent_messages.append(message)
            if highest_seen_id is None or message.id > highest_seen_id:
                highest_seen_id = message.id
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=INITIAL_HISTORY_LOOKBACK_MINUTES)
        note_texts = _load_recent_note_texts()
        async for message in channel.history(limit=HISTORY_BACKFILL_LIMIT, after=cutoff, oldest_first=True):
            if message.author == client.user or message.channel.id != CHANNEL_ID:
                continue
            if highest_seen_id is None or message.id > highest_seen_id:
                highest_seen_id = message.id
            if _message_already_archived(message, note_texts):
                continue
            recent_messages.append(message)

    if recent_messages:
        log.info("Backfilling %d missed Discord message(s)", len(recent_messages))
        for message in recent_messages:
            await _queue_message(message, source="history", acknowledge=False)
    elif last_seen_message_id is None:
        log.info("No recent missed Discord messages found during initial backfill")

    if highest_seen_id is not None:
        _mark_message_seen(highest_seen_id)


async def _discord_send_with_retry(coro_fn, max_retries: int = 5):
    """
    Call an async coroutine factory that sends a Discord message, retrying on 429 rate limits.
    coro_fn is a zero-argument async callable that performs the send.
    Handles both global rate limits and slowmode (error code 40062).
    Uses exponential backoff: waits max(retry_after, 2^attempt) seconds, capped at 60s.
    """
    import asyncio
    for attempt in range(max_retries):
        try:
            return await coro_fn()
        except discord.HTTPException as exc:
            if exc.status == 429:
                # Obey Discord's retry_after; add exponential backoff on top
                retry_after = 5.0
                if hasattr(exc, "retry_after") and exc.retry_after:
                    retry_after = float(exc.retry_after)
                backoff = max(retry_after, 2 ** attempt)
                backoff = min(backoff, 60.0)  # cap at 60s
                log.warning(
                    "Discord 429 rate limit (code %s) — retrying in %.1fs (attempt %d/%d)",
                    exc.code, backoff, attempt + 1, max_retries,
                )
                await asyncio.sleep(backoff)
                continue
            raise
    # Final attempt — let it raise naturally if it still fails
    return await coro_fn()


async def _send_long_message(channel, text: str, reference=None):
    """Send a message to Discord, splitting if over 2000 chars."""
    if not text.strip():
        text = "(Larry had no response)"

    chunks = []
    while len(text) > 2000:
        # Try to split at a newline near the limit
        split_at = text.rfind("\n", 0, 2000)
        if split_at == -1:
            split_at = 2000
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    chunks.append(text)

    for i, chunk in enumerate(chunks):
        try:
            if i == 0 and reference:
                await _discord_send_with_retry(
                    lambda c=chunk: reference.reply(c, mention_author=False)
                )
            else:
                await _discord_send_with_retry(
                    lambda c=chunk: channel.send(c)
                )
        except Exception as exc:
            log.error("Failed to send message chunk: %s", exc)


async def _get_relevant_memory(query: str, task_kind: str) -> Optional[str]:
    """Fetch a short block of relevant prior context from local memory."""
    cleaned = query.strip()
    if not cleaned or cleaned.startswith("!"):
        return None

    intent = "writing" if task_kind in WRITING_TASK_KINDS else "general"
    limit = 6 if intent == "writing" else MEMORY_RETRIEVAL_LIMIT

    cmd = [
        sys.executable,
        str(MEMORY_RETRIEVAL_TOOL),
        "recall",
        "--query",
        cleaned,
        "--limit",
        str(limit),
        "--intent",
        intent,
        "--format",
        "prompt",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PKA_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=MEMORY_RETRIEVAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("Local memory retrieval timed out after %d seconds", MEMORY_RETRIEVAL_TIMEOUT)
        try:
            proc.kill()
        except Exception:
            pass
        return None
    except Exception as exc:
        log.warning("Local memory retrieval failed to start: %s", exc)
        return None

    if proc.returncode != 0:
        error_text = stderr.decode("utf-8", errors="replace").strip()
        log.warning("Local memory retrieval failed: %s", error_text[:400])
        return None

    block = stdout.decode("utf-8", errors="replace").strip()
    if not block:
        return None

    line_count = sum(1 for line in block.splitlines() if line.strip().startswith(tuple(str(i) for i in range(1, 10))))
    log.info("Injected %d retrieved memory hit(s) into prompt", line_count)
    return block


def _load_session(current_task_kind: str, message_text: str) -> Optional[str]:
    """Load the current session ID, or None if expired/missing."""
    try:
        if not SESSION_FILE.exists():
            return None
        data = json.loads(SESSION_FILE.read_text())
        current_claude_mtime = (
            CLAUDE_MD_FILE.stat().st_mtime if CLAUDE_MD_FILE.exists() else None
        )
        saved_claude_mtime = data.get("claude_md_mtime")
        if current_claude_mtime != saved_claude_mtime:
            log.info("Session invalidated because CLAUDE.md changed")
            return None
        last_used = datetime.fromisoformat(data["last_used"])
        age_hours = (datetime.now() - last_used).total_seconds() / 3600
        if age_hours > SESSION_MAX_AGE_HOURS:
            log.info("Session expired (%.1f hours old), starting fresh", age_hours)
            return None
        previous_kind = data.get("task_kind")
        should_reset, reason = _should_reset_session(
            previous_kind=previous_kind,
            current_kind=current_task_kind,
            message_text=message_text.lower(),
        )
        if should_reset:
            log.info(
                "Session reset because %s (previous=%s, current=%s)",
                reason,
                previous_kind or "unknown",
                current_task_kind,
            )
            return None
        log.info("Resuming session %s (%.1f hours old)", data["session_id"], age_hours)
        return data["session_id"]
    except Exception as exc:
        log.warning("Could not load session: %s", exc)
        return None


def _load_session_for_close() -> Optional[dict]:
    data = _read_session_data()
    if not data:
        return None
    try:
        current_claude_mtime = (
            CLAUDE_MD_FILE.stat().st_mtime if CLAUDE_MD_FILE.exists() else None
        )
        saved_claude_mtime = data.get("claude_md_mtime")
        if current_claude_mtime != saved_claude_mtime:
            log.info("Close-chat session invalidated because CLAUDE.md changed")
            return None
        last_used = datetime.fromisoformat(data["last_used"])
        age_hours = (datetime.now() - last_used).total_seconds() / 3600
        if age_hours > SESSION_MAX_AGE_HOURS:
            log.info("Close-chat session expired (%.1f hours old)", age_hours)
            return None
        return data
    except Exception as exc:
        log.warning("Could not load active session for close: %s", exc)
        return None


def _save_session(
    session_id: str,
    task_kind: str,
    message_text: str,
    *,
    turn: Optional[dict] = None,
):
    """Persist the current session ID with a timestamp."""
    try:
        existing = _read_session_data() or {}
        is_same_session = existing.get("session_id") == session_id
        note_paths = existing.get("note_paths", []) if is_same_session else []
        attachment_paths = existing.get("attachment_paths", []) if is_same_session else []
        reply_paths = existing.get("reply_paths", []) if is_same_session else []
        turns = existing.get("turns", []) if is_same_session else []

        if turn:
            note_value = turn.get("note_path")
            attachment_values = turn.get("attachment_paths", [])
            reply_value = turn.get("reply_path")
            note_paths = _append_unique(note_paths, [note_value] if note_value else [])
            attachment_paths = _append_unique(attachment_paths, attachment_values)
            reply_paths = _append_unique(reply_paths, [reply_value] if reply_value else [])
            turns.append(turn)

        payload = {
            "session_id": session_id,
            "started_at": existing.get("started_at") if is_same_session and existing.get("started_at") else datetime.now().isoformat(),
            "last_used": datetime.now().isoformat(),
            "claude_md_mtime": CLAUDE_MD_FILE.stat().st_mtime if CLAUDE_MD_FILE.exists() else None,
            "task_kind": task_kind,
            "last_prompt_preview": message_text[:160],
            "note_paths": note_paths,
            "attachment_paths": attachment_paths,
            "reply_paths": reply_paths,
            "turns": turns,
        }
        SESSION_FILE.write_text(json.dumps(payload))
    except Exception as exc:
        log.warning("Could not save session: %s", exc)


def _clear_session() -> None:
    try:
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
    except Exception as exc:
        log.warning("Could not clear session: %s", exc)


def _build_close_chat_prompt(session_data: dict) -> str:
    task_kind = session_data.get("task_kind", "general")
    preview = session_data.get("last_prompt_preview", "").strip()
    context_bits = [f"Task family: {task_kind}"]
    if preview:
        context_bits.append(f"Last prompt preview: {preview}")
    context = "\n".join(f"- {bit}" for bit in context_bits)
    return (
        "We are closing this Discord chat session.\n\n"
        "Create a concise markdown session close report with exactly these sections:\n"
        "# Session Close Report\n"
        "## Summary\n"
        "## Deliverables Saved\n"
        "## Durable Learnings\n"
        "## Open Loops\n"
        "## Dreamer Quick Pass\n\n"
        "Rules:\n"
        "- Be factual and concise.\n"
        "- Mention concrete saved paths or filenames when you know them.\n"
        "- In Open Loops, say `None.` if there are no obvious follow-ups.\n"
        "- In Dreamer Quick Pass, say either `Recommended` or `Not needed`, with one short reason.\n"
        "- Do not ask follow-up questions.\n\n"
        "Session metadata:\n"
        f"{context}"
    )


def _build_fallback_close_report(session_data: dict) -> str:
    task_kind = session_data.get("task_kind", "general")
    preview = session_data.get("last_prompt_preview", "Unavailable")
    return (
        "# Session Close Report\n\n"
        "## Summary\n"
        "Session closed, but Larry could not generate a full closeout summary automatically.\n\n"
        "## Deliverables Saved\n"
        "Review recent files in owners-inbox/ and team-inbox/discord/.\n\n"
        "## Durable Learnings\n"
        f"Task family: {task_kind}\n\n"
        "## Open Loops\n"
        f"Last prompt preview: {preview}\n\n"
        "## Dreamer Quick Pass\n"
        "Not needed. This fallback closeout did not extract enough detail to justify it."
    )


def _save_session_report(report_markdown: str, session_data: dict) -> Path:
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    closed_at = datetime.now()
    task_kind = session_data.get("task_kind", "general")
    preview = session_data.get("last_prompt_preview", "")
    slug = _slugify(preview, fallback=task_kind)
    filename = f"{closed_at.strftime('%Y-%m-%d_%H%M%S')}-{task_kind}-{slug}.md"
    path = SESSION_LOG_DIR / filename
    frontmatter = [
        "---",
        f"closed_at: {closed_at.isoformat(timespec='seconds')}",
        f"task_kind: {task_kind}",
        f"session_id: {session_data.get('session_id', '')}",
        f"last_prompt_preview: {json.dumps(preview)}",
        "source: discord-close-chat",
        "---",
        "",
    ]
    path.write_text("\n".join(frontmatter) + report_markdown.strip() + "\n", encoding="utf-8")
    _index_session_report(path)
    return path


def _save_session_archive(session_data: dict) -> Optional[Path]:
    turns = session_data.get("turns") or []
    if not turns:
        return None

    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    closed_at = datetime.now()
    task_kind = session_data.get("task_kind", "general")
    preview = session_data.get("last_prompt_preview", "")
    slug = _slugify(preview, fallback=task_kind)
    filename = f"{closed_at.strftime('%Y-%m-%d_%H%M%S')}-{task_kind}-{slug}-archive.md"
    path = SESSION_LOG_DIR / filename

    attachment_count = sum(len(turn.get("attachment_paths", [])) for turn in turns)
    lines = [
        "---",
        f"closed_at: {closed_at.isoformat(timespec='seconds')}",
        f"started_at: {session_data.get('started_at', '')}",
        f"task_kind: {task_kind}",
        f"session_id: {session_data.get('session_id', '')}",
        f"turn_count: {len(turns)}",
        f"attachment_count: {attachment_count}",
        "source: discord-session-archive",
        "---",
        "",
        "# Larry Session Archive",
        "",
        "## Session Overview",
        "",
        f"- Task family: {task_kind}",
        f"- Turns: {len(turns)}",
        f"- Attachments: {attachment_count}",
        "",
        "## Transcript",
        "",
    ]

    for index, turn in enumerate(turns, start=1):
        lines.append(f"### Turn {index}")
        lines.append("")
        note_path = _resolve_saved_path(turn.get("note_path"))
        if note_path and note_path.exists():
            note_meta, note_body = _parse_frontmatter_body(note_path.read_text(encoding="utf-8"))
            author = note_meta.get("author", "Owner")
            lines.append(f"**{author}:**")
            lines.append("")
            lines.append(note_body or "(No text captured.)")
            lines.append("")
            lines.append(f"_Source note: {_rel_path(note_path)}_")
            lines.append("")
        else:
            preview = turn.get("prompt_preview") or "(No user note captured.)"
            lines.append(f"**Owner:** {preview}")
            lines.append("")

        attachments = turn.get("attachment_paths", [])
        if attachments:
            lines.append("**Attachments:**")
            lines.append("")
            for attachment in attachments:
                attachment_path = _resolve_saved_path(attachment)
                lines.append(f"- {_rel_path(attachment_path) if attachment_path else attachment}")
            lines.append("")

        reply_path = _resolve_saved_path(turn.get("reply_path"))
        if reply_path and reply_path.exists():
            reply_meta, reply_body = _parse_frontmatter_body(reply_path.read_text(encoding="utf-8"))
            author = reply_meta.get("author", "Larry")
            lines.append(f"**{author}:**")
            lines.append("")
            lines.append(reply_body or "(No reply text captured.)")
            lines.append("")
            lines.append(f"_Source reply: {_rel_path(reply_path)}_")
            lines.append("")

    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    _index_session_report(path)
    return path


async def _ask_larry(prompt: str, session_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Run claude CLI with the given prompt and return (response_text, session_id)."""
    cmd = [
        CLAUDE_PATH,
        "-p",
        "--output-format", "json",
    ]
    if ALLOW_DANGEROUS_SKIP_PERMISSIONS:
        cmd.append("--dangerously-skip-permissions")
    if session_id:
        cmd.extend(["--resume", session_id])
    cmd.extend(["--", prompt])

    log.info("Sending to Larry (session=%s): %s",
             session_id or "new", prompt[:100] + ("..." if len(prompt) > 100 else ""))

    # Build a clean environment with Claude auth
    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"),
        "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TERM": os.environ.get("TERM", "xterm-256color"),
    }
    # Prefer Claude's local login/keychain. A stale long-lived token can override
    # a healthy local login and produce a misleading "organization has no access"
    # error after billing/account changes.
    claude_token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if claude_token and FORCE_CLAUDE_OAUTH_TOKEN:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = claude_token
        log.info("Claude OAuth token: forced from .env (%d chars)", len(claude_token))
    elif claude_token:
        log.info("Claude OAuth token: present but not injected; using local Claude auth")
    else:
        log.info("No CLAUDE_CODE_OAUTH_TOKEN found; using local Claude auth")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PKA_ROOT),
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=CLAUDE_TIMEOUT,
        )

        raw = stdout.decode("utf-8", errors="replace").strip()
        parsed_json = None
        if raw:
            try:
                parsed_json = json.loads(raw)
            except json.JSONDecodeError:
                parsed_json = None

        if proc.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip()
            json_error = None
            if isinstance(parsed_json, dict):
                json_error = (
                    parsed_json.get("result")
                    or parsed_json.get("error")
                    or parsed_json.get("message")
                )
            stdout_tail = raw[-500:] if raw else ""
            log.error(
                "Claude CLI error (code %d). stderr=%r json_error=%r stdout_tail=%r",
                proc.returncode, error_msg, json_error, stdout_tail,
            )
            # If we were resuming, retry without --resume (session may be stale)
            if session_id:
                log.info("Retrying without --resume (session may have expired)")
                return await _ask_larry(prompt, session_id=None)
            lowered = error_msg.lower()
            if (not ALLOW_DANGEROUS_SKIP_PERMISSIONS) and (
                "permission" in lowered or "approval" in lowered or "allow" in lowered
            ):
                return (
                    "⚠️ Larry needs permission for that request. Use the terminal for tasks that need local approvals, "
                    "or explicitly opt in with CLAUDE_DANGEROUS_SKIP_PERMISSIONS=1 for this bot.",
                    None,
                )
            # Build a meaningful error body even when stderr is empty (the original
            # symptom was a bare "⚠️ Larry encountered an error:" with nothing
            # after the colon). Fall back to stdout tail, then to the exit code.
            detail = (
                error_msg
                or json_error
                or stdout_tail
                or f"(no output; exit code {proc.returncode})"
            )
            return (f"⚠️ Larry encountered an error (exit {proc.returncode}): {detail[:500]}", None)

        # Parse JSON response to extract result text and session_id
        try:
            data = parsed_json if isinstance(parsed_json, dict) else json.loads(raw)
            response_text = data.get("result", raw)
            new_session_id = data.get("session_id")
            if data.get("is_error"):
                return (f"⚠️ Larry encountered an error: {response_text}", new_session_id)
            log.info("Larry responded (%d chars, session=%s)",
                     len(response_text), new_session_id)
            return (response_text, new_session_id)
        except json.JSONDecodeError:
            # Fallback: treat raw output as plain text
            log.warning("Could not parse JSON from Claude CLI, using raw output")
            return (raw, None)

    except asyncio.TimeoutError:
        log.error("Claude CLI timed out after %d seconds", CLAUDE_TIMEOUT)
        try:
            proc.kill()
        except Exception:
            pass
        return ("⚠️ Larry is taking too long to respond. Try a simpler request, or use the terminal for complex tasks.", None)

    except FileNotFoundError:
        log.error("Claude CLI not found at: %s", CLAUDE_PATH)
        return (f"⚠️ Claude CLI not found at `{CLAUDE_PATH}`. Check CLAUDE_PATH in .env.", None)

    except Exception as exc:
        log.error("Unexpected error calling Claude: %r", exc)
        # str(exc) is empty for some exception classes; repr() always has detail.
        body = str(exc) or repr(exc) or exc.__class__.__name__
        return (f"⚠️ Error ({exc.__class__.__name__}): {body[:500]}", None)


_YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=[\w-]{11}|shorts/[\w-]{11}|embed/[\w-]{11})|youtu\.be/[\w-]{11})[^\s]*",
    re.IGNORECASE,
)


def _grok_brief_system_prompt() -> str:
    return (
        "You are producing a concise briefing on a YouTube video for the owner. "
        "Output sections in this exact order, using markdown headings:\n"
        "## TL;DR\n3 bullets, one sentence each.\n"
        "## Key points\n5–8 bullets with the substantive content.\n"
        "## Notable quotes\nUp to 3 short quotes; include the [timestamp] from the transcript when present.\n"
        "## Action items\nOnly if the video clearly implies decisions or follow-ups for the owner. Omit the section if none.\n"
        "Rules: no fluff, no closing pleasantries, no meta-commentary about the transcript itself. "
        "If the transcript is sparse or auto-captioned with errors, still produce best-effort sections."
    )


async def _run_subprocess(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run a subprocess with timeout, returning (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(PKA_ROOT),
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    return (
        proc.returncode,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _slugify_for_brief(text: str, fallback: str) -> str:
    lowered = (text or "").lower().strip()
    lowered = re.sub(r"[^\w\s-]", "", lowered)
    lowered = re.sub(r"[\s_]+", "-", lowered).strip("-")
    return lowered[:60] or fallback


def _extract_transcript_title(transcript_md: str) -> str:
    for line in transcript_md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "YouTube video"


async def _handle_grok_brief(message: discord.Message, content: str) -> None:
    """Handle a `grok-brief <url> [extra instructions]` Discord command."""
    after_prefix = content[len(GROK_BRIEF_PREFIX):].strip()
    url_match = _YOUTUBE_URL_RE.search(after_prefix)
    if not url_match:
        await message.reply(
            "⚠️ `grok-brief` needs a YouTube URL. Example: `grok-brief https://youtu.be/abc123 [optional angle]`",
            mention_author=False,
        )
        return

    url = url_match.group(0)
    prompt_extra = (after_prefix[: url_match.start()] + " " + after_prefix[url_match.end():]).strip()

    async with message.channel.typing():
        # 1. Fetch transcript (also indexes it as a side effect)
        try:
            rc, stdout, stderr = await _run_subprocess(
                [sys.executable, str(TRANSCRIPT_TOOL), url],
                timeout=TRANSCRIPT_FETCH_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await message.reply(
                f"⚠️ Transcript fetch timed out after {TRANSCRIPT_FETCH_TIMEOUT}s.",
                mention_author=False,
            )
            return
        except Exception as exc:
            log.error("grok-brief: transcript subprocess failed: %r", exc)
            await message.reply(f"⚠️ Could not start transcript fetch: {exc}", mention_author=False)
            return

        if rc != 0 or not stdout.strip():
            err = stderr.strip().splitlines()[-1] if stderr.strip() else "no stdout"
            await message.reply(f"⚠️ Transcript fetch failed: {err[:300]}", mention_author=False)
            return

        transcript_md = stdout
        transcript_path = None
        for line in stderr.splitlines():
            if line.startswith("Saved transcript:"):
                transcript_path = line.split(":", 1)[1].strip()
                break
        title = _extract_transcript_title(transcript_md)

        # 2. Call Grok in a thread (xai.chat_text uses blocking urllib)
        try:
            import xai  # type: ignore
        except ImportError as exc:
            await message.reply(f"⚠️ Grok helper not importable: {exc}", mention_author=False)
            return

        user_prompt = transcript_md
        if prompt_extra:
            user_prompt += f"\n\n---\nOwner's angle for this brief: {prompt_extra}"

        try:
            brief_text = await asyncio.wait_for(
                asyncio.to_thread(
                    xai.chat_text,
                    user_prompt,
                    system=_grok_brief_system_prompt(),
                    max_tokens=2048,
                    label="grok_brief",
                ),
                timeout=GROK_BRIEF_TIMEOUT,
            )
        except asyncio.TimeoutError:
            await message.reply(
                f"⚠️ Grok briefing timed out after {GROK_BRIEF_TIMEOUT}s.",
                mention_author=False,
            )
            return
        except Exception as exc:
            log.error("grok-brief: xai call failed: %r", exc)
            await message.reply(f"⚠️ Grok briefing failed: {exc}", mention_author=False)
            return

        if not brief_text.strip():
            await message.reply("⚠️ Grok returned an empty brief.", mention_author=False)
            return

        # 3. Save brief markdown with frontmatter
        BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
        slug = _slugify_for_brief(title, "youtube-brief")
        brief_path = BRIEFS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-{slug}.md"
        version = 2
        while brief_path.exists():
            brief_path = BRIEFS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-{slug}-v{version}.md"
            version += 1

        frontmatter_lines = [
            "---",
            f"title: {title}",
            f"source_url: {url}",
            "model: grok-code-fast-1",
            f"created_at: {datetime.now().isoformat()}",
        ]
        if transcript_path:
            frontmatter_lines.append(f"transcript_path: {transcript_path}")
        if prompt_extra:
            frontmatter_lines.append(f"prompt_extra: {prompt_extra}")
        frontmatter_lines.append("---")
        brief_md = "\n".join(frontmatter_lines) + f"\n\n# {title} — Grok brief\n\n{brief_text.strip()}\n"
        try:
            brief_path.write_text(brief_md, encoding="utf-8")
        except Exception as exc:
            log.error("grok-brief: could not save brief file: %r", exc)
            # Still post the brief inline so user gets value
            await _send_long_message(
                message.channel,
                f"⚠️ Could not save brief file ({exc}). Brief follows:\n\n{brief_text}",
                reference=message,
            )
            return

        # 4. Index the brief (best-effort)
        try:
            await _run_subprocess(
                [
                    sys.executable,
                    str(INDEX_TOOL),
                    "index-markdown",
                    "--file",
                    str(brief_path),
                    "--title",
                    title,
                    "--category",
                    "brief",
                    "--tags",
                    "grok-brief,youtube",
                    "--source-url",
                    url,
                ],
                timeout=30,
            )
        except Exception as exc:
            log.warning("grok-brief: indexing failed (%r); brief saved at %s", exc, brief_path)

        # 5. Reply in Discord
        rel_path = brief_path.relative_to(PKA_ROOT) if brief_path.is_relative_to(PKA_ROOT) else brief_path
        header = f"📺 **{title}** — Grok brief\nSaved: `{rel_path}`\n\n"
        full_reply = header + brief_text.strip()
        await _send_long_message(message.channel, full_reply, reference=message)


async def _process_message(message: discord.Message):
    """Process a single message: save it, send to Larry, reply with response."""

    # --- Handle grok-brief <url> command (cheap-lane briefing via xAI) -------
    content_stripped = message.content.strip()
    if content_stripped.lower().startswith(GROK_BRIEF_PREFIX + " "):
        try:
            await _handle_grok_brief(message, content_stripped)
        except Exception as exc:
            log.error("grok-brief handler crashed: %r", exc)
            try:
                await message.reply(f"⚠️ grok-brief crashed: {exc}", mention_author=False)
            except Exception:
                pass
        return

    # --- Handle !reset / "new topic" command (clears conversation memory) -----
    normalized_command = content_stripped.lower()
    if normalized_command in {"!reset", "new topic"}:
        try:
            _clear_session()
            MEMORY.clear(message.channel.id)
            await message.reply("🧹 Memory cleared — starting fresh.", mention_author=False)
        except Exception as exc:
            await message.reply(f"⚠️ Could not reset: {exc}", mention_author=False)
        return

    if normalized_command in CLOSE_CHAT_COMMANDS:
        session_data = _load_session_for_close()
        if not session_data:
            await message.reply(
                "No active Larry chat session is available to close. If you just want a fresh start, use `!reset`.",
                mention_author=False,
            )
            return

        report_markdown = None
        async with message.channel.typing():
            close_prompt = _build_close_chat_prompt(session_data)
            report_markdown, _ = await _ask_larry(
                close_prompt,
                session_id=session_data.get("session_id"),
            )

        if (
            not report_markdown
            or report_markdown.startswith("⚠️")
            or "# Session Close Report" not in report_markdown
        ):
            report_markdown = _build_fallback_close_report(session_data)

        try:
            report_path = _save_session_report(report_markdown, session_data)
            archive_path = _save_session_archive(session_data)
            _clear_session()
            response = (
                "🧾 Session closed and reset.\n"
                f"Saved closeout report: {report_path}\n\n"
                + (f"Saved session archive: {archive_path}\n\n" if archive_path else "\n")
                + f"{report_markdown.strip()}"
            )
            await _send_long_message(message.channel, response, reference=message)
        except Exception as exc:
            await message.reply(
                f"⚠️ Could not save the session close report: {exc}",
                mention_author=False,
            )
        return

    ts = _timestamp()
    saved_files = []
    note_path = None

    # --- Save text content to inbox (for the record) --------------------------
    if message.content.strip():
        filename = f"{ts}_note.md"
        filepath = INBOX_DIR / filename
        try:
            filepath.write_text(_build_note(message), encoding="utf-8")
            saved_files.append(filename)
            note_path = str(filepath)
            _index_saved_artifact(filepath)
            log.info("Saved note: %s", filename)
        except Exception as exc:
            log.error("Failed to save note: %s", exc)

    # --- Save attachments -----------------------------------------------------
    attachment_paths = []
    for attachment in message.attachments:
        safe_name = attachment.filename.replace(" ", "_")
        filename = f"{ts}_{safe_name}"
        filepath = INBOX_DIR / filename
        try:
            await attachment.save(filepath)
            saved_files.append(filename)
            attachment_paths.append(str(filepath))
            _index_saved_artifact(filepath)
            log.info("Saved attachment: %s (%d bytes)", filename, attachment.size)
        except Exception as exc:
            log.error("Failed to save attachment %s: %s", attachment.filename, exc)

    # --- Build prompt for Larry -----------------------------------------------
    prompt_parts = []
    task_kind = _infer_task_kind(message.content, attachment_paths)

    if message.content.strip():
        prompt_parts.append(message.content.strip())
        memory_block = await _get_relevant_memory(message.content.strip(), task_kind)
        if memory_block:
            prompt_parts.append(f"\n{memory_block}")

    if note_path:
        prompt_parts.append(
            "\n[This Discord message was saved for reference at:\n"
            f"- {note_path}\n"
            "If you need to process it as a file, prefer that path instead of writing a temporary script.]"
        )

    if attachment_paths:
        files_list = "\n".join(f"- {p}" for p in attachment_paths)
        prompt_parts.append(
            f"\n[The user also sent these files via Discord:\n{files_list}\n"
            "Please process them as appropriate.]"
        )

    if not prompt_parts:
        return  # Nothing to process

    prompt = "\n".join(prompt_parts)

    if message.content.strip():
        MEMORY.add(message.channel.id, "user", message.content.strip())

    # --- Send to Larry and reply ----------------------------------------------
    session_id = _load_session(task_kind, message.content.strip())
    # Typing indicator is best-effort — skip silently on 429 rate limit
    try:
        async with message.channel.typing():
            response, new_session_id = await _ask_larry(prompt, session_id=session_id)
    except discord.HTTPException as exc:
        if exc.status == 429:
            log.warning("Typing indicator rate-limited (429); continuing without it")
            response, new_session_id = await _ask_larry(prompt, session_id=session_id)
        else:
            raise
    active_session_id = new_session_id or session_id
    reply_path = None
    if response.strip():
        try:
            saved_reply_path = _save_larry_reply(
                response,
                session_id=active_session_id,
                task_kind=task_kind,
                replying_to_message_id=message.id,
            )
            reply_path = _rel_path(saved_reply_path) if saved_reply_path else None
        except Exception as exc:
            log.warning("Could not save Larry reply artifact: %s", exc)
    if active_session_id:
        turn = _build_session_turn(
            message_id=message.id,
            note_path=_rel_path(Path(note_path)) if note_path else None,
            attachment_paths=[_rel_path(Path(path)) for path in attachment_paths],
            reply_path=reply_path,
            prompt_preview=message.content.strip(),
        )
        _save_session(active_session_id, task_kind, message.content.strip(), turn=turn)

    if response.strip() and not response.startswith("⚠️"):
        MEMORY.add(message.channel.id, "assistant", response)
    await _send_long_message(message.channel, response, reference=message)

    # Turn complete — move handled inbox files into processed/ so filesystem
    # state stays meaningful instead of accumulating in the top-level folder.
    _move_to_processed(
        [Path(note_path) if note_path else None]
        + [Path(p) for p in attachment_paths]
        + [_resolve_saved_path(reply_path)]
    )


async def _message_worker():
    """Worker that processes messages one at a time from the queue."""
    while True:
        message = await message_queue.get()
        try:
            await _process_message(message)
        except Exception as exc:
            log.error("Error processing message: %r", exc, exc_info=True)
            # Don't send an error reply for rate-limit errors — it creates a feedback loop
            if isinstance(exc, discord.HTTPException) and exc.status == 429:
                log.warning("Suppressing error reply for 429 rate limit to avoid feedback loop")
            else:
                body = str(exc) or repr(exc) or exc.__class__.__name__
                try:
                    await _discord_send_with_retry(
                        lambda b=body, e=exc: message.reply(
                            f"⚠️ Something went wrong ({e.__class__.__name__}): {b[:200]}",
                            mention_author=False,
                        )
                    )
                except Exception:
                    pass
        finally:
            queued_message_ids.discard(message.id)
            _mark_message_processed(message.id)
            message_queue.task_done()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@client.event
async def on_ready():
    global message_queue
    _load_message_state()
    log.info("Bot connected as %s (id %s)", client.user, client.user.id)
    channel = await _resolve_channel()
    if channel:
        log.info("Monitoring channel: #%s (%s)", channel.name, CHANNEL_ID)
    else:
        log.warning("Could not find channel with ID %s — will listen anyway", CHANNEL_ID)

    # Create queue on the running event loop and start the worker
    if message_queue is None:
        message_queue = asyncio.Queue()
        asyncio.get_event_loop().create_task(_message_worker())
    if channel is not None:
        await _backfill_missed_messages(channel)
    log.info("Larry is ready to chat! (Claude CLI: %s)", CLAUDE_PATH)


@client.event
async def on_message(message: discord.Message):
    await _queue_message(message, source="live", acknowledge=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Starting PKA Discord bridge bot (two-way Larry chat)...")
    log.info("PKA root: %s", PKA_ROOT)
    log.info("Inbox directory: %s", INBOX_DIR)
    log.info("Claude CLI: %s", CLAUDE_PATH)
    log.info(
        "Claude permission mode: %s",
        "dangerously-skip-permissions enabled" if ALLOW_DANGEROUS_SKIP_PERMISSIONS else "safe default",
    )
    client.run(BOT_TOKEN, log_handler=None)
