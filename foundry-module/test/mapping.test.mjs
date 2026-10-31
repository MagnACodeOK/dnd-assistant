import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mapActor, mapChatMessage, mapCombat, stripHtml } from "../scripts/mapping.js";

const fixture = (name) =>
  JSON.parse(readFileSync(new URL(`../../tests/fixtures/foundry/${name}.json`, import.meta.url), "utf8"));

const chat = (name) => {
  const f = fixture(name);
  return mapChatMessage(f.message, f.rolls, f.speakerAlias);
};

const only = (events, kind) => {
  const matching = events.filter((e) => e.kind === kind);
  assert.equal(matching.length, 1, `expected one ${kind} event, got ${JSON.stringify(events)}`);
  return matching[0];
};

test("chat-ic", () => {
  const ev = only(chat("chat-ic"), "chat");
  assert.equal(ev.speaker, "Paylon");
  assert.equal(ev.text, "Hello there");
  assert.equal(ev.data.style, "ic");
});

test("chat-ooc uses the live alias when speaker.alias is null", () => {
  const ev = only(chat("chat-ooc"), "chat");
  assert.equal(ev.speaker, "Wayne");
  assert.equal(ev.text, "Rules check");
  assert.equal(ev.data.style, "ooc");
});

test("chat-emote", () => {
  const ev = only(chat("chat-emote"), "chat");
  assert.equal(ev.speaker, "Paylon");
  assert.equal(ev.data.style, "emote");
  assert.equal(ev.text, "Paylon cracks his knuckles");
});

test("chat events carry Foundry's timestamp", () => {
  const f = fixture("chat-ooc");
  const [ev] = mapChatMessage(f.message, f.rolls, f.speakerAlias);
  assert.equal(ev.ts, new Date(f.message.timestamp).toISOString());
});

function rollCase(name, expected) {
  test(name, () => {
    const f = fixture(name);
    const events = mapChatMessage(f.message, f.rolls, f.speakerAlias);
    assert.equal(events.filter((e) => e.kind === "chat").length, 0);
    const ev = only(events, "roll");
    assert.equal(ev.speaker, "Paylon");
    assert.equal(ev.data.total, f.rolls[0].total);
    assert.equal(ev.data.formula, f.rolls[0].formula);
    for (const [key, value] of Object.entries(expected)) assert.deepEqual(ev.data[key], value, key);
  });
}

rollCase("roll-skill", { label: "Strength (Athletics) Check", dice: "d20=3", roll_type: "skill", crit: false, fumble: false });
rollCase("roll-attack", { label: "Blood Spear - Attack Roll", dice: "d20=13", roll_type: "attack", crit: false, fumble: false });
rollCase("roll-damage", { label: "Blood Spear - Damage Roll", dice: "d10=10", roll_type: "damage", crit: false });
rollCase("roll-save", { label: "Strength Saving Throw", dice: "d20=4", roll_type: "save" });
rollCase("roll-initiative", { label: "Initiative", dice: "d20=15", roll_type: "initiative" });
rollCase("roll-attack-crit", { dice: "d20=20", crit: true, fumble: false });
rollCase("roll-attack-fumble", { dice: "d20=1", crit: false, fumble: true });
rollCase("roll-damage-crit", { crit: true });

test("roll-attack: no emitted text contains HTML", () => {
  for (const ev of chat("roll-attack")) {
    assert.ok(!ev.text.includes("<"));
    assert.ok(!String(ev.data.label ?? "").includes("<"));
  }
});

test("advantage: only the active d20 counts", () => {
  const f = fixture("roll-attack");
  const die = f.rolls[0].terms[0];
  die.number = 2;
  die.results = [{ result: 20, active: false, discarded: true }, { result: 5, active: true }];
  const ev = only(mapChatMessage(f.message, f.rolls, f.speakerAlias), "roll");
  assert.equal(ev.data.dice, "d20=5");
  assert.equal(ev.data.crit, false);
});

test("usage card becomes an emote line naming the feature", () => {
  const msg = {
    ...fixture("chat-emote").message,
    style: 0,
    type: "usage",
    content: "",
    system: { item: { name: "Haymaker" } },
  };
  const ev = only(mapChatMessage(msg, [], "Paylon"), "chat");
  assert.equal(ev.text, "uses Haymaker");
  assert.equal(ev.data.style, "emote");
});

test("combat-turn", () => {
  const f = fixture("combat-turn");
  const ev = mapCombat(f);
  assert.equal(ev.kind, "combat");
  assert.equal(ev.text, "Round 1: Paylon's turn");
  assert.deepEqual(ev.data, { round: 1, turn: 2, combatant: "Paylon" });
});

test("combat-start", () => {
  assert.equal(mapCombat(fixture("combat-start")).text, "Round 1: Count Strahd von Zarovich's turn");
});

test("combat not started or ended", () => {
  assert.equal(mapCombat({ round: 0, turn: 0, combatantName: null, started: false }), null);
  assert.equal(mapCombat({ round: 3, turn: 1, combatantName: "Paylon", ended: true }).text, "Combat ended");
});

test("actor-snapshot", () => {
  const ev = mapActor(fixture("actor-snapshot").actor);
  assert.equal(ev.kind, "state");
  assert.equal(ev.speaker, "Paylon");
  assert.equal(ev.data.actor, "Paylon");
  assert.deepEqual(ev.data.hp, { value: 50, max: 58, temp: 0 });
  assert.deepEqual(ev.data.resources.Moxie, { value: 2, max: 4 });
  assert.deepEqual(ev.data.conditions, ["prone"]);
  assert.ok(ev.data.effects.includes("Iron Chin"));
});

test("actor-hp", () => {
  const ev = mapActor(fixture("actor-hp").actor);
  assert.deepEqual(ev.data.hp, { value: 50, max: 58, temp: 0 });
});

test("actor-resource shows Moxie after spending", () => {
  assert.deepEqual(mapActor(fixture("actor-resource").actor).data.resources.Moxie, { value: 1, max: 4 });
});

test("actor-condition", () => {
  assert.deepEqual(mapActor(fixture("actor-condition").actor).data.conditions, ["prone"]);
});

test("resources skip consumables when item types are known", () => {
  const actor = fixture("actor-snapshot").actor;
  actor.items = [
    { name: "Moxie", type: "feat", system: { uses: { value: 2, max: 4 } } },
    { name: "Rations", type: "consumable", system: { uses: { value: 1, max: 1 } } },
  ];
  assert.deepEqual(Object.keys(mapActor(actor).data.resources), ["Moxie"]);
});

test("live documents: statuses Set and items collection", () => {
  const actor = fixture("actor-snapshot").actor;
  actor.statuses = new Set(["prone", "grappled"]);
  const items = actor.items;
  actor.items = { contents: items, [Symbol.iterator]: () => items[Symbol.iterator]() };
  const ev = mapActor(actor);
  assert.deepEqual(ev.data.conditions, ["prone", "grappled"]);
  assert.ok("Moxie" in ev.data.resources);
});

test("stripHtml", () => {
  assert.equal(stripHtml("<p>Hello\n<b>there</b></p>&amp; you&#39;re&nbsp;ok"), "Hello there & you're ok");
  assert.equal(stripHtml(null), "");
});
