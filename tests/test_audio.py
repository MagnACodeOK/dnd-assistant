from datetime import datetime, timedelta, timezone

import numpy as np

from dnd_assistant.audio import Segmenter, is_hallucination, resample, to_mono

RATE = 1000  # small rate keeps the arrays tiny; the logic is rate-independent
T0 = datetime(2026, 10, 31, 21, 0, 0, tzinfo=timezone.utc)


def fake_detect(buf):
    """Speech wherever samples are non-zero, as (start, end) sample ranges."""
    voiced = np.concatenate([[False], buf != 0, [False]])
    edges = np.flatnonzero(np.diff(voiced.astype(np.int8)))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def seg(**kw):
    return Segmenter(fake_detect, sample_rate=RATE, **kw)


def speech(seconds):
    return np.ones(int(seconds * RATE), dtype=np.float32)


def silence(seconds):
    return np.zeros(int(seconds * RATE), dtype=np.float32)


def feed_chunks(s, audio, start=T0, chunk_s=0.1):
    out = []
    n = int(chunk_s * RATE)
    for i in range(0, len(audio), n):
        out += s.feed(audio[i:i + n], start + timedelta(seconds=i / RATE))
    return out


def test_emits_after_silence():
    s = seg()
    audio = np.concatenate([silence(0.5), speech(1.0), silence(0.7)])
    (segment,) = feed_chunks(s, audio)
    # padded by 0.2 s each side
    assert len(segment.audio) == int(1.4 * RATE)


def test_holds_while_speaking():
    s = seg()
    audio = np.concatenate([speech(1.0), silence(0.3), speech(1.0), silence(0.4)])
    assert feed_chunks(s, audio) == []


def test_short_pause_joins_one_segment():
    s = seg()
    audio = np.concatenate([silence(0.5), speech(1.0), silence(0.3), speech(1.0), silence(0.7)])
    (segment,) = feed_chunks(s, audio)
    assert len(segment.audio) == int((0.2 + 1.0 + 0.3 + 1.0 + 0.2) * RATE)


def test_splits_at_max_length():
    s = seg(max_s=3.0)
    segments = feed_chunks(s, np.concatenate([speech(7.0), silence(0.7)]))
    assert len(segments) == 3
    assert all(len(x.audio) <= int(3.2 * RATE) for x in segments)
    total_speech = sum(int((x.audio != 0).sum()) for x in segments)
    assert total_speech == 7 * RATE


def test_segment_start_timestamp():
    s = seg()
    (segment,) = feed_chunks(s, np.concatenate([silence(1.0), speech(1.0), silence(0.7)]))
    assert segment.start == T0 + timedelta(seconds=0.8)  # speech at 1.0 s minus 0.2 s pad


def test_segment_start_after_long_silence_trim():
    s = seg()
    (segment,) = feed_chunks(s, np.concatenate([silence(5.0), speech(1.0), silence(0.7)]))
    assert segment.start == T0 + timedelta(seconds=4.8)


def test_drops_long_silence_buffer():
    s = seg()
    feed_chunks(s, silence(10.0))
    assert len(s.buffer) <= int(2.0 * RATE)


def test_flush_emits_pending():
    s = seg()
    assert feed_chunks(s, np.concatenate([silence(0.3), speech(1.0)])) == []
    (segment,) = s.flush()
    assert int((segment.audio != 0).sum()) == RATE
    assert s.flush() == []


def test_flush_silence_only_emits_nothing():
    s = seg()
    feed_chunks(s, silence(1.0))
    assert s.flush() == []


def test_resample_length_48k_to_16k():
    x = np.random.default_rng(0).standard_normal(48000).astype(np.float32)
    y = resample(x, 48000)
    assert y.dtype == np.float32
    assert len(y) == 16000
    assert np.array_equal(resample(x, 16000), x)


def test_to_mono_averages():
    stereo = np.array([1.0, 3.0, -2.0, 2.0], dtype=np.float32)  # two interleaved frames
    assert to_mono(stereo, 2).tolist() == [2.0, 0.0]
    assert to_mono(stereo, 1).tolist() == stereo.tolist()


def test_hallucination_filter():
    assert is_hallucination("Thank you.", 0.8)
    assert is_hallucination("...", 3.0)
    assert is_hallucination("", 1.0)
    assert is_hallucination("Thanks for watching!", 1.0)
    assert is_hallucination(" you", 0.5)
    assert not is_hallucination("Thank you, Ismark.", 2.0)
    assert not is_hallucination("Thank you.", 2.0)
