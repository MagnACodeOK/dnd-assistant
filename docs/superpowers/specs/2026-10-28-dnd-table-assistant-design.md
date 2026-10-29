# D&D Table Assistant: Design

- **Date:** 2026-10-28
- **Status:** Draft, awaiting user review
- **Owner:** Wayne Hancock

## 1. Purpose

A live assistant for the user's *Curse of Strahd* game (D&D 2024 rules), where the user plays **Paylon** (Pugilist 6, grappler). The game runs on **Foundry VTT** with voice on **Discord**. The system captures what is said and what happens in Foundry during a session (about 4 hours), and makes it available to Claude.

Goals, in priority order:

- **A. My turn:** answer rules and tactics questions about Paylon in the moment, using Paylon's current state and the last few minutes of play.
- **B. Story memory:** keep running notes (NPCs, places, clues, promises, loot) so that questions like "what was that innkeeper's name?" can be answered.
- **D. After the session:** write a recap into the campaign's Obsidian vault.

Out of scope: combat analytics (enemy HP tracking, save statistics), automatic or proactive suggestions, per-player speaker identification, and any use of the Claude API. Version 1 runs entirely on the user's Claude subscription, through Claude Code. An API-key "push" mode may come later, and the design keeps that door open.

## 2. Constraints and decisions

| Topic | Decision | Why |
|---|---|---|
| Where it runs | Entirely on the gaming desktop (RTX 4080 16 GB, 64 GB RAM, Windows 11) | The GPU is mostly idle during Foundry and Discord sessions. This avoids the Proxmox box, whose boot SSD is failing, and avoids network setup. |
| Speech to text | faster-whisper, large-v3-turbo, on the 4080 | Free, local, near real time |
| Table audio source | WASAPI loopback of the **"SteelSeries Sonar - Chat"** playback device | Discord outputs to Chat; the browser (Foundry music) and the Windows default go to "Sonar - Gaming", so music is never captured |
| User's voice | Capture from **"SteelSeries Sonar - Microphone"** | It's the same processed mic that Discord hears. Separate streams give "Me" and "Table" labels. The user wears a headset, so the mic doesn't pick up the table. |
| Foundry data | A custom Foundry module running in the user's browser and posting to localhost | The user has admin rights on the Foundry setup and can install modules |
| Foundry versions | Foundry **v14 build 368**, dnd5e system **6.0.6** | Newer than Claude's detailed knowledge, so the event data structures are verified by a test before anything is built on them (section 8) |
| Module install | Manifest URL hosted on a public GitHub repo | The user's choice (easiest install and updates) |
| Store | SQLite, plus Markdown files written into the vault | One queryable timeline, with human-readable output |
| Local model ("scribe") | `qwen/qwen3.5-9b`, already downloaded in LM Studio (OpenAI-compatible API on `localhost:1234`). LM Studio's API token is enabled, so the scribe sends `Authorization: Bearer` using the user environment variable `LMSTUDIO_API_KEY`. | Already installed, so nothing to download. It fits in video memory alongside Whisper with room to spare, and it's better than the 4B model on the 2060. |
| Claude | Claude Code on the subscription, via skills `/dnd` and `/dnd-recap` | No extra cost. Measure real usage before considering the API. |
| Code location | `D:\Claude\dnd-assistant` | The user's default working folder |

Estimated video memory use: Foundry, Discord and the browser about 2.5 GB; Whisper about 2–3 GB; Qwen3.5-9B with a 16K context about 7–8 GB. Total about 11–12 GB of 16 GB.

## 3. Architecture

```
"Sonar - Chat" (Discord) ─┐
"Sonar - Microphone" ─────┴─► Listener (faster-whisper) ─┐
                                                         ├─► Hub (SQLite) ─┬─► timeline.md
Foundry tab ─► dnd-assistant module ─► 127.0.0.1 ────────┘                 ├─► paylon-now.md
                                                                           └─► Scribe (LM Studio) ─► session-notes.md

Claude Code: /dnd reads those files and answers. /dnd-recap writes the session recap.
```

The listener, hub and scribe run as **one Python process** (`dnd-assistant`), started by the D&D-mode launcher. The Foundry module is a separate JavaScript package. The Claude Code skills are Markdown files.

## 4. Components

### 4.1 Listener (voice to text)

- **Two capture streams,** using pyaudiowpatch (WASAPI):
  - loopback of "SteelSeries Sonar - Chat", labeled `Table`
  - capture of "SteelSeries Sonar - Microphone", labeled `Me`
  - Devices are found by name, and the device names are set in `config.toml`.
- **Splitting the audio:** each stream goes through the voice detector built into faster-whisper (Silero VAD). A segment closes after about 0.6 s of silence, or at a hard limit of 15 s.
- **Transcription:** large-v3-turbo on CUDA, fp16. Both streams share one model instance, through a single queue that preserves order.
- **Name hints:** the `initial_prompt` is built from `glossary.md` (section 4.6), so campaign names are spelled correctly more often.
- **Output:** one event per segment: `kind=speech`, with the speaker, the text, and the segment start time.
- **Silence:** WASAPI loopback delivers no data when nothing is playing. This is treated as silence, not as an error.
- **Device problems:** if a device disappears or errors, the listener logs a `gap` event, retries every 5 s, and resumes. A device that stays silent for more than 10 minutes during a session produces a status warning, not a crash.
- **Privacy:** audio is held in memory only and discarded once transcribed. The `--keep-audio` flag saves WAV segments for debugging. The user tells the table that they are transcribing.

### 4.2 Foundry module (`dnd-assistant`)

- **Packaging:** a Foundry v14 module, installed through its manifest URL from GitHub releases. It contains no secrets.
- **Activation:** a client setting **"Send to my PC"** (off by default), plus client settings for the port and the shared token. The module does nothing in any client where the setting is off, so other players' browsers are unaffected.
- **What it captures:** only what the user's **player** client receives. It never asks for GM-only data. Blind rolls, whispers between other people, and monster stat blocks are excluded by design.
- **Events sent:**
  - **chat:** new chat messages, with the speaker alias, an in-character/out-of-character/emote flag, and plain text (HTML stripped).
  - **roll:** rolls attached to chat messages: who rolled, formula, total, dice results, and the roll type and label (attack, save, check or skill, damage, initiative) where the dnd5e system provides them, plus critical or fumble markers.
  - **combat:** combat start and end, and each round or turn change (whose turn it is).
  - **state:** for the actor Paylon (the user's assigned character), a full snapshot when the module connects, and changes to HP, temporary HP, resources, conditions and active effects.
- **Transport:** `POST http://127.0.0.1:<port>/event` with a JSON body and an `X-Token` header. The browser asks once for permission to reach the local network, and the user allows it.
- **When the hub is down:** events are queued in memory (capped at 500), retried with backoff, and a single notice is shown. The events are sent once the hub comes back.
- **Exact hook and flag names** are settled by the first test in section 8 (spike 2). The design depends only on the event types above, not on Foundry's internal field names.

### 4.3 Hub

- **Web server** on `127.0.0.1` only, port from `config.toml`. It accepts `/event` only with the token. CORS preflight is answered only for the configured Foundry origin(s), including the `Access-Control-Allow-Private-Network: true` header that browsers require for localhost. Each event's time is the time the hub received it; any timestamp Foundry sends is kept in its data.
- **Database:** SQLite in WAL mode (readers don't block the writer), at `<session folder>/session.db`.
  - `events(id, ts, source, kind, speaker, text, data_json)`, where `source` is `voice`, `foundry` or `system`, and `kind` is `speech`, `chat`, `roll`, `combat`, `state`, `gap` or `note`.
  - `meta(key, value)`, which holds the session start time and the scribe's last processed event id.
- **Files it writes** into the session folder (section 4.5):
  - `timeline.md`: appended per event, one line each, for example:
    - `21:14:05 Table: "The door creaks open…"`
    - `21:14:12 [IC] Ismark: "You must help my sister."`
    - `21:14:20 🎲 Paylon: Athletics (grapple) 19 [d20=15]`
    - `21:15:02 ⚔ Round 3: Paylon's turn`
  - `paylon-now.md`: rewritten whenever a `state` event arrives (HP, resources, conditions, active effects).
- **Status console:** a one-line live status, for example `🎙 312 lines · 🎲 48 Foundry events · ✍ notes 21:40`, with warnings for a silent device, no Foundry events for 20 or more minutes during a session, or the scribe failing.

### 4.4 Scribe (local model)

- **Schedule:** runs every 5 minutes, and immediately when 150 or more new events have arrived.
- **Input:** all events since the last successful run, rendered like `timeline.md`, plus the current list of fact titles so the model can avoid duplicates. It does not get the full notes body.
- **Call:** LM Studio's OpenAI-compatible API, model `qwen/qwen3.5-9b`, with a JSON schema for the response (structured output). If the installed version of LM Studio doesn't support that, it falls back to a JSON-only instruction plus validation.
- **Output:** a list of facts, each with `{section, subject, fact, time, certain: bool}`. The sections are fixed: **People, Places, Clues & Lore, Quests & Promises, Loot & Rewards, Party Decisions**.
- **Merging is done by code, not by the model.** A new fact is appended under its section and subject. An exact duplicate is skipped. Uncertain facts get "(?)" appended. The model never rewrites `session-notes.md`, so earlier facts can't be lost.
- **Rules given to the model:** record only what was stated in the events, give each fact the time of its source event, and don't infer motives.
- **Failure:** if LM Studio is unreachable, returns invalid JSON twice, or takes longer than 120 s, the scribe skips the run, leaves the last processed event id unchanged, and catches up on the next run. Capture never waits on the scribe.
- **Shutdown:** on Ctrl+C, the scribe makes one final pass over any unprocessed events, then the process exits.

### 4.5 Vault layout

```
Curse of Strahd/
  Live Sessions/YYYY-MM-DD/      ← written by the hub (raw)
    session.db
    timeline.md
    paylon-now.md
    session-notes.md
  Session Notes/YYYY-MM-DD - <Title>.md   ← written by /dnd-recap (existing naming convention)
  glossary.md                    ← Whisper name hints
```

`Live Sessions/` is kept separate from `Session Notes/`, so the curated notes stay clean. The hub creates the date folder when a session starts. If that date's folder already exists (for example, after a crash), the session continues in it. `dnd-assistant run --new` starts a separate folder with a suffix (`-2`) instead.

### 4.6 Glossary

`Curse of Strahd/glossary.md` is a plain list of names and terms, one per line. When a session starts, the hub combines it with the file names in `NPCs/` and `Places/`, and the actor names it receives from Foundry (party members belong in `glossary.md`). The result becomes Whisper's `initial_prompt`, which is capped at about 200 tokens (800 characters), in this order: Foundry actor names first, then glossary lines, then NPC and place names. It is rebuilt every 10 minutes. `/dnd-recap` adds new names to the file.

### 4.7 Claude Code skills (user-level, `~/.claude/skills/`)

**`/dnd <question>`** handles goal A.

- **Reads:**
  - `My Character/Paylon cheat sheet.md`
  - Paylon's entry in `Party.md` (build and background; there is no separate character note in the campaign folder)
  - the "GM rulings" in `Curse of Strahd.md`
  - `paylon-now.md`
  - `session-notes.md`
  - the last 10 minutes of the timeline, printed by `dnd-assistant tail --minutes 10`
- **Older events:** it searches with `dnd-assistant search "<terms>"` (a full-text search in SQLite) or with grep on `timeline.md`.
- **Answer format:** the best option first, at most about 3 bullets, and the rule or feature named for each rules call. GM rulings override the rules as written. If a ruling is uncertain, it says "ask the DM" instead of guessing.
- **`/dnd ask`** suggests questions Paylon might ask, with an **in-character knowledge filter**:
  - It uses only in-character events Paylon was present for: speech, in-character chat, and Paylon's own rolls. Out-of-character table talk and rules discussion are excluded.
  - It also uses knowledge plausible from Paylon's background and skills, taken from `Party.md` and the cheat sheet.
  - Each suggestion is phrased as Paylon speaking to an NPC or the party, with one line on why Paylon would ask it.
  - Voice lines with no in-character or out-of-character marker are judged from context, and borderline ones are flagged.
- **How it's used:** one Claude Code session per game night, started fresh.

**`/dnd-recap`** handles goal D.

- **Reads:** `session-notes.md` and the full `timeline.md` for the session folder, which defaults to the latest one.
- **Writes** `Session Notes/YYYY-MM-DD - <Title>.md`, matching the style of the existing session notes. It contains:
  - a summary
  - NPCs met, linked as `[[Name]]` to existing NPC and place notes
  - loot and XP
  - open threads
  - questions Paylon may want to pursue next time, using the same in-character filter as `/dnd ask`
- **Also:**
  - Adds the recap to the Sections list in `Curse of Strahd.md` and moves the "← latest" marker to it.
  - Appends new names to `glossary.md`.
  - **Lists** NPCs and places that have no note yet. It creates those notes only if the user asks.

### 4.8 D&D-mode launcher

`dnd-mode.ps1` with a desktop shortcut:

1. Runs `C:\Users\magna\GamingMode\GamingMode.ps1 ai`, which starts LM Studio's server bound to `0.0.0.0` (per the existing setup).
2. Runs `lms load` for `qwen/qwen3.5-9b` with a 16K context, and checks that `LMSTUDIO_API_KEY` is set.
3. Starts `dnd-assistant run` in a console window that shows the live status.

Ctrl+C ends the session cleanly (section 4.4). The launcher does not switch the PC back to gaming mode afterward. The user's existing toggle handles that.

## 5. Configuration

`D:\Claude\dnd-assistant\config.toml` holds:

- device names
- the port
- the path to a token file (the token itself is not in the repo)
- the vault path
- the Whisper model and language
- the scribe's interval and model id
- Paylon's actor name

The token is generated once and stored outside git. The user enters it in the module's client settings themselves.

## 6. Repository layout

```
D:\Claude\dnd-assistant\
  pyproject.toml, config.example.toml, README.md
  src/dnd_assistant/   cli.py, runner.py, config.py, listener.py, audio.py, hub.py, pipeline.py, status.py, store.py, scribe.py, timeline.py, session.py, glossary.py
  foundry-module/      module.json, scripts/main.js, lang/en.json
  skills/              dnd/SKILL.md, dnd-recap/SKILL.md   (copied to ~/.claude/skills by an install script)
  scripts/             dnd-mode.ps1, install-skills.ps1
  tests/
  docs/superpowers/specs/
```

The repo is pushed to GitHub. Foundry module releases are created from `foundry-module/`, and the manifest URL points to the latest release's `module.json`. Neither git nor the GitHub CLI is set up for this yet: the GitHub CLI (`gh`) isn't installed and the user's GitHub account is unconfirmed. This is resolved in the implementation plan.

## 7. Error handling summary

| Failure | Behavior |
|---|---|
| Audio device lost | `gap` event, retry every 5 s, status warning |
| Whisper falls behind | Queue grows and catches up. A status warning appears if the backlog exceeds 60 s. |
| Hub down while Foundry is running | Module queues up to 500 events, retries, shows one notice |
| LM Studio down or bad output | Scribe skips the run and catches up later. Capture is unaffected. |
| Hub crash | Restarting with the same date continues the same session folder and database (it appends). |
| Long session | Only `/dnd` reads the timeline's tail. The full timeline is read only by the recap. |

## 8. Testing and build order

1. **Spike 1, audio:** loopback-record "Sonar - Chat" while a Discord call and browser music both play. Pass: only the call is audible in the capture.
2. **Spike 2, Foundry:** a minimal v14 module that logs (to the console) the chat message, roll, combat and actor-update data it receives on dnd5e 6.0.6. Save the samples as test fixtures. This settles the hook names, the roll-type fields and the path of Paylon's resources.
3. **Unit tests (pytest):**
   - storing and ordering events in the store
   - timeline rendering
   - `tail` and `search`
   - scribe merging: no lost facts, duplicates skipped, uncertain facts marked
   - scribe failure and catch-up
   - glossary prompt building
4. **Replay tests:** a recorded short audio clip and the Foundry fixtures from spike 2, run through the full pipeline without a live game.
5. **Dress rehearsal:** a 15-minute Discord call plus a test Foundry world, then run `/dnd`, `/dnd ask` and `/dnd-recap` on the result.
6. **First real session:** the user notes how often they used `/dnd` and how much of their subscription usage it took. This decides whether an API-key mode is worth building.

## 9. Risks and open items

- **Foundry v14 and dnd5e 6.0.6 internals are unknown** until spike 2. The module's field mapping may need to be adjusted.
- **The browser's local-network permission prompt** must be allowed, and its exact behavior depends on the browser.
- **Whisper accuracy** on crosstalk and fantasy names. The glossary helps but won't make it perfect.
- **Qwen3.5-9B's quality as a scribe** is untested. A larger model fits if needed (about 15 GB total, tight). Haiku 5.5 through the API (about 3¢ per session) is a later option.
- **GitHub hosting:** `gh` is not installed, and the user's GitHub account is not confirmed yet.
