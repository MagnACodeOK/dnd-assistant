"""Load config.toml and manage the shared hub token."""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def _default_token_file() -> Path:
    return Path.home() / ".dnd-assistant" / "token"


@dataclass(frozen=True)
class Config:
    campaign: Path
    chat_device: str = "SteelSeries Sonar - Chat"
    mic_device: str = "SteelSeries Sonar - Microphone"
    port: int = 8765
    token_file: Path = field(default_factory=_default_token_file)
    foundry_origins: list[str] = field(default_factory=list)
    whisper_model: str = "large-v3-turbo"
    language: str = "en"
    scribe_url: str = "http://127.0.0.1:1234/v1"
    scribe_model: str = "qwen/qwen3.5-9b"
    scribe_key_env: str = "LMSTUDIO_API_KEY"
    scribe_interval_s: int = 300
    scribe_burst: int = 150
    actor_name: str = "Paylon"


_PATH_FIELDS = {"campaign", "token_file"}


def load_config(path: Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    if not raw.get("campaign"):
        raise ValueError(f"{path}: 'campaign' (the Curse of Strahd folder) is required")
    kwargs = {k: Path(v).expanduser() if k in _PATH_FIELDS else v for k, v in raw.items()}
    return Config(**kwargs)


def read_or_create_token(path: Path) -> str:
    path = Path(path)
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token, encoding="utf-8")
    return token
