#!/usr/bin/env python3
"""
Rolling conversation memory for the PKA Discord bots.

Keeps the last N exchanges per channel so the bot can resolve follow-ups
("make it shorter", "what was the second one?") instead of treating every
message as a cold start. Persisted as a JSON file under data/ — plain file
I/O, no pka.db writes, so it is satellite-safe under the Devendorf contract.

Turns are stored as {"role": "user"|"assistant", "content": str, "ts": iso}.
Content is clipped per turn and the window is bounded, so the file and the
prompt both stay small.

Usage:
    memory = ConversationMemory(PKA_ROOT / "data" / "conversation-history.json")
    memory.add(channel_id, "user", text)
    memory.add(channel_id, "assistant", reply)
    msgs = memory.messages(channel_id)   # ready to splice into llm.chat()
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

MAX_TURNS = 16          # turns kept per channel (user + assistant combined)
MAX_TURN_CHARS = 1200   # per-turn clip so one giant paste doesn't eat the window
STALE_MINUTES = 240     # drop context older than this; yesterday's thread is over


class ConversationMemory:
    def __init__(self, path: Path, max_turns: int = MAX_TURNS):
        self.path = path
        self.max_turns = max_turns
        self._lock = threading.Lock()
        self._data: dict[str, list[dict]] = {}
        try:
            self._data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}

    def add(self, channel_id, role: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        if len(content) > MAX_TURN_CHARS:
            content = content[:MAX_TURN_CHARS] + " …[clipped]"
        with self._lock:
            turns = self._data.setdefault(str(channel_id), [])
            turns.append({
                "role": role,
                "content": content,
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
            del turns[: max(0, len(turns) - self.max_turns)]
            self._save()

    def messages(self, channel_id) -> list[dict[str, str]]:
        """Recent turns as chat messages, oldest first, stale ones dropped."""
        with self._lock:
            turns = list(self._data.get(str(channel_id), []))
        fresh = []
        now = datetime.now()
        for t in turns:
            try:
                age_min = (now - datetime.fromisoformat(t["ts"])).total_seconds() / 60
            except Exception:
                age_min = 0
            if age_min <= STALE_MINUTES:
                fresh.append({"role": t["role"], "content": t["content"]})
        return fresh

    def clear(self, channel_id) -> None:
        with self._lock:
            self._data.pop(str(channel_id), None)
            self._save()

    def _save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass  # memory is a nicety; never let it take down the bot
