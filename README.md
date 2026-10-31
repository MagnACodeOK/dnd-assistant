# D&D Table Assistant

Live assistant for a Curse of Strahd game: transcribes Discord voice with local Whisper, receives Foundry VTT events from a small module, stores everything in SQLite, writes timeline and notes files into the Obsidian vault, and gives Claude Code the `/dnd` and `/dnd-recap` skills.

Design: `docs/superpowers/specs/2026-10-28-dnd-table-assistant-design.md`.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`).
2. In this folder, create the environment and install dependencies:

   ```powershell
   uv venv --python 3.12
   uv sync
   ```

   This also installs the CUDA 12 cuBLAS and cuDNN 9 libraries that faster-whisper needs on the GPU.
3. Copy `config.example.toml` to `config.toml` and set `foundry_origins` to your Foundry URL.
4. Set the user environment variable `LMSTUDIO_API_KEY` to LM Studio's API token (do this yourself; never paste it into chat).
5. Route Discord to the Chat channel: in Windows **Settings → System → Sound → Volume mixer**, set Discord's output device to **SteelSeries Sonar - Chat**, and leave Discord's own Output Device on Default. Discord's dropdown names every Sonar device "SteelSeries Sonar Virtual Audio Device", so it's easy to pick the wrong one there. The table transcript is a loopback of Sonar - Chat, and browser music stays on Sonar - Gaming.
6. Run the tests:

   ```powershell
   uv run pytest
   ```

   GPU tests are skipped by default; run them with `uv run pytest -m gpu`.
