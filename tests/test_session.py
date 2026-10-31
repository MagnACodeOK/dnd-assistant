import os
from datetime import date

from dnd_assistant.session import latest_session, session_dir

DAY = date(2026, 10, 31)


def test_creates_dated_folder(tmp_path):
    d = session_dir(tmp_path, DAY)
    assert d == tmp_path / "Live Sessions" / "2026-10-31"
    assert d.is_dir()


def test_same_day_reuses_folder(tmp_path):
    first = session_dir(tmp_path, DAY)
    (first / "session.db").write_bytes(b"x")
    again = session_dir(tmp_path, DAY)
    assert again == first
    assert (again / "session.db").exists()


def test_new_flag_adds_suffix(tmp_path):
    session_dir(tmp_path, DAY)
    assert session_dir(tmp_path, DAY, new=True).name == "2026-10-31-2"
    assert session_dir(tmp_path, DAY, new=True).name == "2026-10-31-3"


def test_new_flag_without_existing_folder_uses_plain_date(tmp_path):
    assert session_dir(tmp_path, DAY, new=True).name == "2026-10-31"


def test_latest_session(tmp_path):
    assert latest_session(tmp_path) is None
    older = session_dir(tmp_path, date(2026, 10, 24))
    newer = session_dir(tmp_path, DAY)
    os.utime(older, (1_000, 1_000))
    os.utime(newer, (2_000, 2_000))
    assert latest_session(tmp_path) == newer
    os.utime(older, (3_000, 3_000))
    assert latest_session(tmp_path) == older
