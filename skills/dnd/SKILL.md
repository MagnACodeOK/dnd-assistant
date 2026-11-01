---
name: dnd
description: Live help for Paylon during a Curse of Strahd session. Use for /dnd, for any rules or tactics question about Paylon during play ("can I grapple while prone?", "what should I do this turn?", "what was that innkeeper's name?"), and for "/dnd ask" or "what should Paylon ask?" (in-character questions for NPCs).
---

# /dnd: Paylon's table assistant

The user plays **Paylon** (Pugilist 6, Squared Circle, grappler) in a D&D 2024 *Curse of Strahd* game. A capture program records the table's voice and Foundry events while they play. Answer fast: they are mid-game.

Paths:
- Campaign: `C:\Dropbox\Claude Vora\Claude_Vora\Curse of Strahd` (call it CAMPAIGN)
- Live session: the most recently modified folder in `CAMPAIGN\Live Sessions\`
- CLI: `uv run --project D:\Claude\dnd-assistant dnd-assistant <command>`

## Steps

1. Read, once per conversation (reuse what you already read; re-read only the live files):
   - `CAMPAIGN\My Character\Paylon cheat sheet.md`
   - `CAMPAIGN\Party.md`: Paylon's row in the party table and the "Teamwork with Paylon" section
   - the `## GM rulings` section of `CAMPAIGN\Curse of Strahd.md`
   - from the latest live session folder, every time: `paylon-now.md` (HP, Moxie, conditions, effects) and `session-notes.md` (people, places, clues so far)
2. Run `uv run --project D:\Claude\dnd-assistant dnd-assistant tail --minutes 10` for what just happened.
3. For anything earlier, run `uv run --project D:\Claude\dnd-assistant dnd-assistant search <words>`, or grep the session's `timeline.md`.

Timeline lines look like `21:14:05 Table: "…"` (table voice from Discord), `Me: "…"` (the user's mic), `[IC]/[OOC]/[EMOTE] Name: "…"` (Foundry chat), `🎲` rolls, `⚔` combat turns. Voice text is machine-transcribed: names may be misspelled.

## Answer rules

- Best option first. At most 3 bullets.
- Name the rule or feature behind each claim (for example "Grappled condition", "Moxie: Haymaker", "PHB 2024 Prone").
- GM rulings override the rules as written.
- If the ruling is uncertain or depends on the DM, say "ask the DM" instead of guessing.
- Use Paylon's current state from `paylon-now.md` (HP, Moxie left, conditions).
- No preamble, no recap of the question.

## `/dnd ask`: questions Paylon might ask

Suggest questions Paylon might ask, with an **in-character knowledge filter**:

- It uses only in-character events Paylon was present for: speech, in-character chat, and Paylon's own rolls. Out-of-character table talk and rules discussion are excluded.
- It also uses knowledge plausible from Paylon's background and skills, taken from `Party.md` and the cheat sheet.
- Each suggestion is phrased as Paylon speaking to an NPC or the party, with one line on why Paylon would ask it.
- Voice lines with no in-character or out-of-character marker are judged from context, and borderline ones are flagged.

Output up to 5 suggestions, each as:

> **Paylon:** "…"
> *Why Paylon would ask:* …

Mark any suggestion that rests on a borderline voice line with **(table talk?)**. Never use what the user knows from outside the game (for example the published adventure).
