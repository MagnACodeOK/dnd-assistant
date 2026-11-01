import json
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from dnd_assistant.scribe import (
    SECTIONS, Fact, LMStudioClient, NotesFile, Scribe, ScribeError, parse_facts,
)
from dnd_assistant.status import Status
from dnd_assistant.store import Event, Store

T0 = datetime(2026, 10, 31, 21, 0, 0, tzinfo=timezone.utc)


def fact(section="People", subject="Ireena", text="Ismark's sister", time="21:14", certain=True):
    return {"section": section, "subject": subject, "fact": text, "time": time, "certain": certain}


class FakeClient:
    """Returns scripted results in order; an Exception instance is raised instead."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def complete_json(self, system, user, schema, timeout):
        self.calls.append({"system": system, "user": user, "schema": schema, "timeout": timeout})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "session.db")
    yield s
    s.close()


def add_speech(store, text, minute=14):
    return store.add(Event(ts=T0.replace(minute=minute), source="voice", kind="speech", speaker="Table", text=text))


def make_scribe(store, tmp_path, client, **kw):
    status = Status()
    notes = NotesFile(tmp_path / "session-notes.md")
    return Scribe(store, notes, client, status, **kw), notes, status


# --- parsing and the notes file -------------------------------------------------------------

def test_sections_fixed_order():
    assert SECTIONS == ("People", "Places", "Clues & Lore", "Quests & Promises", "Loot & Rewards", "Party Decisions")


def test_parse_facts_drops_bad_entries():
    obj = {"facts": [fact(), fact(section="Monsters"), fact(subject=" "), fact(text=""), {"section": "People"}]}
    assert parse_facts(obj) == [Fact("People", "Ireena", "Ismark's sister", "21:14", True)]


def test_parse_facts_rejects_wrong_shape():
    for bad in (None, [], {"facts": "x"}, {"nope": []}):
        with pytest.raises(ScribeError):
            parse_facts(bad)


def test_merge_appends_and_skips_duplicates(tmp_path):
    notes = NotesFile(tmp_path / "session-notes.md")
    f1 = Fact("People", "Ireena", "Ismark's sister", "21:14", True)
    f2 = Fact("Places", "Vallaki", "Walled town to the west", "21:20", True)
    assert notes.merge([f1, f2]) == 2
    assert notes.merge([Fact("People", " ireena ", "ismark's SISTER ", "21:30", True)]) == 0
    assert notes.merge([Fact("People", "Ireena", "Has bite marks", "21:31", True)]) == 1
    text = (tmp_path / "session-notes.md").read_text(encoding="utf-8")
    assert "- **Ireena**: Ismark's sister (21:14)" in text
    assert "- **Ireena**: Has bite marks (21:31)" in text
    assert text.index("## People") < text.index("## Places")
    assert text.index("Has bite marks") < text.index("## Places")


def test_uncertain_marked(tmp_path):
    notes = NotesFile(tmp_path / "session-notes.md")
    notes.merge([Fact("Clues & Lore", "Strahd", "May be watching the inn", "21:40", False)])
    assert "- **Strahd**: May be watching the inn (21:40) (?)" in (tmp_path / "session-notes.md").read_text(encoding="utf-8")


def test_merge_flattens_multiline_fact(tmp_path):
    notes = NotesFile(tmp_path / "session-notes.md")
    notes.merge([Fact("People", "Ismark", "Wants\nhelp", "21:14", True)])
    assert "- **Ismark**: Wants help (21:14)" in (tmp_path / "session-notes.md").read_text(encoding="utf-8")


def test_notes_file_reparse_roundtrip(tmp_path):
    facts = [Fact("People", "Ireena", "Ismark's sister", "21:14", True),
             Fact("Loot & Rewards", "Party", "50 gp from Ismark", "21:50", False)]
    NotesFile(tmp_path / "session-notes.md").merge(facts)
    reopened = NotesFile(tmp_path / "session-notes.md")
    assert reopened.merge(facts) == 0
    assert reopened.subjects() == ["People / Ireena", "Loot & Rewards / Party"]


# --- running the scribe ---------------------------------------------------------------------

def test_run_once_advances_last_id_on_success(store, tmp_path):
    add_speech(store, "Ireena is Ismark's sister")
    last = add_speech(store, "Vallaki is to the west", minute=20)
    client = FakeClient({"facts": [fact()]})
    scribe, notes, status = make_scribe(store, tmp_path, client)
    assert scribe.run_once(T0) is True
    assert store.get_meta("scribe_last_id") == str(last)
    assert notes.subjects() == ["People / Ireena"]
    assert status.notes_at == T0
    user = client.calls[0]["user"]
    assert "Existing subjects:" in user and "New events:" in user
    assert 'Table: "Ireena is Ismark\'s sister"' in user
    assert "Record only facts stated in the events." in client.calls[0]["system"]
    assert client.calls[0]["timeout"] == 120.0


def test_run_once_invalid_json_twice_keeps_last_id(store, tmp_path):
    add_speech(store, "hello")
    client = FakeClient({"garbage": 1}, {"facts": "nope"})
    scribe, _, status = make_scribe(store, tmp_path, client)
    assert scribe.run_once(T0) is False
    assert len(client.calls) == 2
    assert store.get_meta("scribe_last_id") is None
    assert "scribe" in status.warnings


def test_run_once_invalid_then_valid_succeeds(store, tmp_path):
    last = add_speech(store, "hello")
    scribe, _, _ = make_scribe(store, tmp_path, FakeClient({"garbage": 1}, {"facts": []}))
    assert scribe.run_once(T0) is True
    assert store.get_meta("scribe_last_id") == str(last)


def test_run_once_client_error_warns_and_returns_false(store, tmp_path):
    add_speech(store, "hello")
    client = FakeClient(ScribeError("401 Unauthorized: is LMSTUDIO_API_KEY set?"))
    scribe, _, status = make_scribe(store, tmp_path, client)
    assert scribe.run_once(T0) is False
    assert len(client.calls) == 1
    assert "401" in status.warnings["scribe"]
    assert store.get_meta("scribe_last_id") is None


def test_run_once_unexpected_exception_never_raises(store, tmp_path):
    add_speech(store, "hello")
    scribe, _, status = make_scribe(store, tmp_path, FakeClient(KeyError("choices")))
    assert scribe.run_once(T0) is False
    assert "scribe" in status.warnings


def test_next_success_includes_missed_events(store, tmp_path):
    add_speech(store, "first thing")
    client = FakeClient(ScribeError("down"), {"facts": []})
    scribe, _, status = make_scribe(store, tmp_path, client)
    assert scribe.run_once(T0) is False
    last = add_speech(store, "second thing", minute=30)
    assert scribe.run_once(T0 + timedelta(minutes=5)) is True
    user = client.calls[1]["user"]
    assert "first thing" in user and "second thing" in user
    assert store.get_meta("scribe_last_id") == str(last)
    assert "scribe" not in status.warnings


def test_restart_does_not_reprocess(store, tmp_path):
    add_speech(store, "first thing")
    scribe, _, _ = make_scribe(store, tmp_path, FakeClient({"facts": [fact()]}))
    scribe.run_once(T0)
    add_speech(store, "after restart", minute=40)
    client = FakeClient({"facts": [fact()]})
    scribe2, notes2, _ = make_scribe(store, tmp_path, client)
    scribe2.run_once(T0 + timedelta(minutes=10))
    assert "first thing" not in client.calls[0]["user"]
    assert "after restart" in client.calls[0]["user"]
    assert "People / Ireena" in client.calls[0]["user"]
    assert (tmp_path / "session-notes.md").read_text(encoding="utf-8").count("Ismark's sister") == 1


def test_no_new_events_is_noop(store, tmp_path):
    client = FakeClient()
    scribe, _, _ = make_scribe(store, tmp_path, client)
    assert scribe.run_once(T0) is True
    assert client.calls == []


def test_state_only_events_skip_model(store, tmp_path):
    last = store.add(Event(ts=T0, source="foundry", kind="state", speaker="Paylon", text="", data={"actor": "Paylon"}))
    client = FakeClient()
    scribe, _, _ = make_scribe(store, tmp_path, client)
    assert scribe.run_once(T0) is True
    assert client.calls == []
    assert store.get_meta("scribe_last_id") == str(last)


def test_due_after_interval_or_burst(store, tmp_path):
    scribe, _, _ = make_scribe(store, tmp_path, FakeClient(), interval_s=300, burst=3)
    assert scribe.due(T0) is False  # no events
    add_speech(store, "one")
    assert scribe.due(T0) is False
    assert scribe.due(T0 + timedelta(seconds=299)) is False
    assert scribe.due(T0 + timedelta(seconds=300)) is True
    add_speech(store, "two")
    add_speech(store, "three")
    assert scribe.due(T0 + timedelta(seconds=1)) is True  # burst


def test_burst_does_not_hammer_after_failure(store, tmp_path):
    for i in range(3):
        add_speech(store, f"line {i}")
    scribe, _, _ = make_scribe(store, tmp_path, FakeClient(ScribeError("down")), interval_s=300, burst=3)
    assert scribe.due(T0) is True
    scribe.run_once(T0)
    assert scribe.due(T0 + timedelta(seconds=5)) is False
    assert scribe.due(T0 + timedelta(seconds=300)) is True


def test_loop_makes_final_pass_on_stop(store, tmp_path):
    add_speech(store, "late line")
    client = FakeClient({"facts": [fact()]})
    scribe, notes, _ = make_scribe(store, tmp_path, client, interval_s=3600, burst=1000)
    stop = threading.Event()
    stop.set()
    scribe.loop(stop)
    assert len(client.calls) == 1
    assert notes.subjects() == ["People / Ireena"]


# --- LM Studio client -----------------------------------------------------------------------

def lm_client(handler, key="k"):
    return LMStudioClient("http://lm.test/v1", "qwen/qwen3.5-9b", key, transport=httpx.MockTransport(handler))


def test_lmstudio_client_request_and_parse():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"facts": []}'}}]})

    assert lm_client(handler).complete_json("sys", "user", {"type": "object"}, 5.0) == {"facts": []}
    assert seen["auth"] == "Bearer k"
    assert seen["url"] == "http://lm.test/v1/chat/completions"
    assert seen["body"]["model"] == "qwen/qwen3.5-9b"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    # Qwen 3.5 otherwise "thinks" for 6-14k tokens (86-184 s, sometimes filling the 16K context);
    # with reasoning off the same batch took 11 s.
    assert seen["body"]["reasoning_effort"] == "none"


def test_lmstudio_client_errors_become_scribe_errors():
    cases = [
        lambda r: httpx.Response(401, json={"error": "unauthorized"}),
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
        lambda r: httpx.Response(200, json={"unexpected": True}),
    ]
    for handler in cases:
        with pytest.raises(ScribeError):
            lm_client(handler).complete_json("s", "u", {}, 5.0)

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ScribeError):
        lm_client(refuse).complete_json("s", "u", {}, 5.0)


def test_lmstudio_client_without_key_sends_no_auth():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    lm_client(handler, key=None).complete_json("s", "u", {}, 5.0)
    assert seen["auth"] is None
