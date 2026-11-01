// Builds events.jsonl for the replay test: Foundry fixtures mapped by the real module code
// (foundry-module/scripts/mapping.js), interleaved with voice lines. Regenerate after changing
// the mapping:  node tests/fixtures/replay/build.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { mapActor, mapChatMessage, mapCombat } from "../../../foundry-module/scripts/mapping.js";

const fixture = (name) => JSON.parse(readFileSync(new URL(`../foundry/${name}.json`, import.meta.url), "utf8"));
const chat = (name) => {
  const f = fixture(name);
  return mapChatMessage(f.message, f.rolls, f.speakerAlias);
};
const voice = (speaker, text) => [{ source: "voice", kind: "speech", speaker, text, data: {} }];
const foundry = (events) => events.map((e) => ({ source: "foundry", ...e }));

const steps = [
  voice("Table", "The innkeeper of the Blue Water Inn says Ismark is looking for help."),
  foundry([mapActor(fixture("actor-snapshot").actor)]),
  voice("Me", "Paylon walks over and asks Ismark what he needs."),
  foundry(chat("chat-ic")),
  voice("Table", "Ismark says his sister Ireena was bitten twice and must be taken to Vallaki."),
  foundry(chat("chat-ooc")),
  foundry(chat("roll-skill")),
  foundry([mapCombat(fixture("combat-start"))]),
  foundry(chat("roll-initiative")),
  foundry([mapCombat(fixture("combat-turn"))]),
  foundry(chat("roll-attack")),
  foundry(chat("roll-damage")),
  foundry([mapActor(fixture("actor-hp").actor)]),
  foundry(chat("chat-emote")),
  foundry([mapActor(fixture("actor-resource").actor)]),
  foundry(chat("roll-save")),
  voice("Table", "Ismark promises 50 gold if you get Ireena to Vallaki safely."),
];

// Replace the module's wall-clock timestamps with a steady, reproducible session clock.
const start = Date.parse("2026-10-31T21:00:00-05:00");
const lines = steps.flat().map((e, i) => {
  const { ts, ...rest } = e;
  return JSON.stringify({ ...rest, ts: new Date(start + i * 30_000).toISOString() });
});
writeFileSync(new URL("./events.jsonl", import.meta.url), lines.join("\n") + "\n");
console.log(`wrote ${lines.length} events`);
