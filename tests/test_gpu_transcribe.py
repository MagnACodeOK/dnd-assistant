import wave
from pathlib import Path

import numpy as np
import pytest

from dnd_assistant.glossary import build_prompt

SAMPLE = Path(__file__).parent / "fixtures" / "audio" / "sample.wav"


def load_wav(path):
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1)
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


@pytest.mark.gpu
def test_sample_wav_contains_names():
    from dnd_assistant.listener import Transcriber

    prompt = build_prompt(["Paylon", "Ireena", "Ismark", "Strahd", "Barovia", "Vallaki", "Krezk"])
    text = Transcriber("large-v3-turbo", "en").transcribe(load_wav(SAMPLE), prompt).lower()
    assert "ireena" in text
    assert "barovia" in text
