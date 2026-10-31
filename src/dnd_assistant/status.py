"""The one-line live status shown in the console."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Status:
    speech: int = 0
    foundry: int = 0
    notes_at: datetime | None = None
    warnings: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def warn(self, key: str, msg: str) -> None:
        with self._lock:
            self.warnings[key] = msg

    def clear(self, key: str) -> None:
        with self._lock:
            self.warnings.pop(key, None)

    def line(self, width: int | None = None) -> str:
        """The status line; with `width`, cut to fit so a console `\\r` can overwrite it."""
        with self._lock:
            notes = self.notes_at.strftime("%H:%M") if self.notes_at else "—"
            parts = [f"🎙 {self.speech} lines", f"🎲 {self.foundry} Foundry events", f"✍ notes {notes}"]
            parts += [f"⚠ {msg}" for msg in self.warnings.values()]
        line = " · ".join(parts)
        if width is not None and len(line) > width:
            line = line[: width - 1] + "…"
        return line
