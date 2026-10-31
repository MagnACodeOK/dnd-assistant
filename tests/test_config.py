import pytest

from dnd_assistant.config import load_config, read_or_create_token


def test_load_config_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('campaign = "X"\nfoundry_origins = ["https://f.example"]\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.port == 8765
    assert cfg.chat_device == "SteelSeries Sonar - Chat"
    assert cfg.foundry_origins == ["https://f.example"]


def test_load_config_requires_campaign(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('foundry_origins = ["https://f.example"]\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(p)


def test_token_created_once(tmp_path):
    path = tmp_path / "nested" / "token"
    first = read_or_create_token(path)
    assert path.exists()
    assert len(first) >= 32
    assert all(c.isalnum() or c in "-_" for c in first)
    assert read_or_create_token(path) == first
