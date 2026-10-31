"""THROWAWAY SPIKE (Task 2): check that a loopback of "Sonar - Chat" captures Discord only.

Not part of the package. Usage:
    uv run python scripts/spikes/record_devices.py list
    uv run python scripts/spikes/record_devices.py record OUT_DIR [--seconds 20]
    uv run python scripts/spikes/record_devices.py sample OUT_WAV [--seconds 15]

`record` writes OUT_DIR/chat.wav (loopback of the Chat device) and OUT_DIR/mic.wav.
`sample` records the mic only and saves it as 16 kHz mono 16-bit (the test fixture).
"""

import argparse
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio

CHAT = "SteelSeries Sonar - Chat"
MIC = "SteelSeries Sonar - Microphone"


def wasapi_index(pa):
    return pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]


def list_devices(pa):
    api = wasapi_index(pa)
    print("WASAPI input devices:")
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d["hostApi"] == api and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
            print(f"  [{i}] {d['name']}  ({d['maxInputChannels']} ch, {int(d['defaultSampleRate'])} Hz)")
    print("Loopback devices:")
    for d in pa.get_loopback_device_info_generator():
        print(f"  [{d['index']}] {d['name']}  ({d['maxInputChannels']} ch, {int(d['defaultSampleRate'])} Hz)")


def find(pa, name, loopback):
    if loopback:
        candidates = list(pa.get_loopback_device_info_generator())
    else:
        api = wasapi_index(pa)
        candidates = [
            d for d in (pa.get_device_info_by_index(i) for i in range(pa.get_device_count()))
            if d["hostApi"] == api and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")
        ]
    for d in candidates:
        if name in d["name"]:
            return d
    raise LookupError(f"no {'loopback' if loopback else 'input'} device containing {name!r}")


class Recorder:
    def __init__(self, pa, dev):
        self.dev = dev
        self.channels = dev["maxInputChannels"]
        self.rate = int(dev["defaultSampleRate"])
        self.chunks = []
        self.callbacks = 0
        self.stream = pa.open(
            format=pyaudio.paInt16, channels=self.channels, rate=self.rate, input=True,
            input_device_index=dev["index"], frames_per_buffer=1024, stream_callback=self._cb,
        )

    def _cb(self, data, frames, info, status):
        self.chunks.append(data)
        self.callbacks += 1
        return (None, pyaudio.paContinue)

    def stop(self):
        self.stream.stop_stream()
        self.stream.close()

    def samples(self):
        return np.frombuffer(b"".join(self.chunks), dtype=np.int16).reshape(-1, self.channels)


def write_wav(path, samples, rate):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(samples.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())


def to_16k_mono(samples, rate):
    mono = samples.astype(np.float32).mean(axis=1)
    n = int(len(mono) * 16000 / rate)
    return np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono).reshape(-1, 1)


def countdown(seconds):
    for left in range(seconds, 0, -1):
        print(f"\r  recording… {left:2d}s left", end="", flush=True)
        time.sleep(1)
    print()


def report(label, rec):
    s = rec.samples()
    secs = len(s) / rec.rate
    peak = int(np.abs(s).max()) if len(s) else 0
    rms = float(np.sqrt((s.astype(np.float64) ** 2).mean())) if len(s) else 0.0
    print(f"{label}: {rec.dev['name']} | {secs:.1f}s captured, {rec.callbacks} callbacks, peak {peak}, rms {rms:.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["list", "record", "sample"])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--seconds", type=int)
    args = ap.parse_args()

    with pyaudio.PyAudio() as pa:
        if args.mode == "list":
            list_devices(pa)
            return
        if not args.out:
            sys.exit("OUT is required")
        out = Path(args.out)
        if args.mode == "record":
            secs = args.seconds or 20
            chat = Recorder(pa, find(pa, CHAT, loopback=True))
            mic = Recorder(pa, find(pa, MIC, loopback=False))
            countdown(secs)
            chat.stop()
            mic.stop()
            report("chat", chat)
            report("mic ", mic)
            write_wav(out / "chat.wav", chat.samples(), chat.rate)
            write_wav(out / "mic.wav", mic.samples(), mic.rate)
            print(f"wrote {out / 'chat.wav'} and {out / 'mic.wav'}")
        else:
            secs = args.seconds or 15
            mic = Recorder(pa, find(pa, MIC, loopback=False))
            countdown(secs)
            mic.stop()
            report("mic", mic)
            write_wav(out, to_16k_mono(mic.samples(), mic.rate), 16000)
            print(f"wrote {out} (16 kHz mono)")


if __name__ == "__main__":
    main()
