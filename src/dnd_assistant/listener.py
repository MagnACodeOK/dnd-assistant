"""Audio capture (WASAPI), segmentation, and Whisper transcription threads."""

from __future__ import annotations

import os
import queue
import threading
import wave
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import Segment, Segmenter, is_hallucination, resample, to_mono
from .status import Status
from .store import Event

RATE = 16000
SILENT_WARN_S = 600
# PortAudio crashes natively when two threads initialize or open streams at the same time,
# so every PortAudio setup/teardown call goes through this lock.
PORTAUDIO_LOCK = threading.Lock()
Handle = Callable[[Event], int]


def _now() -> datetime:
    return datetime.now().astimezone()


def _init_com() -> None:
    """WASAPI needs COM on every thread that opens a stream, but PortAudio initializes it only
    on the first thread to call Pa_Initialize. Safe to call repeatedly."""
    if os.name == "nt":
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED


def find_device(pa, name: str, loopback: bool) -> dict:
    """First WASAPI device whose name contains `name`: a loopback entry, or a real input."""
    if loopback:
        candidates = list(pa.get_loopback_device_info_generator())
    else:
        import pyaudiowpatch as pyaudio

        api = pa.get_host_api_info_by_type(getattr(pyaudio, "paWASAPI", 13))["index"]
        candidates = [
            d for d in (pa.get_device_info_by_index(i) for i in range(pa.get_device_count()))
            if d["hostApi"] == api and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")
        ]
    for d in candidates:
        if name in d["name"]:
            return d
    kind = "loopback" if loopback else "input"
    raise LookupError(f"no {kind} device containing {name!r}; run `dnd-assistant devices`")


class AudioSource:
    """A WASAPI capture stream that pushes (float32 mono 16 kHz chunk, start time) onto `queue`.
    Chunks are about `chunk_s` long, so VAD runs a few times a second rather than per callback.
    A loopback stream delivers nothing while its device is silent."""

    def __init__(self, device_name: str, loopback: bool, chunk_s: float = 0.5):
        self.device_name = device_name
        self.loopback = loopback
        self.chunk_s = chunk_s
        self.queue: queue.Queue = queue.Queue()
        self._pa = None
        self._stream = None

    def open(self) -> None:
        import pyaudiowpatch as pyaudio

        _init_com()
        with PORTAUDIO_LOCK:
            self._pa = pyaudio.PyAudio()
            try:
                self._open_stream(pyaudio)
            except Exception:
                self._pa.terminate()
                self._pa = None
                raise

    def _open_stream(self, pyaudio) -> None:
        dev = find_device(self._pa, self.device_name, self.loopback)
        channels, rate = dev["maxInputChannels"], int(dev["defaultSampleRate"])
        frames_wanted = int(rate * self.chunk_s)
        pending: list[bytes] = []
        pending_frames = 0
        chunk_start: datetime | None = None

        def callback(data, frame_count, time_info, status_flags):
            nonlocal pending_frames, chunk_start
            if chunk_start is None:
                chunk_start = _now() - timedelta(seconds=frame_count / rate)
            pending.append(data)
            pending_frames += frame_count
            if pending_frames >= frames_wanted:
                pcm = np.frombuffer(b"".join(pending), dtype=np.int16).astype(np.float32) / 32768.0
                self.queue.put((resample(to_mono(pcm, channels), rate, RATE), chunk_start))
                pending.clear()
                pending_frames = 0
                chunk_start = None
            return (None, pyaudio.paContinue)

        self._stream = self._pa.open(
            format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
            input_device_index=dev["index"], frames_per_buffer=1024, stream_callback=callback,
        )

    def check(self) -> None:
        if self._stream is not None and not self._stream.is_active():
            raise RuntimeError("stream stopped")

    def close(self) -> None:
        with PORTAUDIO_LOCK:
            for action in (lambda: self._stream.stop_stream(), lambda: self._stream.close(), lambda: self._pa.terminate()):
                try:
                    action()
                except Exception:
                    pass
            self._stream = self._pa = None


class CaptureWorker(threading.Thread):
    def __init__(self, source_factory: Callable[[], AudioSource], speaker: str, segmenter: Segmenter,
                 out: queue.Queue, handle: Handle, status: Status, stop: threading.Event,
                 now: Callable[[], datetime] = _now, poll_s: float = 0.5, retry_s: float = 5.0):
        super().__init__(name=f"capture-{speaker}", daemon=True)
        self.source_factory = source_factory
        self.speaker = speaker
        self.segmenter = segmenter
        self.out = out
        self.handle = handle
        self.status = status
        self.stop = stop
        self.now = now
        self.poll_s = poll_s
        self.retry_s = retry_s
        self.source = None
        self._last_speech: datetime | None = None
        self._next_ts: datetime | None = None

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                self.step()
        finally:
            for segment in self.segmenter.flush():
                self.out.put((self.speaker, segment))
            if self.source is not None:
                self.source.close()

    def step(self) -> None:
        try:
            if self.source is None:
                self.source = self.source_factory()
                self.source.open()
                self.status.clear(f"lost-{self.speaker}")
                if self._last_speech is None:
                    self._last_speech = self.now()
            try:
                item = self.source.queue.get(timeout=self.poll_s)
            except queue.Empty:
                # Silence watchdog: loopback sends nothing when Discord goes quiet, so feed zeros
                # to let a sentence that was mid-segment close after the usual pause.
                self.source.check()
                item = (np.zeros(int(self.poll_s * RATE), dtype=np.float32), self._next_ts or self.now())
            if isinstance(item, Exception):
                raise item
            chunk, ts = item
            self._next_ts = ts + timedelta(seconds=len(chunk) / RATE)
            segments = self.segmenter.feed(chunk, ts)
        except Exception as err:
            self._lost(err)
            return
        now = self.now()
        if segments:
            self._last_speech = now
            self.status.clear(f"silent-{self.speaker}")
            for segment in segments:
                self.out.put((self.speaker, segment))
        elif self._last_speech and (now - self._last_speech).total_seconds() > SILENT_WARN_S:
            self.status.warn(f"silent-{self.speaker}", f"{self.speaker} silent for 10+ min")

    def _lost(self, err: Exception) -> None:
        self.handle(Event(ts=self.now(), source="system", kind="gap", speaker=self.speaker,
                          text=f"{self.speaker} audio lost: {err}"))
        self.status.warn(f"lost-{self.speaker}", f"{self.speaker} audio lost, retrying")
        if self.source is not None:
            try:
                self.source.close()
            except Exception:
                pass
        self.source = None
        self._next_ts = None
        self.stop.wait(self.retry_s)


def _add_cuda_dll_dirs() -> None:
    """pip's CUDA 12 cuBLAS/cuDNN wheels aren't on the Windows DLL search path by default."""
    if os.name != "nt":
        return
    try:
        import nvidia.cublas
        import nvidia.cudnn
    except ImportError:
        return
    for pkg in (nvidia.cublas, nvidia.cudnn):
        bin_dir = os.path.join(pkg.__path__[0], "bin")
        if os.path.isdir(bin_dir):
            os.add_dll_directory(bin_dir)
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


class Transcriber:
    def __init__(self, model: str, language: str):
        _add_cuda_dll_dirs()
        from faster_whisper import WhisperModel

        self.language = language
        self.model = WhisperModel(model, device="cuda", compute_type="float16")

    def transcribe(self, audio: np.ndarray, prompt: str) -> str:
        # Always pass arrays: file decoding goes through PyAV, which is incompatible here.
        segments, _ = self.model.transcribe(
            audio, language=self.language, initial_prompt=prompt, vad_filter=False,
            condition_on_previous_text=False, beam_size=5,
        )
        return " ".join(s.text.strip() for s in segments).strip()


class TranscribeWorker(threading.Thread):
    def __init__(self, transcriber, inbox: queue.Queue, handle: Handle, status: Status,
                 prompt: Callable[[], str], stop: threading.Event, keep_audio_dir: Path | None):
        super().__init__(name="transcribe", daemon=True)
        self.transcriber = transcriber
        self.inbox = inbox
        self.handle = handle
        self.status = status
        self.prompt = prompt
        self.stop = stop
        self.keep_audio_dir = Path(keep_audio_dir) if keep_audio_dir else None
        self._avg_s = 0.0
        self._count = 0

    def run(self) -> None:
        # After stop, finish what the capture workers flushed.
        while not (self.stop.is_set() and self.inbox.empty()):
            self.step()

    def step(self) -> None:
        try:
            speaker, segment = self.inbox.get(timeout=0.5)
        except queue.Empty:
            return
        duration = len(segment.audio) / RATE
        self._count += 1
        self._avg_s += (duration - self._avg_s) / self._count
        if self.inbox.qsize() * self._avg_s > 60:
            self.status.warn("backlog", f"transcription {int(self.inbox.qsize() * self._avg_s)} s behind")
        else:
            self.status.clear("backlog")
        if self.keep_audio_dir:
            self._save(speaker, segment)
        try:
            text = self.transcriber.transcribe(segment.audio, self.prompt())
        except Exception as err:
            self.status.warn("whisper", f"Whisper error: {err}")
            return
        self.status.clear("whisper")
        if is_hallucination(text, duration):
            return
        self.handle(Event(ts=segment.start, source="voice", kind="speech", speaker=speaker, text=text.strip()))

    def _save(self, speaker: str, segment: Segment) -> None:
        self.keep_audio_dir.mkdir(parents=True, exist_ok=True)
        path = self.keep_audio_dir / f"{segment.start:%H%M%S-%f}-{speaker}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((np.clip(segment.audio, -1, 1) * 32767).astype(np.int16).tobytes())
