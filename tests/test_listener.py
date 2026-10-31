import queue
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from dnd_assistant.audio import Segment, Segmenter
from dnd_assistant.listener import CaptureWorker, TranscribeWorker, find_device
from dnd_assistant.status import Status

RATE = 16000
T0 = datetime(2026, 10, 31, 21, 0, 0, tzinfo=timezone.utc)


def fake_detect(buf):
    voiced = np.concatenate([[False], buf != 0, [False]])
    edges = np.flatnonzero(np.diff(voiced.astype(np.int8)))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


class FakeSource:
    """Delivers scripted chunks; then nothing (like WASAPI loopback when Discord is quiet)."""

    def __init__(self, items=(), fail_on_open=None):
        self.queue = queue.Queue()
        for item in items:
            self.queue.put(item)
        self.fail_on_open = fail_on_open
        self.opened = 0
        self.closed = 0

    def open(self):
        self.opened += 1
        if self.fail_on_open:
            raise self.fail_on_open

    def check(self):
        pass

    def close(self):
        self.closed += 1


def capture(sources, clock=None, poll_s=0.5, retry_s=0.0):
    it = iter(sources)
    out, events, status = queue.Queue(), [], Status()
    worker = CaptureWorker(lambda: next(it), "Table", Segmenter(fake_detect), out,
                           lambda e: events.append(e) or len(events), status, threading.Event(),
                           now=clock or Clock(), poll_s=poll_s, retry_s=retry_s)
    return worker, out, events, status


def test_watchdog_flushes_sentence_when_source_goes_silent():
    speech = np.ones(RATE, dtype=np.float32)
    start = datetime.now().astimezone() - timedelta(seconds=1)
    worker, out, _, _ = capture([FakeSource([(speech, start)])], clock=lambda: datetime.now().astimezone(), poll_s=0.05)
    worker.start()
    try:
        speaker, segment = out.get(timeout=3)  # 0.6 s of zeros fed in by the watchdog, in 0.05 s steps
    finally:
        worker.stop.set()
        worker.join(timeout=3)
    assert speaker == "Table"
    assert segment.start == start
    assert int((segment.audio != 0).sum()) == RATE


class SpySegmenter(Segmenter):
    def __init__(self):
        super().__init__(fake_detect)
        self.fed = []

    def feed(self, chunk, chunk_start):
        self.fed.append((len(chunk), chunk_start, bool(chunk.any())))
        return super().feed(chunk, chunk_start)


def spy_capture(source, clock):
    out, events, status = queue.Queue(), [], Status()
    seg = SpySegmenter()
    worker = CaptureWorker(lambda: source, "Table", seg, out, lambda e: events.append(e) or 1, status,
                           threading.Event(), now=clock, poll_s=0.01, retry_s=0.0)
    return worker, seg, out


def test_no_silence_inserted_while_source_streams():
    """Chunks arrive every ~0.512 s, a bit slower than the poll: a late chunk must not be
    mistaken for silence (live bug: inserted zeros pushed timestamps minutes ahead)."""
    clock = Clock()
    source = FakeSource()
    worker, seg, _ = spy_capture(source, clock)
    t = T0
    for _ in range(5):
        source.queue.put((np.ones(8192, dtype=np.float32), t))
        worker.step()
        t += timedelta(seconds=8192 / RATE)
        clock.t = t + timedelta(seconds=0.5)  # poll times out before the next chunk
        worker.step()
    assert all(nonzero for _, _, nonzero in seg.fed)
    assert sum(n for n, _, _ in seg.fed) == 5 * 8192


def test_gap_before_chunk_is_filled_so_times_stay_real():
    clock = Clock()
    source = FakeSource([
        (np.ones(RATE, dtype=np.float32), T0),
        (np.zeros(RATE, dtype=np.float32), T0 + timedelta(seconds=1)),
        (np.ones(RATE, dtype=np.float32), T0 + timedelta(seconds=2.5)),
        (np.zeros(RATE, dtype=np.float32), T0 + timedelta(seconds=3.5)),
    ])
    worker, _, out = spy_capture(source, clock)
    for _ in range(4):
        worker.step()
    starts = [s.start for _, s in list(out.queue)]
    assert starts == [T0, T0 + timedelta(seconds=2.5) - timedelta(seconds=0.2)]


def test_long_gap_reanchors_to_real_time():
    clock = Clock()
    source = FakeSource([
        (np.ones(RATE, dtype=np.float32), T0),
        (np.ones(RATE, dtype=np.float32), T0 + timedelta(minutes=5)),
        (np.zeros(RATE, dtype=np.float32), T0 + timedelta(minutes=5, seconds=1)),
    ])
    worker, seg, out = spy_capture(source, clock)
    for _ in range(3):
        worker.step()
    starts = [s.start for _, s in list(out.queue)]
    assert starts == [T0, T0 + timedelta(minutes=5)]
    assert sum(n for n, _, nonzero in seg.fed if not nonzero) < 3 * RATE  # no 5 minutes of zeros


def test_watchdog_fills_only_real_elapsed_time():
    clock = Clock()
    source = FakeSource([(np.ones(RATE, dtype=np.float32), T0)])
    worker, seg, out = spy_capture(source, clock)
    worker.step()
    clock.t = T0 + timedelta(seconds=1.5)  # only 0.5 s since the chunk ended: too soon to call it silence
    worker.step()
    assert len(seg.fed) == 1
    clock.t = T0 + timedelta(seconds=2.5)
    worker.step()
    filled = seg.fed[1]
    assert filled[1] == T0 + timedelta(seconds=1) and not filled[2]
    assert filled[0] == int(1.25 * RATE)  # up to now - 0.25 s
    assert out.qsize() == 1


def test_source_error_emits_gap_and_reopens():
    broken = FakeSource([RuntimeError("device unplugged")])
    healthy = FakeSource([(np.ones(RATE, dtype=np.float32), T0)])
    worker, out, events, status = capture([broken, healthy])
    worker.step()  # opens broken, raises on its first item
    assert [(e.source, e.kind) for e in events] == [("system", "gap")]
    assert "Table audio lost: device unplugged" in events[0].text
    assert broken.closed == 1
    assert "lost-Table" in status.warnings
    worker.step()  # reopens with the healthy source
    assert healthy.opened == 1
    assert "lost-Table" not in status.warnings


def test_open_failure_emits_gap():
    worker, _, events, _ = capture([FakeSource(fail_on_open=OSError("no such device"))])
    worker.step()
    assert events[0].kind == "gap" and "no such device" in events[0].text


def test_silence_warning_after_600s():
    clock = Clock()
    source = FakeSource()
    worker, out, _, status = capture([source], clock=clock, poll_s=0.01)
    worker.step()
    clock.t = T0 + timedelta(seconds=599)
    worker.step()
    assert "silent-Table" not in status.warnings
    clock.t = T0 + timedelta(seconds=601)
    worker.step()
    assert "silent-Table" in status.warnings
    source.queue.put((np.ones(RATE, dtype=np.float32), clock.t))
    source.queue.put((np.zeros(RATE, dtype=np.float32), clock.t + timedelta(seconds=1)))
    worker.step()
    worker.step()
    assert out.qsize() == 1
    assert "silent-Table" not in status.warnings


def test_stop_flushes_pending_speech():
    worker, out, _, _ = capture([FakeSource([(np.ones(RATE, dtype=np.float32), T0)])])
    worker.step()
    worker.stop.set()
    worker.run()  # loop exits at once and flushes
    assert out.qsize() == 1


# --- transcription ---------------------------------------------------------------------------

class FakeTranscriber:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.prompts = []

    def transcribe(self, audio, prompt):
        self.prompts.append(prompt)
        return self.texts.pop(0)


def transcribe_worker(transcriber, keep_dir=None):
    inbox, events, status = queue.Queue(), [], Status()
    worker = TranscribeWorker(transcriber, inbox, lambda e: events.append(e) or len(events), status,
                              lambda: "Names: Ireena.", threading.Event(), keep_dir)
    return worker, inbox, events, status


def segment(seconds, start=T0):
    return Segment(np.ones(int(seconds * RATE), dtype=np.float32), start)


def test_transcribe_worker_skips_hallucination():
    worker, inbox, events, _ = transcribe_worker(FakeTranscriber("Thank you.", "..."))
    inbox.put(("Table", segment(0.8)))
    inbox.put(("Table", segment(3.0)))
    worker.step()
    worker.step()
    assert events == []


def test_transcribe_worker_emits_speech_event_with_segment_start():
    tr = FakeTranscriber(" Ireena is Ismark's sister. ")
    worker, inbox, events, _ = transcribe_worker(tr)
    start = T0 + timedelta(seconds=42)
    inbox.put(("Me", segment(2.0, start)))
    worker.step()
    (e,) = events
    assert (e.source, e.kind, e.speaker, e.text, e.ts) == ("voice", "speech", "Me", "Ireena is Ismark's sister.", start)
    assert tr.prompts == ["Names: Ireena."]


def test_transcribe_worker_keeps_audio_only_when_asked(tmp_path):
    worker, inbox, _, _ = transcribe_worker(FakeTranscriber("Hello there."), keep_dir=tmp_path)
    inbox.put(("Me", segment(2.0)))
    worker.step()
    assert len(list(tmp_path.glob("*.wav"))) == 1


def test_transcribe_worker_backlog_warning():
    worker, inbox, _, status = transcribe_worker(FakeTranscriber(*["Hello there."] * 30))
    for _ in range(30):
        inbox.put(("Table", segment(5.0)))
    worker.step()
    assert "backlog" in status.warnings
    for _ in range(29):
        worker.step()
    assert "backlog" not in status.warnings


def test_transcribe_error_warns_and_continues():
    class Broken:
        def transcribe(self, audio, prompt):
            raise RuntimeError("CUDA out of memory")

    worker, inbox, events, status = transcribe_worker(Broken())
    inbox.put(("Table", segment(2.0)))
    worker.step()
    assert events == [] and "whisper" in status.warnings


# --- PortAudio thread safety -----------------------------------------------------------------

def test_audio_source_serializes_portaudio_calls(monkeypatch):
    """PortAudio crashes natively when two threads open streams at once (seen live)."""
    import pyaudiowpatch

    from dnd_assistant import listener

    held = []

    class Stream:
        def is_active(self):
            return True

        def stop_stream(self):
            held.append(("stop", listener.PORTAUDIO_LOCK.locked()))

        def close(self):
            held.append(("close", listener.PORTAUDIO_LOCK.locked()))

    class PA:
        def __init__(self):
            held.append(("init", listener.PORTAUDIO_LOCK.locked()))

        def get_loopback_device_info_generator(self):
            return iter([{"index": 3, "name": "SteelSeries Sonar - Chat [Loopback]",
                          "maxInputChannels": 2, "defaultSampleRate": 48000}])

        def open(self, **kw):
            held.append(("open", listener.PORTAUDIO_LOCK.locked()))
            return Stream()

        def terminate(self):
            held.append(("terminate", listener.PORTAUDIO_LOCK.locked()))

    monkeypatch.setattr(pyaudiowpatch, "PyAudio", PA)
    com_threads = []
    monkeypatch.setattr(listener, "_init_com", lambda: com_threads.append(threading.get_ident()))
    source = listener.AudioSource("SteelSeries Sonar - Chat", loopback=True)
    opener = threading.Thread(target=source.open)
    opener.start()
    opener.join()
    source.close()
    assert [name for name, _ in held] == ["init", "open", "stop", "close", "terminate"]
    assert all(locked for _, locked in held)
    # WASAPI needs COM on each thread that opens a stream; PortAudio only sets it up on the
    # first thread to initialize it (seen live: "Unanticipated host error" on the second device).
    assert com_threads == [opener.ident]


# --- device lookup ---------------------------------------------------------------------------

class FakePA:
    def __init__(self):
        self.devices = [
            {"index": 0, "name": "Microphone (Arctis Nova Pro)", "maxInputChannels": 2, "hostApi": 1},
            {"index": 1, "name": "SteelSeries Sonar - Microphone (SteelSeries Sonar Virtual Audio Device)", "maxInputChannels": 2, "hostApi": 1},
            {"index": 2, "name": "SteelSeries Sonar - Microphone (SteelSeries Sonar Virtual Audio Device) [Loopback]", "maxInputChannels": 2, "hostApi": 1, "isLoopbackDevice": True},
            {"index": 3, "name": "SteelSeries Sonar - Chat (SteelSeries Sonar Virtual Audio Device) [Loopback]", "maxInputChannels": 2, "hostApi": 1, "isLoopbackDevice": True},
            {"index": 4, "name": "SteelSeries Sonar - Microp", "maxInputChannels": 2, "hostApi": 0},
        ]

    def get_host_api_info_by_type(self, api_type):
        return {"index": 1}

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, i):
        return self.devices[i]

    def get_loopback_device_info_generator(self):
        return (d for d in self.devices if d.get("isLoopbackDevice"))


def test_find_device():
    pa = FakePA()
    assert find_device(pa, "SteelSeries Sonar - Chat", loopback=True)["index"] == 3
    assert find_device(pa, "SteelSeries Sonar - Microphone", loopback=False)["index"] == 1
    with pytest.raises(LookupError):
        find_device(pa, "SteelSeries Sonar - Chat", loopback=False)
