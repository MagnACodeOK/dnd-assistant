# Foundry v14 (build 368) / dnd5e 6.0.6 fixtures

Captured 2026-10-31 with the probe module (v0.0.1 and v0.0.2) from the user's **player** client. Raw captures (`raw-capture*.json`) stay local and are git-ignored, because they hold the full character sheet and other players' character data. The files here are trimmed, one per case.

Files whose top level has a `_synthetic` key were derived by hand from a real capture. The key says what was changed.

## Shapes

- **Chat and roll fixtures:** `{hook: "createChatMessage", at, message, rolls, visible, speakerAlias}`.
  - `message` is `message.toObject()`.
  - `rolls` is `message.rolls.map(r => r.toJSON())`.
  - `speakerAlias` is the live `message.alias` getter. It isn't in `toObject()`, and it's the only place the user's name appears for out-of-character messages, where `speaker.alias` is `null`.
- **Combat fixtures:** `{hook: "updateCombat", changed, round, turn, combatantName, started}`. These are read from the live `combat` and `combat.combatant?.name`.
- **Actor fixtures:** `actor` is an **actor view**, shaped like the live actor's *prepared* data: `{name, system.attributes.hp, statuses, appliedEffects[].name, items[].system.uses{value,max,spent}}`. `toObject()` is source data and lacks prepared values (Moxie's max is the formula `@scale.pugilist.moxie` there), so the mapper must read the live document.

## Field paths

### In-character, out-of-character and emote style
`message.style`, as a number from `CONST.CHAT_MESSAGE_STYLES = {OTHER: 0, OOC: 1, IC: 2, EMOTE: 3}`, confirmed in the console.
- Rolls and usage cards are `0` (OTHER).
- `message.emote` was `false` even on a real emote, so ignore it.
- In testing, plain chat posted as the user (style 1, `speaker.alias` null), even with Paylon's token selected. `/ic` isn't a chat command in v14. `chat-ic.json` is therefore synthetic.
- An emote posts as whatever token is controlled. The first test posted as "Player Map Marker". With no token controlled, it falls back to the user's character.

### Speaker
`message.speaker.alias`, falling back to `speakerAlias` (the live `message.alias`).

### Roll type and label
- `message.type` is the dnd5e message subtype: `check`, `save`, `attack`, `damage`, `usage`, or `base` for plain chat.
- `rolls[i].options.rollType` is `check`, `save`, `attack` or `damage`. It's **absent on initiative rolls**.
- Initiative: `message.flags.core.initiativeRoll === true`, and `message.system.type === "initiative"`.
- Skill: `message.system.skill` (for example `"ath"`), plus `message.system.ability`.
- Save: `message.system.ability`.
- Label: `message.flavor`, which is human-readable. Examples: `"Strength (Athletics) Check"`, `"Strength Saving Throw"`, `"Blood Spear - Attack Roll"`, `"Blood Spear - Damage Roll"`, `"Paylon rolls for Initiative!"`.
- Item: `message.system.item.name`, on attack, damage and usage cards.
- `usage` messages, such as Haymaker or using a weapon, carry no rolls. `message.system.deltas.item[<itemId>]` lists the resources they consumed, for example Moxie `system.uses.spent +1`.

### Total and dice
- `rolls[i].total` and `rolls[i].formula`.
- Dice are the terms with a `faces` number. The d20 class is `"Die"`; dnd5e damage dice are `"BasicDie"`. Each die's values are in `terms[j].results[k].result`, and only results with `active: true` count. Advantage drops a result by marking it inactive.

### Critical and fumble markers
- **d20 rolls:** the die term's `options.criticalSuccess` (20) and `options.criticalFailure` (1) are the thresholds; `rolls[i].options` has the same keys. A crit is an active d20 result ≥ `criticalSuccess`, and a fumble is one ≤ `criticalFailure`. Neither occurred in testing: `roll-attack-crit.json` and `roll-attack-fumble.json` are synthetic.
- **Damage:** `rolls[i].options.isCritical`. `roll-damage-crit.json` is synthetic.

### HP path
`system.attributes.hp.{value, max, temp}`. `temp` can be `null` or `0`. An `updateActor` change set carries `system.attributes.hp.value`. Many `updateActor` events are noise, such as `flags.dnd5e.lastMovedToken` for every party member on a scene change, so filter by the actor and by `hp`.

### Resource paths
Paylon's `system.resources.{primary,secondary,tertiary}` are all unused (0/0). **Moxie is a feat item named "Moxie":**
- read the prepared `item.system.uses.value` and `.max` (for example 2/4)
- spending it fires **`updateItem`**, with `changed.system.uses.spent`, not `updateActor`

Other limited-use features show up the same way: Bloodied But Unbowed, Swagger Streak, Savage Attacker. So do consumables such as Rations and Waterskin, so the mapper should limit "resources" to `feat` items, or to a named list.

### Conditions and effects
- Conditions: `actor.statuses`, a Set of ids such as `"prone"`, kept current.
- A condition toggle fires `createActiveEffect` or `deleteActiveEffect` on the actor, with `effect.statuses` (for example `["prone"]`) and `effect.name` (`"Prone"`).
- Active effects: `actor.appliedEffects[].name`. The snapshot shows `Iron Chin` and `Cloak of Protection` as passive item effects, plus `Prone` once applied.
- A player client also receives effect hooks for **other** party members (for example "Falling" on Liam Shieldheart), so filter by `effect.parent === game.user.character`.

### Combat
- `updateCombat` fires on round or turn changes.
- `changed` holds `round` and/or `turn`; the live `combat.combatant?.name` names whose turn it is.
- Starting combat appears as `changed.round: 1, turn: 0` with `started: true`. `combatStart` did **not** fire on the player client; it's local to the GM who clicked.

### Hooks
`createChatMessage`, `updateCombat`, `updateActor`, `updateItem`, `createActiveEffect` and `deleteActiveEffect` all fire on the player client. dnd5e 6 roll hooks (`dnd5e.rollAttackV2`, `dnd5e.rollSkillV2`, …) also exist, but the module doesn't need them: every roll arrives as a chat message.

### Chat card HTML
In dnd5e 6, `message.content` is **empty** for attack, damage, save and check cards; the card is rendered on the client. Initiative messages have the total as their content (for example `"15"`). Plain chat content is HTML, such as `<p>Hello there</p>`, so it still needs stripping.
