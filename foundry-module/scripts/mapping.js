// Turn Foundry v14 / dnd5e 6.0.6 data into hub events: {kind, speaker, text, data, ts}.
// Field paths are documented in tests/fixtures/foundry/NOTES.md.

const STYLE = { 1: "ooc", 2: "ic", 3: "emote" }; // CONST.CHAT_MESSAGE_STYLES; 0 = OTHER

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", "#39": "'" };

export function stripHtml(html) {
  return String(html ?? "")
    .replace(/<[^>]*>/g, " ")
    .replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, code) => {
      if (code[0] === "#") {
        const n = code[1] === "x" || code[1] === "X" ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
        return Number.isFinite(n) ? String.fromCodePoint(n) : m;
      }
      return ENTITIES[code.toLowerCase()] ?? m;
    })
    .replace(/\s+/g, " ")
    .trim();
}

function isoTime(ms) {
  return Number.isFinite(ms) ? new Date(ms).toISOString() : new Date().toISOString();
}

function rollType(msg, roll) {
  if (msg.flags?.core?.initiativeRoll || msg.system?.type === "initiative") return "initiative";
  const t = roll.options?.rollType;
  if (t === "check") return msg.system?.skill ? "skill" : "check";
  if (["attack", "damage", "save"].includes(t)) return t;
  return "other";
}

function dice(roll) {
  return (roll.terms ?? [])
    .filter((t) => Number.isFinite(t.faces))
    .flatMap((t) => (t.results ?? []).filter((r) => r.active !== false).map((r) => `d${t.faces}=${r.result}`))
    .join(",");
}

function critFumble(roll) {
  if (roll.class === "DamageRoll") return { crit: Boolean(roll.options?.isCritical), fumble: false };
  const d20 = (roll.terms ?? []).find((t) => t.faces === 20);
  if (!d20) return { crit: false, fumble: false };
  const active = (d20.results ?? []).filter((r) => r.active !== false).map((r) => r.result);
  const critAt = d20.options?.criticalSuccess ?? roll.options?.criticalSuccess ?? 20;
  const fumbleAt = d20.options?.criticalFailure ?? roll.options?.criticalFailure ?? 1;
  return { crit: active.some((r) => r >= critAt), fumble: active.some((r) => r <= fumbleAt) };
}

/**
 * @param msg   message.toObject()
 * @param rolls message.rolls.map(r => r.toJSON())
 * @param alias the live message.alias (speaker.alias is null for out-of-character messages)
 */
export function mapChatMessage(msg, rolls = [], alias = null) {
  const speaker = msg.speaker?.alias || alias || "Unknown";
  const ts = isoTime(msg.timestamp);
  const events = [];

  if (rolls.length) {
    for (const roll of rolls) {
      const type = rollType(msg, roll);
      const label = type === "initiative" ? "Initiative" : stripHtml(msg.flavor) || msg.system?.item?.name || roll.formula;
      events.push({
        kind: "roll",
        speaker,
        text: "",
        data: { label, total: roll.total, formula: roll.formula, dice: dice(roll), roll_type: type, ...critFumble(roll) },
        ts,
      });
    }
    return events;
  }

  if (msg.type === "usage" && msg.system?.item?.name) {
    events.push({ kind: "chat", speaker, text: `uses ${msg.system.item.name}`, data: { style: "emote" }, ts });
    return events;
  }

  const text = stripHtml(msg.content);
  if (text) events.push({ kind: "chat", speaker, text, data: { style: STYLE[msg.style] ?? "ooc" }, ts });
  return events;
}

/** @param combat {round, turn, combatantName, started?, ended?} */
export function mapCombat(combat) {
  const ts = new Date().toISOString();
  if (combat.ended) return { kind: "combat", speaker: null, text: "Combat ended", data: { round: combat.round }, ts };
  if (!combat.started || !combat.round) return null;
  return {
    kind: "combat",
    speaker: null,
    text: `Round ${combat.round}: ${combat.combatantName}'s turn`,
    data: { round: combat.round, turn: combat.turn, combatant: combat.combatantName },
    ts,
  };
}

function* each(collection) {
  if (!collection) return;
  yield* collection.contents ?? collection;
}

/** A full state snapshot from the live actor's prepared data. */
export function mapActor(actor) {
  const hp = actor.system?.attributes?.hp ?? {};
  const resources = {};
  for (const item of each(actor.items)) {
    const uses = item.system?.uses;
    // Limited-use features (Moxie lives here); consumables such as rations are not resources.
    if (!uses?.max || (item.type && item.type !== "feat")) continue;
    resources[item.name] = { value: uses.value, max: uses.max };
  }
  return {
    kind: "state",
    speaker: actor.name,
    text: "",
    data: {
      actor: actor.name,
      hp: { value: hp.value ?? null, max: hp.max ?? null, temp: hp.temp ?? 0 },
      resources,
      conditions: Array.from(actor.statuses ?? []),
      effects: Array.from(each(actor.appliedEffects), (e) => e.name),
    },
    ts: new Date().toISOString(),
  };
}
