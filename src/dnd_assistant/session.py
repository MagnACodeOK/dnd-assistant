"""Live session folders: Curse of Strahd/Live Sessions/YYYY-MM-DD[-N]."""

from __future__ import annotations

from datetime import date
from pathlib import Path

LIVE = "Live Sessions"


def session_dir(campaign: Path, day: date, new: bool = False) -> Path:
    """The session folder for `day`. The same day continues the same folder (restart after a
    crash); `new` starts a separate one with the first free suffix (-2, -3, ...)."""
    root = Path(campaign) / LIVE
    path = root / day.isoformat()
    if new and path.exists():
        n = 2
        while (root / f"{day.isoformat()}-{n}").exists():
            n += 1
        path = root / f"{day.isoformat()}-{n}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_session(campaign: Path) -> Path | None:
    root = Path(campaign) / LIVE
    if not root.is_dir():
        return None
    dirs = [d for d in root.iterdir() if d.is_dir()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None
