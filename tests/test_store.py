from datetime import datetime, timedelta, timezone

import pytest

from dnd_assistant.store import Event, Store

T0 = datetime(2026, 10, 31, 21, 0, 0, tzinfo=timezone.utc)


def speech(text, ts=T0, speaker="Table"):
    return Event(ts=ts, source="voice", kind="speech", speaker=speaker, text=text)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "session.db")
    yield s
    s.close()


def test_add_and_since_preserve_order(store):
    ids = [store.add(speech(f"line {i}", T0 + timedelta(seconds=i))) for i in range(5)]
    assert ids == sorted(ids)
    events = store.since(0)
    assert [e.text for e in events] == [f"line {i}" for i in range(5)]
    assert [e.id for e in events] == ids
    assert [e.text for e in store.since(ids[2])] == ["line 3", "line 4"]


def test_event_roundtrips_fields(store):
    e = Event(ts=T0, source="foundry", kind="roll", speaker="Paylon", text="Athletics",
              data={"total": 19, "dice": "d20=15"})
    store.add(e)
    (got,) = store.since(0)
    assert (got.ts, got.source, got.kind, got.speaker, got.text, got.data) == \
        (T0, "foundry", "roll", "Paylon", "Athletics", {"total": 19, "dice": "d20=15"})


def test_add_rejects_unknown_kind(store):
    with pytest.raises(ValueError):
        store.add(Event(ts=T0, source="voice", kind="shout", speaker=None, text="x"))


def test_add_rejects_unknown_source(store):
    with pytest.raises(ValueError):
        store.add(Event(ts=T0, source="radio", kind="speech", speaker=None, text="x"))


def test_tail_filters_by_minutes(store):
    now = T0 + timedelta(hours=1)
    for mins in (20, 5, 1):
        store.add(speech(f"{mins} ago", now - timedelta(minutes=mins)))
    assert [e.text for e in store.tail(10, now)] == ["5 ago", "1 ago"]


def test_tail_defaults_to_newest_event(store):
    newest = datetime.now(timezone.utc) - timedelta(hours=2)
    for mins in (30, 8, 0):
        store.add(speech(f"{mins} before", newest - timedelta(minutes=mins)))
    assert [e.text for e in store.tail(10)] == ["8 before", "0 before"]


def test_tail_empty_store(store):
    assert store.tail(10) == []


def test_search_fts_matches_words(store):
    store.add(speech("Ireena is Ismark's sister"))
    assert [e.text for e in store.search("Ireena")] == ["Ireena is Ismark's sister"]
    assert store.search("Strahd") == []


def test_search_matches_speaker(store):
    store.add(speech("You must help my sister.", speaker="Ismark"))
    assert len(store.search("Ismark")) == 1


def test_search_handles_quotes_and_symbols(store):
    store.add(speech("Ireena is Ismark's sister"))
    assert len(store.search('sister"')) == 1
    store.search("AND OR NOT ( ) * : ^ -")
    assert store.search("") == []


def test_meta_roundtrip(store):
    assert store.get_meta("scribe_last_id") is None
    store.set_meta("scribe_last_id", "7")
    store.set_meta("scribe_last_id", "9")
    assert store.get_meta("scribe_last_id") == "9"


def test_reopen_keeps_events(tmp_path):
    path = tmp_path / "session.db"
    s = Store(path)
    for i in range(3):
        s.add(speech(f"line {i}"))
    s.close()
    s2 = Store(path)
    assert len(s2.since(0)) == 3
    assert len(s2.search("line")) == 3
    s2.close()
