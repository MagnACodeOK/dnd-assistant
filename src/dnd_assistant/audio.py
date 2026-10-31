"""Pure audio logic: VAD segmentation, resampling, mono mixdown, and Whisper hallucination filter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import numpy as np

Detector = Callable[[np.ndarray], list[tuple[int, int]]]


@dataclass
class Segment:
    audio: np.ndarray
    start: datetime


class Segmenter:
    """Cuts a stream into utterances: a segment closes after `silence_s` of silence, or at `max_s`."""

    def __init__(self, detect_speech: Detector, sample_rate: int = 16000, silence_s: float = 0.6,
                 max_s: float = 15.0, pad_s: float = 0.2):
        self.detect = detect_speech
        self.rate = sample_rate
        self.silence = int(silence_s * sample_rate)
        self.max = int(max_s * sample_rate)
        self.pad = int(pad_s * sample_rate)
        self.buffer = np.zeros(0, dtype=np.float32)
        self._origin: datetime | None = None  # time of the first sample since the buffer was last empty
        self._offset = 0  # samples between _origin and buffer[0]

    def _time(self, index: int) -> datetime:
        return self._origin + timedelta(seconds=(self._offset + index) / self.rate)

    def _drop(self, n: int) -> None:
        self.buffer = self.buffer[n:]
        self._offset += n

    def _cut(self, begin: int, end: int) -> Segment:
        begin = max(0, begin)
        return Segment(self.buffer[begin:end].copy(), self._time(begin))

    def feed(self, chunk: np.ndarray, chunk_start: datetime) -> list[Segment]:
        if len(self.buffer) == 0:
            self._origin, self._offset = chunk_start, 0
        self.buffer = np.concatenate([self.buffer, chunk.astype(np.float32, copy=False)])
        out: list[Segment] = []
        while True:
            speech = self.detect(self.buffer)
            if not speech:
                if len(self.buffer) > 2 * self.rate:
                    self._drop(len(self.buffer) - self.rate // 2)
                return out
            first, last = speech[0][0], speech[-1][1]
            if len(self.buffer) - first >= self.max:
                out.append(self._cut(first - self.pad, first + self.max))
                self._drop(first + self.max)
            elif len(self.buffer) - last >= self.silence:
                out.append(self._cut(first - self.pad, last + self.pad))
                self._drop(last)
            else:
                return out

    def flush(self) -> list[Segment]:
        out = []
        if len(self.buffer):
            speech = self.detect(self.buffer)
            if speech:
                out.append(self._cut(speech[0][0] - self.pad, len(self.buffer)))
        self.buffer = np.zeros(0, dtype=np.float32)
        self._offset = 0
        return out


def resample(x: np.ndarray, src_rate: int, dst_rate: int = 16000) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if src_rate == dst_rate or len(x) == 0:
        return x
    n = int(round(len(x) * dst_rate / src_rate))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def to_mono(x: np.ndarray, channels: int) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if channels == 1:
        return x
    return x.reshape(-1, channels).mean(axis=1).astype(np.float32)


_HALLUCINATIONS = {"thank you.", "thanks for watching!", "thank you for watching.", "you"}


def is_hallucination(text: str, duration_s: float) -> bool:
    """Whisper's stock phrases on near-silence, and output with no letters at all."""
    t = text.strip()
    if not re.search(r"[^\W\d_]", t):
        return True
    return duration_s < 1.5 and t.lower() in _HALLUCINATIONS


def silero_detector() -> Detector:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    # Small internal padding: the Segmenter adds its own, and Silero's default 400 ms would
    # delay every segment close by that much.
    options = VadOptions(min_silence_duration_ms=600, speech_pad_ms=30)

    def detect(audio: np.ndarray) -> list[tuple[int, int]]:
        return [(t["start"], t["end"]) for t in get_speech_timestamps(audio, options)]

    return detect
