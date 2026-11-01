"""Replay a recorded mix of voice and Foundry events through the whole pipeline, offline."""

import json
import wave
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from dnd_assistant.pipeline import Pipeline
from dnd_assistant.scribe import NotesFile, Scribe
from dnd_assistant.session import session_dir
from dnd_assistant.status import Status
from dnd_assistant.store import Event, Store
from dnd_assistant.timeline import TimelineWriter

FIXTURES = Path(__file__).parent / "fixtures"
EVENTS = FIXTURES / "replay" / "events.jsonl"


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def complete_json(self, system, user, schema, timeout):
        self.calls += 1
        return self.result


def load_events():
    for line in EVENTS.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        yield Event(ts=datetime.fromisoformat(d["ts"].replace("Z", "+00:00")), source=d["source"], kind=d["kind"],
                    speaker=d.get("speaker"), text=d.get("text", ""), data=d.get("data") or {})


def make_session(tmp_path):
    folder = session_dir(tmp_path / "Curse of Strahd", date(2026, 10, 31))
    store = Store(folder / "session.db")
    status = Status()
    pipeline = Pipeline(store, TimelineWriter(folder / "timeline.md"), folder / "paylon-now.md", "Paylon", status)
    return folder, store, status, pipeline


def test_replay_builds_session_files(tmp_path):
    folder, store, status, pipeline = make_session(tmp_path)
    events = list(load_events())
    for e in events:
        pipeline.handle(e)

    client = FakeClient({"facts": [
        {"section": "People", "subject": "Ismark", "fact": "Ireena's brother; asked for help", "time": "21:02", "certain": True},
        {"section": "Quests & Promises", "subject": "Escort Ireena", "fact": "Ismark promised 50 gold to get Ireena to Vallaki", "time": "21:08", "certain": True},
    ]})
    notes = NotesFile(folder / "session-notes.md")
    assert Scribe(store, notes, client, status).run_once(datetime(2026, 10, 31, 21, 10).astimezone()) is True
    store.close()

    timeline = [l for l in (folder / "timeline.md").read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
    shown = [e for e in events if e.kind != "state"]
    assert len(timeline) == len(shown)
    assert [l[:8] for l in timeline] == [e.ts.strftime("%H:%M:%S") for e in shown]
    assert timeline[0].endswith('Table: "The innkeeper of the Blue Water Inn says Ismark is looking for help."')
    assert any(l.endswith('[IC] Paylon: "Hello there"') for l in timeline)
    assert any(l.endswith('[OOC] Wayne: "Rules check"') for l in timeline)
    assert any("🎲 Paylon: Strength (Athletics) Check 13 [d20=3]" in l for l in timeline)
    assert any("⚔ Round 1: Paylon's turn" in l for l in timeline)

    now = (folder / "paylon-now.md").read_text(encoding="utf-8")
    assert "HP 50/58" in now
    assert "Moxie 1/4" in now  # the last state event, after Moxie was spent
    assert "prone" in now

    text = (folder / "session-notes.md").read_text(encoding="utf-8")
    people, quests = text.index("## People"), text.index("## Quests & Promises")
    ismark = text.index("- **Ismark**: Ireena's brother; asked for help (21:02)")
    escort = text.index("- **Escort Ireena**: Ismark promised 50 gold to get Ireena to Vallaki (21:08)")
    assert people < ismark < quests < escort
    assert client.calls == 1


def load_wav(path):
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


@pytest.mark.gpu
def test_replay_audio_end_to_end(tmp_path):
    from dnd_assistant.audio import Segmenter, is_hallucination, silero_detector
    from dnd_assistant.glossary import build_prompt
    from dnd_assistant.listener import Transcriber

    folder, store, _, pipeline = make_session(tmp_path)
    audio = load_wav(FIXTURES / "audio" / "sample.wav")
    segmenter = Segmenter(silero_detector())
    start = datetime(2026, 10, 31, 21, 0, 0).astimezone()
    segments = []
    for i in range(0, len(audio), 8000):  # 0.5 s chunks, like the live capture
        segments += segmenter.feed(audio[i:i + 8000], start + timedelta(seconds=i / 16000))
    segments += segmenter.feed(np.zeros(16000, dtype=np.float32), start + timedelta(seconds=len(audio) / 16000))
    assert segments

    transcriber = Transcriber("large-v3-turbo", "en")
    prompt = build_prompt(["Paylon", "Ireena", "Ismark", "Strahd", "Barovia", "Vallaki", "Krezk"])
    for seg in segments:
        text = transcriber.transcribe(seg.audio, prompt)
        if not is_hallucination(text, len(seg.audio) / 16000):
            pipeline.handle(Event(ts=seg.start, source="voice", kind="speech", speaker="Me", text=text))
    store.close()

    timeline = (folder / "timeline.md").read_text(encoding="utf-8")
    assert 'Me: "' in timeline
    assert "Ireena" in timeline
