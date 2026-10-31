from datetime import date, datetime, timedelta

import pytest

from dnd_assistant.cli import main
from dnd_assistant.session import session_dir
from dnd_assistant.store import Event, Store


@pytest.fixture
def campaign(tmp_path):
    c = tmp_path / "Curse of Strahd"
    c.mkdir()
    return c


@pytest.fixture
def config(tmp_path, campaign):
    p = tmp_path / "config.toml"
    token = (tmp_path / "token").as_posix()
    p.write_text(f"campaign = '{campaign.as_posix()}'\nfoundry_origins = []\ntoken_file = '{token}'\n", encoding="utf-8")
    return p


def populate(campaign, texts, day=date(2026, 10, 31)):
    d = session_dir(campaign, day)
    store = Store(d / "session.db")
    t = datetime(2026, 10, 31, 21, 0, 0).astimezone()
    for i, text in enumerate(texts):
        store.add(Event(ts=t + timedelta(minutes=i), source="voice", kind="speech", speaker="Table", text=text))
    store.close()
    return d


def test_tail_prints_latest_session_lines(config, campaign, capsys):
    populate(campaign, ["old line", "x", "y", "z", "a", "b", "c", "d", "e", "f", "g", "h", "newest line"])
    assert main(["--config", str(config), "tail", "--minutes", "3"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 4
    assert out[-1].endswith('Table: "newest line"')
    assert not any("old line" in l for l in out)


def test_tail_with_session_path(config, campaign, capsys):
    d = populate(campaign, ["only line"])
    assert main(["--config", str(config), "tail", "--session", str(d)]) == 0
    assert 'Table: "only line"' in capsys.readouterr().out


def test_search_prints_matches(config, campaign, capsys):
    populate(campaign, ["Ireena is Ismark's sister", "The road to Vallaki"])
    assert main(["--config", str(config), "search", "Ireena", "sister"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and "Ireena is Ismark's sister" in out[0]


def test_tail_without_session_exits_2(config, capsys):
    assert main(["--config", str(config), "tail"]) == 2
    assert "no session" in capsys.readouterr().err.lower()


def test_missing_config_exits_2(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nope.toml"), "tail"]) == 2
    assert "config.example.toml" in capsys.readouterr().err


def test_token_hidden_by_default(config, tmp_path, capsys):
    assert main(["--config", str(config), "token"]) == 0
    out = capsys.readouterr().out
    token = (tmp_path / "token").read_text(encoding="utf-8").strip()
    assert str(tmp_path / "token") in out
    assert token not in out
    assert main(["--config", str(config), "token", "--show"]) == 0
    assert token in capsys.readouterr().out
