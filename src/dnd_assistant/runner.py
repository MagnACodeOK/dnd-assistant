"""`dnd-assistant run`: capture one live session until Ctrl+C."""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import time
from datetime import date, datetime

from .audio import Segmenter, silero_detector
from .config import Config, read_or_create_token
from .glossary import build_prompt, collect_names
from .hub import make_app, serve
from .listener import AudioSource, CaptureWorker, Transcriber, TranscribeWorker
from .pipeline import Pipeline
from .scribe import LMStudioClient, NotesFile, Scribe
from .session import session_dir
from .status import Status
from .store import Event, Store
from .timeline import TimelineWriter

GLOSSARY_REFRESH_S = 600
FOUNDRY_QUIET_S = 1200


def _now() -> datetime:
    return datetime.now().astimezone()


def _api_key(env_name: str) -> str | None:
    """The key from this process's environment, or from the user environment in the registry
    (a console opened before the variable was set doesn't see it)."""
    if os.environ.get(env_name):
        return os.environ[env_name]
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                return winreg.QueryValueEx(key, env_name)[0] or None
        except OSError:
            return None
    return None


class SessionRun:
    """Wires the pipeline, hub, listener and scribe together for one session."""

    def __init__(self, cfg: Config, new: bool, keep_audio: bool):
        self.cfg = cfg
        self.token = read_or_create_token(cfg.token_file)
        self.dir = session_dir(cfg.campaign, date.today(), new=new)
        self.status = Status()
        self.store = Store(self.dir / "session.db")
        self.pipeline = Pipeline(self.store, TimelineWriter(self.dir / "timeline.md"),
                                 self.dir / "paylon-now.md", cfg.actor_name, self.status)
        self.notes = NotesFile(self.dir / "session-notes.md")
        self.keep_audio_dir = self.dir / "audio" if keep_audio else None
        self.actor_names: set[str] = set()
        self.last_foundry: float | None = None
        self._prompt = build_prompt(collect_names(cfg.campaign, [cfg.actor_name]))
        self._lock = threading.Lock()

    def handle(self, e: Event) -> int:
        if e.source == "foundry":
            with self._lock:
                self.last_foundry = time.monotonic()
                for name in (e.speaker, e.data.get("actor")):
                    if name:
                        self.actor_names.add(name)
            self.status.clear("foundry")
        return self.pipeline.handle(e)

    def prompt(self) -> str:
        with self._lock:
            return self._prompt

    def refresh_glossary(self) -> None:
        with self._lock:
            extra = [self.cfg.actor_name] + sorted(self.actor_names - {self.cfg.actor_name})
        prompt = build_prompt(collect_names(self.cfg.campaign, extra))
        with self._lock:
            self._prompt = prompt

    def check_foundry(self) -> None:
        with self._lock:
            last = self.last_foundry
        if last is not None and time.monotonic() - last > FOUNDRY_QUIET_S:
            self.status.warn("foundry", "no Foundry events for 20+ min")

    def note(self, text: str) -> None:
        self.pipeline.handle(Event(ts=_now(), source="system", kind="note", speaker=None, text=text))


def run(cfg: Config, new: bool = False, keep_audio: bool = False) -> int:
    s = SessionRun(cfg, new, keep_audio)
    print(f"Session folder: {s.dir}")

    key = _api_key(cfg.scribe_key_env)
    if not key:
        s.status.warn("scribe-key", f"{cfg.scribe_key_env} not set; scribe will get 401s")
    scribe = Scribe(s.store, s.notes, LMStudioClient(cfg.scribe_url, cfg.scribe_model, key), s.status,
                    interval_s=cfg.scribe_interval_s, burst=cfg.scribe_burst)

    print("Loading Whisper…", flush=True)
    transcriber = Transcriber(cfg.whisper_model, cfg.language)

    capture_stop = threading.Event()
    scribe_stop = threading.Event()
    inbox: queue.Queue = queue.Queue()
    captures = [
        CaptureWorker(lambda: AudioSource(cfg.chat_device, loopback=True), "Table",
                      Segmenter(silero_detector()), inbox, s.handle, s.status, capture_stop),
        CaptureWorker(lambda: AudioSource(cfg.mic_device, loopback=False), "Me",
                      Segmenter(silero_detector()), inbox, s.handle, s.status, capture_stop),
    ]
    transcribe = TranscribeWorker(transcriber, inbox, s.handle, s.status, s.prompt, capture_stop, s.keep_audio_dir)
    scribe_thread = threading.Thread(target=scribe.loop, args=(scribe_stop,), name="scribe", daemon=True)

    async def main() -> None:
        try:
            runner = await serve(make_app(s.handle, s.token, cfg.foundry_origins), cfg.port)
        except OSError as exc:
            raise SystemExit(f"dnd-assistant: can't listen on 127.0.0.1:{cfg.port}: {exc}")
        s.note("Session started")
        for t in (*captures, transcribe, scribe_thread):
            t.start()
        print(f"Hub on http://127.0.0.1:{cfg.port}. Ctrl+C ends the session.", flush=True)
        last_glossary = time.monotonic()
        try:
            while True:
                await asyncio.sleep(2)
                if time.monotonic() - last_glossary > GLOSSARY_REFRESH_S:
                    s.refresh_glossary()
                    last_glossary = time.monotonic()
                s.check_foundry()
                sys.stdout.write("\r" + s.status.line().ljust(120))
                sys.stdout.flush()
        finally:
            await runner.cleanup()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    print("\nEnding session: finishing transcription and notes…", flush=True)
    capture_stop.set()
    for t in captures:
        t.join(timeout=10)
    transcribe.join(timeout=120)
    s.note("Session ended")
    scribe_stop.set()
    scribe_thread.join(timeout=300)
    s.store.close()
    print(s.status.line())
    print(f"Done. Files are in {s.dir}")
    return 0
