"""dnd-assistant command line: run, tail, search, devices, token."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config, read_or_create_token
from .session import latest_session
from .store import Store
from .timeline import render_line

REPO = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dnd-assistant", description="Live D&D table assistant.")
    p.add_argument("--config", type=Path, default=REPO / "config.toml")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="capture a live session")
    run.add_argument("--new", action="store_true", help="start a separate session folder for today")
    run.add_argument("--keep-audio", action="store_true", help="save each speech segment as a WAV (debugging)")

    tail = sub.add_parser("tail", help="print the last minutes of the timeline")
    tail.add_argument("--minutes", type=float, default=10)
    tail.add_argument("--session", type=Path)

    search = sub.add_parser("search", help="full-text search the session")
    search.add_argument("terms", nargs="+")
    search.add_argument("--session", type=Path)

    sub.add_parser("devices", help="list audio input and loopback devices")

    token = sub.add_parser("token", help="create the hub token if needed and print its file")
    token.add_argument("--show", action="store_true", help="also print the token itself")
    return p


def _fail(msg: str) -> int:
    print(f"dnd-assistant: {msg}", file=sys.stderr)
    return 2


def _open_session(cfg, session: Path | None) -> Store | None:
    folder = session or latest_session(cfg.campaign)
    if folder is None or not (Path(folder) / "session.db").exists():
        return None
    return Store(Path(folder) / "session.db")


def _print_events(events) -> None:
    for e in events:
        line = render_line(e)
        if line:
            print(line)


def _devices() -> int:
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as pa:
        api = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
        print("Inputs:")
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            if d["hostApi"] == api and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                print(f"  {d['name']}")
        print("Loopback:")
        for d in pa.get_loopback_device_info_generator():
            print(f"  {d['name']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    if args.command == "devices":
        return _devices()
    if not args.config.exists():
        return _fail(f"{args.config} not found. Copy config.example.toml to config.toml and edit it.")
    try:
        cfg = load_config(args.config)
    except (ValueError, TypeError) as exc:
        return _fail(f"bad config: {exc}")

    if args.command == "token":
        token = read_or_create_token(cfg.token_file)
        print(f"Token file: {cfg.token_file}")
        if args.show:
            print(token)
        return 0

    if args.command in ("tail", "search"):
        store = _open_session(cfg, args.session)
        if store is None:
            return _fail("no session found (nothing recorded yet, or wrong --session path)")
        try:
            if args.command == "tail":
                _print_events(store.tail(args.minutes))
            else:
                _print_events(store.search(" ".join(args.terms)))
        finally:
            store.close()
        return 0

    from .runner import run

    return run(cfg, new=args.new, keep_audio=args.keep_audio)


if __name__ == "__main__":
    sys.exit(main())
