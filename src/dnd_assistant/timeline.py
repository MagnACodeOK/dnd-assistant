"""Render events as one-line timeline entries and Paylon's state as Markdown."""

from __future__ import annotations

import html
import re
import threading
from html.parser import HTMLParser
from pathlib import Path

from .store import Event

_STYLE_TAGS = {"ic": "[IC]", "ooc": "[OOC]", "emote": "[EMOTE]"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def clean_text(text: str) -> str:
    """Strip HTML tags, unescape entities, and collapse all whitespace to single spaces."""
    parser = _TextExtractor()
    parser.feed(text or "")
    parser.close()
    return re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()


def render_line(e: Event) -> str | None:
    t = e.ts.strftime("%H:%M:%S")
    text = clean_text(e.text)
    if e.kind == "speech":
        return f'{t} {e.speaker}: "{text}"'
    if e.kind == "chat":
        tag = _STYLE_TAGS.get(e.data.get("style"), "[OOC]")
        return f'{t} {tag} {e.speaker}: "{text}"'
    if e.kind == "roll":
        d = e.data
        line = f"{t} 🎲 {e.speaker}: {clean_text(str(d.get('label', '')))} {d.get('total')} [{d.get('dice', '')}]"
        if d.get("crit"):
            line += " CRIT"
        if d.get("fumble"):
            line += " FUMBLE"
        return line
    if e.kind == "combat":
        return f"{t} ⚔ {text}"
    if e.kind == "gap":
        return f"{t} ⚠ {text}"
    if e.kind == "note":
        return f"{t} 📝 {text}"
    return None  # state


def render_state(data: dict) -> str:
    hp = data.get("hp") or {}
    hp_line = f"HP {hp.get('value')}/{hp.get('max')}"
    if hp.get("temp"):
        hp_line += f" (+{hp['temp']} temp)"
    resources = ", ".join(f"{name} {r.get('value')}/{r.get('max')}" for name, r in (data.get("resources") or {}).items())
    conditions = ", ".join(data.get("conditions") or []) or "none"
    effects = ", ".join(data.get("effects") or []) or "none"
    return "\n".join([
        f"# {data.get('actor', 'Character')}: now",
        "",
        f"- {hp_line}",
        f"- Resources: {resources or 'none'}",
        f"- Conditions: {conditions}",
        f"- Effects: {effects}",
        "",
    ])


class TimelineWriter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, e: Event) -> None:
        line = render_line(e)
        if line is None:
            return
        with self._lock:
            new = not self.path.exists() or self.path.stat().st_size == 0
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                if new:
                    f.write(f"# Timeline {e.ts.strftime('%Y-%m-%d')}\n\n")
                f.write(line + "\n")
