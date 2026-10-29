# D&D Table Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture a live D&D session (Discord voice and Foundry VTT events) into SQLite and Obsidian files, keep rolling story notes with a local LLM, and give Claude Code `/dnd` and `/dnd-recap` skills that use them.

**Architecture:** One Python process (`dnd-assistant run`) holds a listener (WASAPI capture → VAD segmenter → faster-whisper), an aiohttp hub on 127.0.0.1 that receives Foundry events, a `Pipeline` that stores every event and renders vault files, and a scribe thread that calls LM Studio. A Foundry v14 module posts events from the user's browser. Claude Code skills read the files and call the CLI.

**Tech Stack:** Python 3.12 (uv-managed venv), faster-whisper (CTranslate2, CUDA), pyaudiowpatch, numpy, aiohttp, httpx, SQLite FTS5, pytest. JavaScript ES modules for Foundry v14, tested with `node --test`. PowerShell 5.1 scripts. `gh` CLI for releases.

**Spec:** `docs/superpowers/specs/2026-10-28-dnd-table-assistant-design.md`

## Global Constraints

- Project root: `D:\Claude\dnd-assistant`. Package `src/dnd_assistant/`. CLI entry point `dnd-assistant`.
- Python 3.12, created with `uv venv --python 3.12`. Run everything with `uv run …`. (`uv` lives at `C:\Users\magna\AppData\Local\hermes\bin\uv.exe`. If it isn't on PATH in a new shell, run `winget install astral-sh.uv`.)
- Campaign folder: `C:\Dropbox\Claude Vora\Claude_Vora\Curse of Strahd`. Live files go in `Live Sessions/YYYY-MM-DD/`, recaps in `Session Notes/YYYY-MM-DD - <Title>.md`, the glossary is `glossary.md`.
- Audio devices: table = loopback of `SteelSeries Sonar - Chat`; me = capture `SteelSeries Sonar - Microphone`. Speaker labels are exactly `Table` and `Me`.
- Whisper: `large-v3-turbo`, `device="cuda"`, `compute_type="float16"`, `language="en"`. VAD silence to close a segment: 0.6 s. Maximum segment: 15 s.
- Hub binds `127.0.0.1` only, default port `8765`, auth header `X-Token`. The token file is `%USERPROFILE%\.dnd-assistant\token`, never inside the repo.
- Scribe: LM Studio at `http://127.0.0.1:1234/v1`, model `qwen/qwen3.5-9b` (already installed; it replaces the spec's Qwen3-8B), context 16384, interval 300 s, burst threshold 150 events, timeout 120 s, at most 2 attempts per run. LM Studio requires `Authorization: Bearer`; the key comes from the user environment variable `LMSTUDIO_API_KEY`, which the user sets themselves, never in chat.
- Note sections, exactly and in this order: `People`, `Places`, `Clues & Lore`, `Quests & Promises`, `Loot & Rewards`, `Party Decisions`.
- Event `source` ∈ {`voice`, `foundry`, `system`}. `kind` ∈ {`speech`, `chat`, `roll`, `combat`, `state`, `gap`, `note`}. **`ts` is the time the hub received the event (local, timezone-aware)**. Foundry's own timestamp, when sent, is kept in `data["foundry_ts"]`.
- Foundry: v14 build 368, dnd5e 6.0.6. Module id `dnd-assistant`. Its client setting "Send to my PC" is off by default. The module's in-memory queue holds at most 500 events.
- No audio is written to disk unless `--keep-audio` is passed.
- Git author: Wayne Hancock <magnaxiom@gmail.com>. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Discord goes quiet:** WASAPI loopback then delivers no data at all. A sentence that was mid-segment must still be emitted after the 0.6 s silence, not held until someone speaks again (watchdog test, Task 11).
2. **Whisper hallucinations on near-silence or noise** ("Thank you.", "Thanks for watching!", text that is only punctuation) must not reach the timeline (filter test, Task 10).
3. **Messy text** (newlines, HTML from Foundry chat cards, quotes, emoji, very long messages) must render as exactly one timeline line and be searchable (tests in Task 5 and Task 13).
4. **Restarting on the same date after a crash** must continue the same folder and database. The scribe must not re-process events or duplicate facts (tests in Tasks 6 and 9).
5. **LM Studio problems** (not running, 401 because the key is missing, model not loaded, slow) must be a skipped scribe run with a status warning, never a crash, and must never block capture (test in Task 9).

---

### Task 1: Project scaffold and config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `config.example.toml`, `README.md`, `src/dnd_assistant/__init__.py`, `src/dnd_assistant/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Config` with fields:
  - `campaign: Path`
  - `chat_device: str = "SteelSeries Sonar - Chat"`
  - `mic_device: str = "SteelSeries Sonar - Microphone"`
  - `port: int = 8765`
  - `token_file: Path = ~/.dnd-assistant/token`
  - `foundry_origins: list[str]`
  - `whisper_model: str = "large-v3-turbo"`
  - `language: str = "en"`
  - `scribe_url: str = "http://127.0.0.1:1234/v1"`
  - `scribe_model: str = "qwen/qwen3.5-9b"`
  - `scribe_key_env: str = "LMSTUDIO_API_KEY"`
  - `scribe_interval_s: int = 300`
  - `scribe_burst: int = 150`
  - `actor_name: str = "Paylon"`

  Also `load_config(path: Path) -> Config` and `read_or_create_token(path: Path) -> str`.

- [ ] **Step 1:** `git init` already exists. Create the venv with `uv venv --python 3.12`. Write `pyproject.toml` (hatchling build, `[project.scripts] dnd-assistant = "dnd_assistant.cli:main"`). Runtime dependencies: `faster-whisper`, `pyaudiowpatch`, `numpy`, `aiohttp`, `httpx`. Dev dependencies: `pytest`, `pytest-aiohttp`. Install the CUDA libraries with `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*`. `.gitignore`: `.venv/`, `config.toml`, `__pycache__/`, `*.wav`, except `tests/fixtures/**/*.wav`, `foundry-module/dist/`.
- [ ] **Step 2: Write the failing tests**
  - `test_load_config_defaults`: a TOML file with only `campaign = "X"` and `foundry_origins = ["https://f.example"]` → `cfg.port == 8765` and `cfg.chat_device == "SteelSeries Sonar - Chat"`.
  - `test_load_config_requires_campaign`: raises `ValueError`.
  - `test_token_created_once`: the first call creates a file holding a 32+ character url-safe token; a second call returns the same value.
- [ ] **Step 3:** Run `uv run pytest tests/test_config.py -v`. Expected: FAIL (import error).
- [ ] **Step 4:** Implement `config.py` with `tomllib`. Make tokens with `secrets.token_urlsafe(32)`, creating parent directories as needed.
- [ ] **Step 5:** Run the tests. Expected: PASS. Write `config.example.toml` holding the real campaign path and `foundry_origins = ["<your Foundry URL>"]`. Add a README with a setup section.
- [ ] **Step 6:** Commit with message `chore: scaffold project and config`.

### Task 2: Spike 1, audio capture (manual, with the user)

**Files:**
- Create: `scripts/spikes/record_devices.py` (throwaway; a header comment says so)
- Create: `tests/fixtures/audio/sample.wav`

- [ ] **Step 1:** Write `record_devices.py`. It lists pyaudiowpatch devices, including the `[Loopback]` entries, then records 20 s from the loopback of the device whose name contains `SteelSeries Sonar - Chat` and from `SteelSeries Sonar - Microphone`, writing `chat.wav` and `mic.wav` to the scratchpad.
- [ ] **Step 2:** Ask the user to play browser music (Sonar Gaming) and talk on a Discord call (or a Discord voice test) while it runs. **Pass:** `chat.wav` contains the call and no music; `mic.wav` contains only the user's voice. Record the exact device names that were printed. If they differ from Global Constraints, update `config.example.toml`.
- [ ] **Step 3:** Ask the user to record about 15 s of themselves reading: "Ireena, Ismark, Strahd, Barovia, Vallaki, Krezk. Paylon grapples the zombie." Save it as a 16 kHz mono `tests/fixtures/audio/sample.wav`. This is the user's own voice, so no consent issue.
- [ ] **Step 4:** Commit the fixture and the spike script with message `spike: verify Sonar Chat loopback isolates Discord`. If spike 1 fails, stop and switch to the spec's VB-CABLE fallback before continuing.

### Task 3: GitHub hosting and Spike 2, Foundry probe (with the user)

**Files:**
- Create: `foundry-module/module.json`, `foundry-module/scripts/probe.js`, `scripts/release-module.ps1`
- Create: `tests/fixtures/foundry/*.json`

**Interfaces:**
- Produces: `scripts/release-module.ps1 -Version <x.y.z>`. It sets `version` and `download` in `module.json`, zips `foundry-module/` (excluding `test/` and `dist/`) to `foundry-module/dist/module.zip`, then runs `gh release create v<x.y.z> dist/module.zip module.json`.
  - Manifest URL: `https://github.com/<owner>/dnd-assistant/releases/latest/download/module.json`

- [ ] **Step 1:** Run `winget install GitHub.cli`. Ask the user to run `gh auth login` in their own terminal; Claude never handles the credentials. Then create a public repo and push: `gh repo create dnd-assistant --public --source . --push`. Confirm the user is happy for the code to be public; it contains no secrets.
- [ ] **Step 2:** Write the `module.json` manifest: id `dnd-assistant`, `compatibility: {minimum: "14", verified: "14"}`, `esmodules: ["scripts/probe.js"]`, plus `manifest` and `download` URLs.
- [ ] **Step 3:** Write `probe.js`. It pushes the raw data from these hooks into `window.__dndProbe` (an array):
  - `createChatMessage`: `message.toObject()` plus `message.rolls.map(r => r.toJSON())`
  - `updateCombat`: the change set plus `combat.round`, `combat.turn` and the combatant name
  - `updateActor` and `createActiveEffect`: the change set plus `actor.name`
  - the initial snapshot of `game.user.character.toObject()`

  It also logs each item with `console.debug("[dnd-probe]", …)`.
- [ ] **Step 4:** Run `release-module.ps1 -Version 0.0.1`. The user installs the module from the manifest URL and enables it in a test world, logged in as their player user.
- [ ] **Step 5:** The user, guided by Claude in the built-in browser or by their own clicks, performs each of these:
  - an in-character message, an out-of-character message and an emote
  - an Athletics check
  - an attack roll and a damage roll from Paylon's sheet
  - a saving throw
  - an initiative roll
  - starting combat and advancing a turn
  - taking damage
  - adding a condition
  - spending a resource (Moxie)

  Then, in DevTools, run `copy(JSON.stringify(window.__dndProbe, null, 1))` and save the result.
- [ ] **Step 6:** Split the capture into one fixture per case: `chat-ic.json`, `chat-ooc.json`, `chat-emote.json`, `roll-skill.json`, `roll-attack.json`, `roll-damage.json`, `roll-save.json`, `roll-initiative.json`, `combat-turn.json`, `actor-hp.json`, `actor-condition.json`, `actor-resource.json`, `actor-snapshot.json`. Write `tests/fixtures/foundry/NOTES.md` recording which fields hold:
  - the in-character, out-of-character and emote style
  - the roll type and label
  - the critical and fumble markers
  - the HP path
  - the resource paths
  - conditions and effects
- [ ] **Step 7:** Commit with message `spike: capture Foundry v14 / dnd5e 6.0.6 event fixtures`.

### Task 4: Event store

**Files:**
- Create: `src/dnd_assistant/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Event(ts: datetime, source: str, kind: str, speaker: str | None, text: str, data: dict = {}, id: int | None = None)`
  - `SOURCES`, `KINDS` (frozensets)
  - `class Store`:
    - `Store(path: Path)` opens in WAL mode with `check_same_thread=False` and a lock
    - `add(e: Event) -> int`
    - `since(event_id: int) -> list[Event]`
    - `tail(minutes: float, now: datetime | None = None) -> list[Event]`. `now` defaults to the newest event's `ts`, not the clock, so `tail` on a finished session still returns its last minutes.
    - `search(query: str, limit: int = 50) -> list[Event]`
    - `get_meta(key: str) -> str | None`
    - `set_meta(key: str, value: str) -> None`
    - `close()`

- [ ] **Step 1: Write the failing tests**
  - `test_add_and_since_preserve_order`
  - `test_add_rejects_unknown_kind` (`ValueError`)
  - `test_tail_filters_by_minutes`: events at now-20 minutes, now-5 and now-1, with `tail(10, now)` → the last two.
  - `test_tail_defaults_to_newest_event`: events 2 hours in the past, `tail(10)` → the events within 10 minutes of the newest one.
  - `test_search_fts_matches_words`: a speech event "Ireena is Ismark's sister" → `search("Ireena")` returns it; `search("Strahd")` returns `[]`.
  - `test_search_handles_quotes_and_symbols`: `search('sister"')` doesn't raise.
  - `test_meta_roundtrip`
  - `test_reopen_keeps_events`: close, reopen the same path, `since(0)` has the same length.
- [ ] **Step 2:** Run `uv run pytest tests/test_store.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. The `events` table stores `ts` as ISO 8601 text and `data_json`. Add an FTS5 table `events_fts(text, speaker)` populated in `add`. Sanitize the search query by quoting each word as an FTS phrase.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: SQLite event store with FTS search`.

### Task 5: Timeline and state rendering

**Files:**
- Create: `src/dnd_assistant/timeline.py`
- Test: `tests/test_timeline.py`

**Interfaces:**
- Consumes: `Event`.
- Produces:
  - `render_line(e: Event) -> str | None` (returns `None` for `state`)
  - `render_state(data: dict) -> str`
  - `class TimelineWriter(path: Path)` with `.append(e: Event) -> None`. It appends, uses UTF-8, and writes a `# Timeline YYYY-MM-DD` header if the file is new.
- Line formats (`HH:MM:SS` from `e.ts`):

| kind | format |
|---|---|
| speech | `HH:MM:SS {speaker}: "{text}"` |
| chat | `HH:MM:SS [IC]/[OOC]/[EMOTE] {speaker}: "{text}"`, from `data["style"]` ∈ `ic`/`ooc`/`emote` |
| roll | `HH:MM:SS 🎲 {speaker}: {data.label} {data.total} [{data.dice}]`, plus ` CRIT` or ` FUMBLE` |
| combat | `HH:MM:SS ⚔ {text}` |
| gap | `HH:MM:SS ⚠ {text}` |
| note | `HH:MM:SS 📝 {text}` |

- `state` data shape: `{actor, hp: {value, max, temp}, resources: {name: {value, max}}, conditions: [str], effects: [str]}`.

- [ ] **Step 1: Write the failing tests**
  - One test per row of the table, using the spec's exact examples. For example, roll `label="Athletics (grapple)"`, `total=19`, `dice="d20=15"` → `21:14:20 🎲 Paylon: Athletics (grapple) 19 [d20=15]`.
  - `test_render_collapses_newlines_and_strips_html`: text `"<p>Hello\nthere</p>"` → `"Hello there"` on one line.
  - `test_state_not_in_timeline`
  - `test_render_state` shows `HP 31/45 (+5 temp)`, each resource as `Moxie 2/4`, and conditions listed (or `none`).
  - `test_writer_appends_with_header_once`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. HTML stripping uses `html.parser`, then unescapes entities and collapses whitespace.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: timeline and character-state rendering`.

### Task 6: Session folder and glossary

**Files:**
- Create: `src/dnd_assistant/session.py`, `src/dnd_assistant/glossary.py`
- Test: `tests/test_session.py`, `tests/test_glossary.py`

**Interfaces:**
- Produces:
  - `session_dir(campaign: Path, day: date, new: bool = False) -> Path`. It creates `Live Sessions/YYYY-MM-DD`. When `new` is set and that folder exists, it uses the first free suffix: `-2`, `-3`, …
  - `latest_session(campaign: Path) -> Path | None`, by folder modification time.
  - `collect_names(campaign: Path, extra: list[str] = []) -> list[str]`. Order: `extra` (Foundry actor names), then `glossary.md` lines (skipping blank lines and lines starting with `#`), then the file stems of `NPCs/*.md` and `Places/*.md`. Case-insensitive deduplication, keeping the first occurrence.
  - `build_prompt(names: list[str], max_chars: int = 800) -> str`. Format: `"Curse of Strahd D&D session. Names: A, B, C."`, truncated at a name boundary.

- [ ] **Step 1: Write the failing tests**
  - `test_same_day_reuses_folder` (the default continues the session; this is the restart-after-crash case)
  - `test_new_flag_adds_suffix`
  - `test_latest_session`
  - `test_collect_names_order_and_dedupe`, using a tmp campaign with `glossary.md` ("# header", "Ireena", ""), `NPCs/Ireena Kolyana.md`, `Places/Krezk.md`, extra `["Paylon", "ireena"]` → `["Paylon", "ireena", "Ireena Kolyana", "Krezk"]`
  - `test_build_prompt_truncates_at_name_boundary`
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: session folders and Whisper glossary prompt`.

### Task 7: Status and Pipeline

**Files:**
- Create: `src/dnd_assistant/status.py`, `src/dnd_assistant/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Store`, `Event`, `TimelineWriter`, `render_state`.
- Produces:
  - `class Status`:
    - fields: `speech: int`, `foundry: int`, `notes_at: datetime | None`, `warnings: dict[str, str]`
    - `warn(key: str, msg: str)` and `clear(key: str)`
    - `line() -> str`, in the format `🎙 {speech} lines · 🎲 {foundry} Foundry events · ✍ notes {HH:MM | —}` followed by ` · ⚠ {msg}` for each warning
  - `class Pipeline(store: Store, timeline: TimelineWriter, state_path: Path, actor_name: str, status: Status)`:
    - `handle(e: Event) -> int` is thread-safe. It stores the event, appends its timeline line, increments the counters, and rewrites `state_path` when `kind == "state"` and `data["actor"] == actor_name`.

- [ ] **Step 1: Write the failing tests**
  - `test_speech_stored_and_rendered`
  - `test_state_for_other_actor_ignored_for_file` (it is still stored)
  - `test_status_line_format`
  - `test_handle_from_threads`: 4 threads × 50 events → `since(0)` has 200 events and the timeline has 200 lines.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement with a `threading.Lock` around `handle`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: event pipeline and status line`.

### Task 8: Hub HTTP endpoint

**Files:**
- Create: `src/dnd_assistant/hub.py`
- Test: `tests/test_hub.py`

**Interfaces:**
- Consumes: `Pipeline.handle`, `Event`.
- Produces: `make_app(handle: Callable[[Event], int], token: str, allowed_origins: list[str]) -> aiohttp.web.Application` with these routes:
  - `GET /health` → `{"ok": true}`
  - `POST /event`: the body is `{kind ∈ chat|roll|combat|state, speaker?, text, data?, ts?}` → 202 `{"id": n}`. Any `ts` the client sends goes into `data["foundry_ts"]`, and `ts` is set to the receive time. `source = "foundry"`.
  - `OPTIONS /event` (preflight): for an allowed `Origin` → 204 with these headers:
    - `Access-Control-Allow-Origin: <origin>`
    - `Access-Control-Allow-Methods: POST`
    - `Access-Control-Allow-Headers: Content-Type, X-Token`
    - `Access-Control-Allow-Private-Network: true`

    Otherwise → 403.
  - Responses to an allowed origin always carry `Access-Control-Allow-Origin`.

- [ ] **Step 1: Write the failing tests** (pytest-aiohttp)
  - `test_post_requires_token` (401)
  - `test_post_rejects_bad_kind` (400)
  - `test_post_rejects_non_json` (400)
  - `test_post_accepted_and_ts_is_receive_time` (`foundry_ts` preserved)
  - `test_preflight_allowed_origin_headers`
  - `test_preflight_unknown_origin_403`
  - `test_health`
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement `make_app` plus `async def serve(app, port) -> web.AppRunner`, bound to `127.0.0.1`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: localhost hub endpoint for Foundry events`.

### Task 9: Scribe

**Files:**
- Create: `src/dnd_assistant/scribe.py`
- Test: `tests/test_scribe.py`

**Interfaces:**
- Consumes: `Store` (meta key `scribe_last_id`), `render_line`, `Status`.
- Produces:
  - `SECTIONS: tuple[str, ...]`
  - `@dataclass(frozen=True) class Fact(section: str, subject: str, fact: str, time: str, certain: bool)`
  - `class LLMClient(Protocol)` with `complete_json(system: str, user: str, schema: dict, timeout: float) -> dict`
  - `class LMStudioClient(base_url: str, model: str, api_key: str | None)`. It calls `POST {base_url}/chat/completions` with `response_format={"type": "json_schema", "json_schema": {"name": "facts", "strict": true, "schema": schema}}` through httpx, and raises `ScribeError` on HTTP or JSON errors.
  - `parse_facts(obj: dict) -> list[Fact]`, which drops entries with an unknown section or empty fields.
  - `class NotesFile(path: Path)`:
    - `.subjects() -> list[str]`, as `"Section / Subject"`
    - `.merge(facts: list[Fact]) -> int`, which returns the number added
    - It re-parses an existing file on construction. Bullet format: `- **{subject}**: {fact} ({time})`, with ` (?)` appended when uncertain. Sections are written in `SECTIONS` order. A duplicate means the same section, subject and fact text, compared case-insensitively with whitespace stripped.
  - `class Scribe(store, notes: NotesFile, client: LLMClient, status: Status, interval_s=300, burst=150, timeout=120.0)`:
    - `.due(now: datetime) -> bool`
    - `.run_once(now: datetime) -> bool`
    - `.loop(stop: threading.Event)` checks `due` every 5 s and calls `run_once` once more after `stop` is set.
- System prompt rules, verbatim:
  - "Record only facts stated in the events."
  - "Give each fact the HH:MM of its source event."
  - "Do not infer motives."
  - "Mark facts you are unsure of with certain=false."
  - "Skip facts already listed under Existing subjects unless something new was said."

- [ ] **Step 1: Write the failing tests** (with a `FakeClient` that returns scripted dicts or raises)
  - `test_merge_appends_and_skips_duplicates`
  - `test_uncertain_marked`
  - `test_notes_file_reparse_roundtrip` (restart case: a new `NotesFile` on the same path, merging the same facts → adds 0)
  - `test_run_once_advances_last_id_on_success`
  - `test_run_once_invalid_json_twice_keeps_last_id`
  - `test_run_once_client_error_warns_and_returns_false` (a 401-style `ScribeError` → `status.warnings["scribe"]` is set and the last id is unchanged)
  - `test_next_success_includes_missed_events`
  - `test_due_after_interval_or_burst`
  - `test_no_new_events_is_noop`
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. The user message contains `Existing subjects:` followed by the list, then `New events:` followed by the rendered lines.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: local-LLM scribe with append-only notes merge`.

### Task 10: Segmenter, resampling and hallucination filter (pure logic)

**Files:**
- Create: `src/dnd_assistant/audio.py`
- Test: `tests/test_audio.py`

**Interfaces:**
- Produces:
  - `@dataclass class Segment(audio: np.ndarray, start: datetime)`
  - `class Segmenter(detect_speech: Callable[[np.ndarray], list[tuple[int, int]]], sample_rate=16000, silence_s=0.6, max_s=15.0, pad_s=0.2)`:
    - `.feed(chunk: np.ndarray, chunk_start: datetime) -> list[Segment]`
    - `.flush() -> list[Segment]`
  - `resample(x: np.ndarray, src_rate: int, dst_rate: int = 16000) -> np.ndarray` (mono float32 via `np.interp`)
  - `to_mono(x: np.ndarray, channels: int) -> np.ndarray`
  - `is_hallucination(text: str, duration_s: float) -> bool`
  - `silero_detector() -> Callable`, which wraps `faster_whisper.vad.get_speech_timestamps` with `VadOptions(min_silence_duration_ms=600)`. It is not unit-tested; Task 11 covers it.
- Segmenter algorithm:
  1. Keep a rolling buffer and the timestamp of its first sample.
  2. After each feed, run `detect_speech(buffer)`.
  3. If there's no speech and the buffer is longer than 2 s, drop all but the last 0.5 s.
  4. If speech ends at least `silence_s` before the end of the buffer, emit `[first_start - pad, last_end + pad]` and drop the buffer up to `last_end`.
  5. If the speech since `first_start` reaches `max_s`, emit the first `max_s` and keep the rest.
  6. A segment's start = buffer start time + offset / rate.

- [ ] **Step 1: Write the failing tests** (a fake detector that returns ranges where the samples are non-zero)
  - `test_emits_after_silence`
  - `test_holds_while_speaking`
  - `test_splits_at_max_length`
  - `test_segment_start_timestamp`
  - `test_drops_long_silence_buffer`
  - `test_flush_emits_pending`
  - `test_resample_length_48k_to_16k`
  - `test_to_mono_averages`
  - `test_hallucination_filter`: `("Thank you.", 0.8)` and `("...", 3.0)` and `("", 1.0)` → True; `("Thanks for watching!", 1.0)` → True; `("Thank you, Ismark.", 2.0)` → False.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. The hallucination list is `{"thank you.", "thanks for watching!", "thank you for watching.", "you"}`. It applies only when `duration_s < 1.5`, except that text with no letters always counts.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat: VAD segmenter, resampling, hallucination filter`.

### Task 11: Capture, transcription and listener threads

**Files:**
- Create: `src/dnd_assistant/listener.py`
- Test: `tests/test_listener.py` (logic, using fakes); `tests/test_gpu_transcribe.py` (`@pytest.mark.gpu`)

**Interfaces:**
- Consumes: `Segmenter`, `resample`, `to_mono`, `is_hallucination`, `silero_detector`, `Pipeline.handle`, `Status`, `build_prompt`.
- Produces:
  - `find_device(pa, name: str, loopback: bool) -> dict`, which matches a substring of the device name. Loopback entries are the ones from `pa.get_loopback_device_info_generator()`. Raises `LookupError`.
  - `class AudioSource(device_name: str, loopback: bool)`:
    - `.open()` opens a callback stream that pushes `(float32 mono 16 kHz chunk, datetime)` onto `self.queue`
    - `.close()`
  - `class CaptureWorker(threading.Thread)(source_factory: Callable[[], AudioSource], speaker: str, segmenter: Segmenter, out: queue.Queue, handle, status, stop: threading.Event)`:
    - It reads with `queue.get(timeout=0.5)`. **On a timeout it feeds 0.5 s of zeros**, which is the silence watchdog.
    - On an exception it calls `handle(Event(..., source="system", kind="gap", text=f"{speaker} audio lost: {err}"))`, closes, sleeps 5 s and reopens.
    - It sets `status.warn(f"silent-{speaker}", …)` after 600 s with no detected speech, and clears it when speech returns.
    - It puts `(speaker, Segment)` on `out`.
  - `class Transcriber(model: str, language: str)` with `.transcribe(audio: np.ndarray, prompt: str) -> str`. It uses `WhisperModel(model, device="cuda", compute_type="float16")` and `transcribe(..., language, initial_prompt=prompt, vad_filter=False, condition_on_previous_text=False, beam_size=5)`, joining the segment texts.
  - `class TranscribeWorker(threading.Thread)(transcriber, inbox: queue.Queue, handle, status, prompt: Callable[[], str], stop, keep_audio_dir: Path | None)`. It skips hallucinations, emits `Event(source="voice", kind="speech", speaker=...)` with `ts = segment.start`, and warns `backlog` when `inbox.qsize()` × the average segment length exceeds 60 s.

- [ ] **Step 1: Write the failing tests**
  - `test_watchdog_flushes_sentence_when_source_goes_silent`: a fake source yields a speech chunk, then nothing → a segment is emitted within the time it takes 0.6 s of zeros to be fed in. This is Review Focus 1.
  - `test_source_error_emits_gap_and_reopens`
  - `test_silence_warning_after_600s` (inject a clock)
  - `test_transcribe_worker_skips_hallucination`
  - `test_transcribe_worker_emits_speech_event_with_segment_start`
  - The GPU test `test_sample_wav_contains_names` (`@pytest.mark.gpu`): transcribe `tests/fixtures/audio/sample.wav` with the glossary prompt → the text contains "Ireena" and "Barovia" (case-insensitive).
- [ ] **Step 2:** Run `uv run pytest tests/test_listener.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Make the clock injectable (`now: Callable[[], datetime]`) for testing.
- [ ] **Step 4:** Run `uv run pytest tests/test_listener.py -v`, then `uv run pytest -m gpu -v`. Expected: all PASS. The first GPU run downloads the model.
- [ ] **Step 5:** Commit with message `feat: audio capture, Whisper transcription, listener threads`.

### Task 12: CLI

**Files:**
- Create: `src/dnd_assistant/cli.py`, `src/dnd_assistant/runner.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes everything above.
- Produces the `dnd-assistant` subcommands (argparse, `--config` defaults to `<repo>/config.toml`):
  - `run [--new] [--keep-audio]`. `runner.run(cfg, new, keep_audio)`:
    1. Builds the session dir, `Store(dir/"session.db")`, `TimelineWriter(dir/"timeline.md")`, `Pipeline` with state path `dir/"paylon-now.md"`, and `NotesFile(dir/"session-notes.md")`.
    2. Starts the hub (the event loop runs in the main thread), the 2 `CaptureWorker`s, 1 `TranscribeWorker` and the `Scribe.loop` thread.
    3. Prints `status.line()` every 2 s, overwriting the same line.
    4. Writes a `note` event "Session started" / "Session ended".
    5. On Ctrl+C it sets `stop`, joins the workers, and the scribe makes its final pass.
    6. The glossary prompt is rebuilt every 10 minutes with the actor names seen in Foundry events.
    7. If no Foundry events arrive for 20 minutes after the first one, it warns `foundry`.
  - `tail [--minutes 10] [--session PATH]` prints the rendered lines of `Store.tail` for the latest session.
  - `search TERMS... [--session PATH]` prints the rendered matches.
  - `devices` lists input and loopback devices.
  - `token [--show]` creates the token if needed, prints the file path, and prints the token itself only with `--show`.
  - Exit code 2 with a clear message when no session exists, or when `config.toml` is missing (pointing to `config.example.toml`).

- [ ] **Step 1: Write the failing tests**
  - `test_tail_prints_latest_session_lines` (tmp campaign plus a store populated directly)
  - `test_search_prints_matches`
  - `test_tail_without_session_exits_2`
  - `test_missing_config_exits_2`
  - `test_token_hidden_by_default`
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the full suite, `uv run pytest -v` (GPU tests are excluded by default via `addopts = -m "not gpu"`). Expected: PASS.
- [ ] **Step 5: Manual check.**
  1. Run `uv run dnd-assistant run` with a Discord voice test playing. Within about 5 s, speech lines appear in `timeline.md` and the counter moves.
  2. `curl -X POST -H "X-Token: <token>" -d '{"kind":"chat","speaker":"Ismark","text":"Help","data":{"style":"ic"}}' http://127.0.0.1:8765/event` adds an `[IC]` line.
  3. Ctrl+C exits cleanly.
- [ ] **Step 6:** Commit with message `feat: CLI with run, tail, search, devices, token`.

### Task 13: Foundry module, mapping and transport (pure JS)

**Files:**
- Create: `foundry-module/scripts/mapping.js`, `foundry-module/scripts/transport.js`
- Test: `foundry-module/test/mapping.test.mjs`, `foundry-module/test/transport.test.mjs`

**Interfaces:**
- Consumes: the fixtures and `NOTES.md` from Task 3, and the hub body contract from Task 8.
- Produces:
  - In `mapping.js`:
    - `mapChatMessage(msg: object, rolls: object[]) -> HubEvent[]`, which yields a `chat` event when there is text and a `roll` event for each roll
    - `mapCombat(combat: {round, turn, combatantName, started?, ended?}) -> HubEvent | null`
    - `mapActor(actor: object) -> HubEvent`, a full `state` event in the Task 5 data shape
  - `HubEvent = {kind, speaker, text, data, ts}`
  - Roll `data`: `{label, total, formula, dice: "d20=15" (multiple dice as "d6=3,d6=5"), roll_type ∈ attack|damage|save|check|skill|initiative|other, crit: bool, fumble: bool}`
  - Chat text is HTML-stripped, with `data.style` ∈ `ic|ooc|emote`.
  - In `transport.js`: `class Sender({fetchFn, url, token, notify, maxQueue = 500})` with `.send(ev)` and `.flush()`. When the queue is full it drops the oldest event. Backoff is 1, 2, 4 … 30 s. It calls `notify` once per outage.

- [ ] **Step 1: Write the failing tests** (`node --test foundry-module/test/`)
  - One test per fixture file, asserting its kind, speaker, and its label, total and dice (taken from the fixture's own values), plus the critical or fumble flag where relevant.
  - `chat-emote` → `style: "emote"`.
  - `roll-attack.json`, whose message content is a dnd5e HTML chat card → no emitted event's `text` contains `<`.
  - `actor-snapshot` → `hp.value/max/temp`, `resources.Moxie`, and `conditions` populated.
  - `sender_queues_when_down_and_flushes_in_order`
  - `sender_drops_oldest_over_500`
  - `sender_notifies_once_per_outage`
  - `sender_sets_X-Token_header`
- [ ] **Step 2:** Run `node --test foundry-module/test/`. Expected: FAIL.
- [ ] **Step 3:** Implement, using the field paths recorded in `NOTES.md`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit with message `feat(foundry): event mapping and resilient sender`.

### Task 14: Foundry module wiring and release

**Files:**
- Create: `foundry-module/scripts/main.js`, `foundry-module/lang/en.json`
- Modify: `foundry-module/module.json` (`esmodules: ["scripts/main.js"]`; remove `probe.js`)
- Delete: `foundry-module/scripts/probe.js`

**Interfaces:**
- Consumes: `mapping.js`, `Sender`.
- Produces:
  - Client settings (`scope: "client"`): `enabled` (Boolean, default false, labelled "Send to my PC"), `port` (Number, 8765), `token` (String).
  - In the `ready` hook, when `enabled` is set:
    - Create a `Sender` for `http://127.0.0.1:{port}/event`.
    - Send `mapActor(game.user.character)`.
    - Register `createChatMessage` → `mapChatMessage`. Only messages visible to this client are processed; Foundry already filters these, so skip messages where `message.visible` is false.
    - Register `updateCombat` / `combatStart` / `deleteCombat` → `mapCombat`.
    - Register `updateActor` / `createActiveEffect` / `deleteActiveEffect` for `game.user.character` → `mapActor`.
  - Notices go through `ui.notifications.warn`, once per outage.

- [ ] **Step 1:** Implement `main.js` and `en.json`.
- [ ] **Step 2:** Run `node --test foundry-module/test/`. Expected: still PASS.
- [ ] **Step 3:** Run `scripts/release-module.ps1 -Version 0.1.0`. The user updates the module in Foundry, enables "Send to my PC", and pastes the token from `dnd-assistant token --show` themselves. Add the Foundry URL to `foundry_origins` in `config.toml`.
- [ ] **Step 4: Manual check.** With `dnd-assistant run` running, in the test world:
  - An in-character message, a skill roll, a combat turn and an HP change each appear in `timeline.md` or `paylon-now.md` within 2 s.
  - The browser's local-network permission prompt has been allowed.
  - Stopping the hub, rolling twice, then restarting the hub delivers both rolls in order, with one notice.
  - A second browser logged in as another user with the setting off sends nothing (the hub counter doesn't change).
- [ ] **Step 5:** Commit with message `feat(foundry): wire hooks and settings, release 0.1.0`.

### Task 15: Claude Code skills

**Files:**
- Create: `skills/dnd/SKILL.md`, `skills/dnd-recap/SKILL.md`, `scripts/install-skills.ps1`

**Interfaces:**
- Consumes: `dnd-assistant tail`, `dnd-assistant search` (invoked as `uv run --project D:\Claude\dnd-assistant dnd-assistant …`), and the vault layout.
- Produces: `/dnd`, `/dnd ask` and `/dnd-recap`. `install-skills.ps1` copies `skills/*` to `%USERPROFILE%\.claude\skills\`, overwriting.

Required content of `dnd/SKILL.md`:
- **Frontmatter description:** triggers on `/dnd`, rules or tactics questions about Paylon during a session, and "what should Paylon ask".
- **Steps:**
  1. Read `My Character/Paylon cheat sheet.md`, Paylon's entry in `Party.md`, the `## GM rulings` section of `Curse of Strahd.md`, and the latest `Live Sessions/*/paylon-now.md` and `session-notes.md`.
  2. Run `tail --minutes 10`.
  3. For anything earlier, run `search`.
- **Answer rules:**
  - Best option first, at most 3 bullets.
  - Name the rule or feature behind each claim.
  - GM rulings override the rules as written.
  - Say "ask the DM" when a ruling is uncertain.
  - No preamble.
- **`ask` mode:** the spec's in-character filter (section 4.7) word for word. Up to 5 suggestions, each a line of Paylon's dialogue plus "Why Paylon would ask:". Flag borderline voice-sourced items "(table talk?)".

Required content of `dnd-recap/SKILL.md`:
- **Inputs:** the latest session folder (or a path argument), `session-notes.md` and the full `timeline.md`.
- **Output:** `Session Notes/YYYY-MM-DD - <Title>.md`, matching the frontmatter and style of the existing session notes. Read `Session Notes/Yester Hill.md` as the style reference. Sections:
  - Summary
  - NPCs met (as `[[links]]` to existing `NPCs/` and `Places/` notes)
  - Loot & XP
  - Open threads
  - Questions Paylon may pursue (in-character filter)
- **Then:**
  - Add a line under `## Sections` in `Curse of Strahd.md` and move "← latest" to it.
  - Append new names to `glossary.md`.
  - List NPCs and places without notes, and create them only if the user says yes.

- [ ] **Step 1:** Write both SKILL.md files and the install script, then run `scripts/install-skills.ps1`.
- [ ] **Step 2: Verify** against a session folder created in the Task 12 manual check, or by `tests/fixtures/replay` in Task 17:
  - `/dnd can I grapple while prone?` returns at most 3 bullets naming a rule.
  - `/dnd ask` returns in-character lines only.
  - `/dnd-recap` writes the note into a **copy** of the campaign folder (pass the path) and the hub-note edit looks right.
- [ ] **Step 3:** Commit with message `feat: /dnd and /dnd-recap Claude Code skills`.

### Task 16: D&D mode launcher

**Files:**
- Create: `scripts/dnd-mode.ps1`, `scripts/make-shortcut.ps1`

**Interfaces:**
- Consumes: `C:\Users\magna\GamingMode\GamingMode.ps1 ai`, `C:\Users\magna\.lmstudio\bin\lms.exe`, `dnd-assistant run`.
- Produces: `dnd-mode.ps1 [-New]` and a Desktop shortcut named "D&D Mode". Use the OneDrive Desktop if it exists, as the Gaming Mode shortcut does.

- [ ] **Step 1:** Write `dnd-mode.ps1`:
  1. Run `GamingMode.ps1 ai`.
  2. Run `lms load qwen/qwen3.5-9b` with a 16384 context. Confirm the flag name with `lms load --help` first and use what it prints.
  3. Check that `$env:LMSTUDIO_API_KEY` is set in the user environment, and print a one-line fix if it isn't.
  4. Run `uv run --project D:\Claude\dnd-assistant dnd-assistant run` (passing `--new` when `-New` is given) in the current console, with the window title "D&D Assistant".
- [ ] **Step 2:** Write `make-shortcut.ps1` (it targets `powershell.exe -NoExit -ExecutionPolicy Bypass -File …\dnd-mode.ps1`) and run it.
- [ ] **Step 3: Verify.**
  - Starting from Gaming mode, double-click "D&D Mode". LM Studio comes up, `lms ps` shows the model loaded, and the status line appears.
  - `nvidia-smi` shows total GPU memory use under 14 GB with Discord and Foundry open.
  - Ctrl+C ends the session cleanly.
- [ ] **Step 4:** Commit with message `feat: D&D mode launcher and shortcut`.

### Task 17: Replay integration test and dress rehearsal

**Files:**
- Create: `tests/test_replay.py`, `tests/fixtures/replay/events.jsonl`

**Interfaces:**
- Consumes: `Pipeline`, `Scribe` (with `FakeClient`), the Foundry fixtures mapped by the Task 13 logic exported to JSONL, and `sample.wav` (GPU variant).

- [ ] **Step 1: Write the tests**
  - `test_replay_builds_session_files`: in a tmp campaign, feed `events.jsonl` (speech, chat, rolls, combat, state) through `Pipeline`, then run `Scribe.run_once` with a fake that returns 2 facts. Assert that `timeline.md` has one line per non-state event, in order; `paylon-now.md` shows the last HP; and `session-notes.md` contains both facts under the right sections.
  - `@pytest.mark.gpu test_replay_audio_end_to_end`: feed `sample.wav` through `Segmenter` + `Transcriber` + `Pipeline` → `timeline.md` contains `Me: "` and "Ireena".
- [ ] **Step 2:** Run `uv run pytest -v`, then `uv run pytest -m gpu -v`. Expected: PASS.
- [ ] **Step 3: Dress rehearsal with the user.**
  1. A 15-minute Discord call (the user and a friend or their phone) plus a Foundry test world with the module enabled and some background music playing.
  2. Afterwards: run `/dnd` twice, `/dnd ask`, and `/dnd-recap` into a copy of the campaign folder.
  3. Check: no music lyrics in the timeline, names mostly spelled correctly, the notes contain real facts, and the recap reads well.
  4. Record any fixes as follow-up tasks.
- [ ] **Step 4:** Commit with message `test: replay integration and rehearsal fixes`. Update the vault project note [[D&D table assistant]] with its status.
