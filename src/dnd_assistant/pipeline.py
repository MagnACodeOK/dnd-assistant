"""Every event goes through here: store it, render it, count it."""

from __future__ import annotations

import threading
from pathlib import Path

from .status import Status
from .store import Event, Store
from .timeline import TimelineWriter, render_state


class Pipeline:
    def __init__(self, store: Store, timeline: TimelineWriter, state_path: Path, actor_name: str, status: Status):
        self.store = store
        self.timeline = timeline
        self.state_path = Path(state_path)
        self.actor_name = actor_name
        self.status = status
        self._lock = threading.Lock()

    def handle(self, e: Event) -> int:
        with self._lock:
            event_id = self.store.add(e)
            self.timeline.append(e)
            if e.kind == "speech":
                self.status.speech += 1
            if e.source == "foundry":
                self.status.foundry += 1
            if e.kind == "state" and e.data.get("actor") == self.actor_name:
                self.state_path.write_text(render_state(e.data), encoding="utf-8")
        return event_id
