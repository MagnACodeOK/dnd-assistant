"""The scribe: a local model turns new events into facts, and code merges them into session-notes.md.

The model never rewrites the notes file, so earlier facts can't be lost. Any scribe failure is a
skipped run with a status warning; capture never waits on it.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import httpx

from .status import Status
from .store import Store
from .timeline import clean_text, render_line

SECTIONS: tuple[str, ...] = (
    "People", "Places", "Clues & Lore", "Quests & Promises", "Loot & Rewards", "Party Decisions",
)
META_LAST_ID = "scribe_last_id"
MAX_ATTEMPTS = 2

SYSTEM_PROMPT = "\n".join([
    "You keep running notes for a Curse of Strahd D&D session from a transcript of table voice and Foundry VTT events.",
    "Return JSON: a list of facts, each with section, subject, fact, time and certain.",
    f"Sections: {', '.join(SECTIONS)}.",
    "Rules:",
    "- Record only facts stated in the events.",
    "- Give each fact the HH:MM of its source event.",
    "- Do not infer motives.",
    "- Mark facts you are unsure of with certain=false.",
    "- Skip facts already listed under Existing subjects unless something new was said.",
])

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": list(SECTIONS)},
                    "subject": {"type": "string"},
                    "fact": {"type": "string"},
                    "time": {"type": "string"},
                    "certain": {"type": "boolean"},
                },
                "required": ["section", "subject", "fact", "time", "certain"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


class ScribeError(Exception):
    """The model call failed (unreachable, HTTP error, timeout)."""


class ScribeInvalidOutput(ScribeError):
    """The model answered, but not with the expected JSON. Worth one retry."""


@dataclass(frozen=True)
class Fact:
    section: str
    subject: str
    fact: str
    time: str
    certain: bool


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> dict: ...


class LMStudioClient:
    def __init__(self, base_url: str, model: str, api_key: str | None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.transport = transport

    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "facts", "strict": True, "schema": schema}},
            "temperature": 0.2,
            # Fact extraction needs no hidden reasoning; with it on, Qwen 3.5 spent 6-14k tokens
            # (86-184 s) thinking per batch and sometimes ran out of context before answering.
            "reasoning_effort": "none",
        }
        try:
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                resp = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ScribeError(f"LM Studio unreachable: {exc}") from exc
        if resp.status_code == 401:
            raise ScribeError("LM Studio 401: is LMSTUDIO_API_KEY set?")
        if resp.status_code >= 400:
            raise ScribeError(f"LM Studio HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ScribeInvalidOutput(f"LM Studio returned invalid JSON: {exc}") from exc


def parse_facts(obj: dict) -> list[Fact]:
    if not isinstance(obj, dict) or not isinstance(obj.get("facts"), list):
        raise ScribeInvalidOutput("expected an object with a 'facts' list")
    facts = []
    for item in obj["facts"]:
        if not isinstance(item, dict) or item.get("section") not in SECTIONS:
            continue
        subject, text, time = (clean_text(str(item.get(k) or "")) for k in ("subject", "fact", "time"))
        if not (subject and text and time):
            continue
        facts.append(Fact(item["section"], subject, text, time, item.get("certain") is not False))
    return facts


_BULLET = re.compile(r"^- \*\*(?P<subject>.+?)\*\*: (?P<fact>.*?) \((?P<time>[^()]*)\)(?P<unsure> \(\?\))?$")


def _key(f: Fact) -> tuple[str, str, str]:
    norm = lambda s: " ".join(s.split()).casefold()
    return (f.section, norm(f.subject), norm(f.fact))


class NotesFile:
    """session-notes.md, owned by code: facts are only ever appended under their section."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._facts: dict[str, list[Fact]] = {s: [] for s in SECTIONS}
        self._seen: set[tuple[str, str, str]] = set()
        if self.path.exists():
            self._parse(self.path.read_text(encoding="utf-8"))

    def _parse(self, text: str) -> None:
        section = None
        for line in text.splitlines():
            if line.startswith("## "):
                section = line[3:].strip() if line[3:].strip() in SECTIONS else None
                continue
            m = _BULLET.match(line)
            if section and m:
                f = Fact(section, m["subject"], m["fact"], m["time"], not m["unsure"])
                self._facts[section].append(f)
                self._seen.add(_key(f))

    def subjects(self) -> list[str]:
        out: list[str] = []
        for section in SECTIONS:
            for f in self._facts[section]:
                s = f"{section} / {f.subject}"
                if s not in out:
                    out.append(s)
        return out

    def merge(self, facts: list[Fact]) -> int:
        added = 0
        for f in facts:
            f = Fact(f.section, clean_text(f.subject).replace("**", ""), clean_text(f.fact), clean_text(f.time), f.certain)
            if f.section not in SECTIONS or _key(f) in self._seen:
                continue
            self._seen.add(_key(f))
            self._facts[f.section].append(f)
            added += 1
        if added:
            self._write()
        return added

    def _write(self) -> None:
        lines = ["# Session notes", ""]
        for section in SECTIONS:
            lines += [f"## {section}", ""]
            for f in self._facts[section]:
                lines.append(f"- **{f.subject}**: {f.fact} ({f.time})" + ("" if f.certain else " (?)"))
            if self._facts[section]:
                lines.append("")
        self.path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


class Scribe:
    def __init__(self, store: Store, notes: NotesFile, client: LLMClient, status: Status,
                 interval_s: int = 300, burst: int = 150, timeout: float = 120.0,
                 now: Callable[[], datetime] = lambda: datetime.now().astimezone()):
        self.store = store
        self.notes = notes
        self.client = client
        self.status = status
        self.interval_s = interval_s
        self.burst = burst
        self.timeout = timeout
        self.now = now
        self._last_run: datetime | None = None
        self._last_failed = False

    def _last_id(self) -> int:
        return int(self.store.get_meta(META_LAST_ID) or 0)

    def due(self, now: datetime) -> bool:
        if self._last_run is None:
            self._last_run = now
        pending = len(self.store.since(self._last_id()))
        if pending == 0:
            return False
        if pending >= self.burst and not self._last_failed:
            return True
        return (now - self._last_run).total_seconds() >= self.interval_s

    def run_once(self, now: datetime) -> bool:
        self._last_run = now
        try:
            ok = self._run(now)
        except Exception as exc:  # never let the scribe take down capture
            self.status.warn("scribe", f"scribe skipped: {exc}")
            ok = False
        self._last_failed = not ok
        return ok

    def _run(self, now: datetime) -> bool:
        events = self.store.since(self._last_id())
        if not events:
            return True
        lines = [line for line in (render_line(e) for e in events) if line]
        if lines:
            subjects = self.notes.subjects()
            user = "\n".join([
                "Existing subjects:",
                *(f"- {s}" for s in subjects or ["(none)"]),
                "",
                "New events:",
                *lines,
            ])
            facts = None
            for attempt in range(MAX_ATTEMPTS):
                try:
                    facts = parse_facts(self.client.complete_json(SYSTEM_PROMPT, user, FACTS_SCHEMA, self.timeout))
                    break
                except ScribeInvalidOutput as exc:
                    if attempt == MAX_ATTEMPTS - 1:
                        self.status.warn("scribe", f"scribe skipped: {exc}")
                        return False
                except ScribeError as exc:
                    self.status.warn("scribe", f"scribe skipped: {exc}")
                    return False
            self.notes.merge(facts)
            self.status.notes_at = now
        self.store.set_meta(META_LAST_ID, str(events[-1].id))
        self.status.clear("scribe")
        return True

    def loop(self, stop: threading.Event) -> None:
        while not stop.wait(5):
            now = self.now()
            if self.due(now):
                self.run_once(now)
        self.run_once(self.now())  # final pass over anything unprocessed
