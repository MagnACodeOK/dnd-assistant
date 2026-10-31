import threading
from datetime import datetime, timezone

import pytest

from dnd_assistant.pipeline import Pipeline
from dnd_assistant.status import Status
from dnd_assistant.store import Event, Store
from dnd_assistant.timeline import TimelineWriter

T0 = datetime(2026, 10, 31, 21, 14, 5, tzinfo=timezone.utc)


@pytest.fixture
def parts(tmp_path):
    store = Store(tmp_path / "session.db")
    status = Status()
    pipe = Pipeline(store, TimelineWriter(tmp_path / "timeline.md"), tmp_path / "paylon-now.md", "Paylon", status)
    yield pipe, store, status, tmp_path
    store.close()


def state(actor, hp):
    return Event(ts=T0, source="foundry", kind="state", speaker=actor, text="",
                 data={"actor": actor, "hp": {"value": hp, "max": 58, "temp": 0},
                       "resources": {}, "conditions": [], "effects": []})


def test_speech_stored_and_rendered(parts):
    pipe, store, status, tmp = parts
    event_id = pipe.handle(Event(ts=T0, source="voice", kind="speech", speaker="Table", text="Hello"))
    assert [e.id for e in store.since(0)] == [event_id]
    assert 'Table: "Hello"' in (tmp / "timeline.md").read_text(encoding="utf-8")
    assert status.speech == 1 and status.foundry == 0


def test_foundry_counter(parts):
    pipe, _, status, _ = parts
    pipe.handle(Event(ts=T0, source="foundry", kind="chat", speaker="Ismark", text="Help", data={"style": "ic"}))
    assert status.foundry == 1 and status.speech == 0


def test_state_for_actor_writes_file(parts):
    pipe, _, _, tmp = parts
    pipe.handle(state("Paylon", 50))
    assert "HP 50/58" in (tmp / "paylon-now.md").read_text(encoding="utf-8")
    pipe.handle(state("Paylon", 41))
    assert "HP 41/58" in (tmp / "paylon-now.md").read_text(encoding="utf-8")


def test_state_for_other_actor_ignored_for_file(parts):
    pipe, store, _, tmp = parts
    pipe.handle(state("Liam Shieldheart", 52))
    assert not (tmp / "paylon-now.md").exists()
    assert len(store.since(0)) == 1


def test_status_line_format():
    s = Status()
    s.speech, s.foundry = 312, 48
    assert s.line() == "🎙 312 lines · 🎲 48 Foundry events · ✍ notes —"
    s.notes_at = datetime(2026, 10, 31, 21, 40, 12)
    s.warn("scribe", "LM Studio unreachable")
    s.warn("silent-Table", "Table silent 10 min")
    assert s.line() == "🎙 312 lines · 🎲 48 Foundry events · ✍ notes 21:40 · ⚠ LM Studio unreachable · ⚠ Table silent 10 min"
    s.clear("scribe")
    s.clear("not-set")
    assert s.line().endswith("✍ notes 21:40 · ⚠ Table silent 10 min")


def test_handle_from_threads(parts):
    pipe, store, status, tmp = parts

    def worker(n):
        for i in range(50):
            pipe.handle(Event(ts=T0, source="voice", kind="speech", speaker=f"T{n}", text=f"line {i}"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.since(0)) == 200
    lines = [l for l in (tmp / "timeline.md").read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    assert len(lines) == 200
    assert status.speech == 200
