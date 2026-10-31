"""SQLite event store: one ordered timeline of voice, Foundry and system events."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

SOURCES = frozenset({"voice", "foundry", "system"})
KINDS = frozenset({"speech", "chat", "roll", "combat", "state", "gap", "note"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    t REAL NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    speaker TEXT,
    text TEXT NOT NULL,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_t ON events(t);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(text, speaker);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Event:
    ts: datetime
    source: str
    kind: str
    speaker: str | None
    text: str
    data: dict = field(default_factory=dict)
    id: int | None = None


class Store:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def add(self, e: Event) -> int:
        if e.source not in SOURCES:
            raise ValueError(f"unknown source {e.source!r}")
        if e.kind not in KINDS:
            raise ValueError(f"unknown kind {e.kind!r}")
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (ts, t, source, kind, speaker, text, data_json) VALUES (?,?,?,?,?,?,?)",
                (e.ts.isoformat(), e.ts.timestamp(), e.source, e.kind, e.speaker, e.text,
                 json.dumps(e.data, ensure_ascii=False)),
            )
            event_id = cur.lastrowid
            self._db.execute(
                "INSERT INTO events_fts (rowid, text, speaker) VALUES (?,?,?)",
                (event_id, e.text, e.speaker or ""),
            )
            self._db.commit()
        return event_id

    def since(self, event_id: int) -> list[Event]:
        return self._query("SELECT * FROM events WHERE id > ? ORDER BY id", (event_id,))

    def tail(self, minutes: float, now: datetime | None = None) -> list[Event]:
        if now is None:
            with self._lock:
                row = self._db.execute("SELECT MAX(t) FROM events").fetchone()
            if row[0] is None:
                return []
            end = row[0]
        else:
            end = now.timestamp()
        start = end - timedelta(minutes=minutes).total_seconds()
        return self._query("SELECT * FROM events WHERE t >= ? AND t <= ? ORDER BY id", (start, end))

    def search(self, query: str, limit: int = 50) -> list[Event]:
        # Quote each word as an FTS phrase so user input can't form FTS syntax.
        words = re.findall(r"\w+", query)
        if not words:
            return []
        match = " ".join(f'"{w}"' for w in words)
        return self._query(
            "SELECT events.* FROM events_fts JOIN events ON events.id = events_fts.rowid "
            "WHERE events_fts MATCH ? ORDER BY events.id LIMIT ?",
            (match, limit),
        )

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _query(self, sql: str, params: tuple) -> list[Event]:
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [
            Event(ts=datetime.fromisoformat(ts), source=source, kind=kind, speaker=speaker,
                  text=text, data=json.loads(data_json), id=id_)
            for id_, ts, _t, source, kind, speaker, text, data_json in rows
        ]
