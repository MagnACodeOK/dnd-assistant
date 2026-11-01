---
name: dnd-recap
description: Write the session recap for the Curse of Strahd campaign from a recorded live session. Use for /dnd-recap, or when the user asks to write up, summarize or recap tonight's / the last D&D session. Optional argument - a live session folder path, and/or a campaign folder path (use a copy for testing).
---

# /dnd-recap: write the session note

Paths (an argument may override either):
- CAMPAIGN: `C:\Dropbox\Claude Vora\Claude_Vora\Curse of Strahd`
- SESSION: the given folder, or else the most recently modified folder in `CAMPAIGN\Live Sessions\`

## Inputs

Read in full:
- `SESSION\session-notes.md`: facts the local scribe extracted (uncertain ones end in "(?)")
- `SESSION\timeline.md`: everything said and rolled, in time order. Voice lines (`Table:`, `Me:`) are machine-transcribed: names may be misspelled, and Discord echoes of the user's own voice appear as both `Me` and `Table`.
- `CAMPAIGN\Session Notes\Yester Hill.md`: the **style reference** (frontmatter, numbered events with bold key facts, `[[links]]`, "Open threads", spoiler section only if the user already uses one)
- `CAMPAIGN\Curse of Strahd.md` (the `## Sections` list), `CAMPAIGN\Party.md`, and the file names in `CAMPAIGN\NPCs\` and `CAMPAIGN\Places\` (for links)

The session folder name gives the date (`YYYY-MM-DD`, maybe with a `-2` suffix).

## Output

Write `CAMPAIGN\Session Notes\YYYY-MM-DD - <Title>.md`. Pick a short, evocative title like the existing notes ("The Storm Mage", "Yester Hill"). Match the existing notes' frontmatter:

```
---
tags: [curse-of-strahd, session]
updated: YYYY-MM-DD
---
# <Title>

_Follows [[<previous session note>]]._
```

Then these sections:
1. **Summary**: numbered events in order, key facts in bold, written in the first person plural ("we") like the existing notes.
2. **NPCs met**: each as a `[[link]]` to its note in `NPCs/` or `Places/` when one exists (use `[[Note name|display]]` when the spoken name differs), with one line on what they did or said.
3. **Loot & XP**: items, money, XP or level changes. Say "none recorded" if nothing.
4. **Open threads**: unresolved questions, promises, leads.
5. **Questions Paylon may pursue**: apply the same in-character filter as `/dnd ask`. Use only in-character events Paylon was present for (speech, in-character chat, Paylon's own rolls), plus knowledge plausible from Paylon's background and skills. Exclude out-of-character table talk and rules discussion. Flag borderline voice-sourced items "(table talk?)".

Rules: record only what the session shows. Don't invent events or fill gaps from the published adventure. If the transcript is unclear, say so ("unclear from the recording").

## Then

1. In `CAMPAIGN\Curse of Strahd.md`, under `## Sections`, add a line for the new note right after the current "← latest" entry, in the same style (`- [[Title]]: one-line hook ← latest`), and remove "← latest" from the old entry. Change nothing else in that file.
2. Append new proper names heard this session (NPCs, places, items) to `CAMPAIGN\glossary.md`, one per line, skipping names already in it, in `NPCs/` or in `Places/`. Create the file if it doesn't exist. These help Whisper spell names correctly next time.
3. **List** the NPCs and places that came up but have no note in `NPCs/` or `Places/`. **Do not create those notes unless the user says yes.**
4. Tell the user the note's path and show its Summary.
