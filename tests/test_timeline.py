from datetime import datetime, timezone

from dnd_assistant.store import Event
from dnd_assistant.timeline import TimelineWriter, render_line, render_state


def at(h, m, s):
    return datetime(2026, 10, 31, h, m, s, tzinfo=timezone.utc)


def ev(kind, text, speaker=None, data=None, ts=at(21, 14, 5), source="foundry"):
    return Event(ts=ts, source=source, kind=kind, speaker=speaker, text=text, data=data or {})


def test_render_speech():
    e = ev("speech", "The door creaks open…", speaker="Table", source="voice")
    assert render_line(e) == '21:14:05 Table: "The door creaks open…"'


def test_render_chat_styles():
    for style, tag in (("ic", "[IC]"), ("ooc", "[OOC]"), ("emote", "[EMOTE]")):
        e = ev("chat", "You must help my sister.", speaker="Ismark", data={"style": style}, ts=at(21, 14, 12))
        assert render_line(e) == f'21:14:12 {tag} Ismark: "You must help my sister."'


def test_render_roll():
    e = ev("roll", "", speaker="Paylon", ts=at(21, 14, 20),
           data={"label": "Athletics (grapple)", "total": 19, "dice": "d20=15"})
    assert render_line(e) == "21:14:20 🎲 Paylon: Athletics (grapple) 19 [d20=15]"


def test_render_roll_crit_and_fumble():
    base = {"label": "Blood Spear - Attack Roll", "total": 27, "dice": "d20=20"}
    assert render_line(ev("roll", "", "Paylon", base | {"crit": True})).endswith("[d20=20] CRIT")
    assert render_line(ev("roll", "", "Paylon", base | {"dice": "d20=1", "fumble": True})).endswith("[d20=1] FUMBLE")


def test_render_combat_gap_note():
    assert render_line(ev("combat", "Round 3: Paylon's turn", ts=at(21, 15, 2))) == "21:15:02 ⚔ Round 3: Paylon's turn"
    assert render_line(ev("gap", "Table audio lost: device gone", source="system")) == "21:14:05 ⚠ Table audio lost: device gone"
    assert render_line(ev("note", "Session started", source="system")) == "21:14:05 📝 Session started"


def test_render_collapses_newlines_and_strips_html():
    e = ev("chat", "<p>Hello\nthere</p>", speaker="Paylon", data={"style": "ic"})
    assert render_line(e) == '21:14:05 [IC] Paylon: "Hello there"'


def test_render_unescapes_entities_and_handles_long_text():
    e = ev("speech", "Fish &amp; chips\r\n\t" + "word " * 500, speaker="Me", source="voice")
    line = render_line(e)
    assert "\n" not in line and "\r" not in line
    assert line.startswith('21:14:05 Me: "Fish & chips word word')


def test_state_not_in_timeline():
    assert render_line(ev("state", "", data={"actor": "Paylon"})) is None


def test_render_state():
    out = render_state({
        "actor": "Paylon",
        "hp": {"value": 31, "max": 45, "temp": 5},
        "resources": {"Moxie": {"value": 2, "max": 4}},
        "conditions": ["prone"],
        "effects": ["Iron Chin"],
    })
    assert "HP 31/45 (+5 temp)" in out
    assert "Moxie 2/4" in out
    assert "prone" in out
    assert "Iron Chin" in out


def test_render_state_no_temp_no_conditions():
    out = render_state({"actor": "Paylon", "hp": {"value": 50, "max": 58, "temp": None},
                        "resources": {}, "conditions": [], "effects": []})
    assert "HP 50/58" in out and "temp" not in out
    assert "Conditions: none" in out


def test_writer_appends_with_header_once(tmp_path):
    path = tmp_path / "timeline.md"
    w = TimelineWriter(path)
    w.append(ev("speech", "one", speaker="Me", source="voice"))
    w.append(ev("state", "", data={"actor": "Paylon"}))
    w2 = TimelineWriter(path)
    w2.append(ev("speech", "two", speaker="Me", source="voice"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Timeline 2026-10-31"
    assert [l for l in lines if l.startswith("#")] == ["# Timeline 2026-10-31"]
    assert lines[-2:] == ['21:14:05 Me: "one"', '21:14:05 Me: "two"']
