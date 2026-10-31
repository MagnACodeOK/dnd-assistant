// THROWAWAY SPIKE (Task 3): records the raw data this client receives, so the real
// mapping can be written against Foundry v14 / dnd5e 6.0.6 fixtures. Replaced by main.js.
// In DevTools afterwards: copy(JSON.stringify(window.__dndProbe, null, 1))

const probe = (window.__dndProbe = []);
const hookNames = (window.__dndProbeHooks = new Set());

function record(hook, payload) {
  const item = { hook, at: new Date().toISOString(), ...payload };
  try {
    // Deep copy now, so later document changes don't rewrite what we captured.
    probe.push(JSON.parse(JSON.stringify(item)));
  } catch (err) {
    probe.push({ hook, at: item.at, error: String(err) });
  }
  console.debug("[dnd-probe]", hook, item);
}

function actorSummary(actor) {
  return {
    actorName: actor?.name,
    hp: actor?.system?.attributes?.hp,
    statuses: Array.from(actor?.statuses ?? []),
    effects: (actor?.appliedEffects ?? []).map((e) => e.name),
    // Prepared (not source) values: limited-use features such as Moxie live here.
    uses: (actor?.items ?? [])
      .filter((i) => i.system?.uses?.max)
      .map((i) => ({ name: i.name, value: i.system.uses.value, max: i.system.uses.max, spent: i.system.uses.spent })),
  };
}

// Note the name of every dnd5e.* hook that fires, so we learn the roll hooks' real names.
for (const fn of ["call", "callAll"]) {
  const original = Hooks[fn];
  Hooks[fn] = function (name, ...args) {
    if (typeof name === "string" && name.startsWith("dnd5e.")) hookNames.add(name);
    return original.call(this, name, ...args);
  };
}

Hooks.on("createChatMessage", (message) => {
  record("createChatMessage", {
    message: message.toObject(),
    rolls: (message.rolls ?? []).map((r) => r.toJSON()),
    visible: message.visible,
    speakerAlias: message.alias,
  });
});

Hooks.on("updateCombat", (combat, changed) => {
  record("updateCombat", {
    changed,
    round: combat.round,
    turn: combat.turn,
    combatantName: combat.combatant?.name,
    started: combat.started,
  });
});

for (const hook of ["combatStart", "deleteCombat"]) {
  Hooks.on(hook, (combat) => {
    record(hook, { round: combat.round, turn: combat.turn, combatantName: combat.combatant?.name });
  });
}

Hooks.on("updateActor", (actor, changed) => {
  record("updateActor", { changed, ...actorSummary(actor) });
});

Hooks.on("updateItem", (item, changed) => {
  record("updateItem", { itemName: item.name, changed, ...actorSummary(item.parent) });
});

for (const hook of ["createActiveEffect", "deleteActiveEffect"]) {
  Hooks.on(hook, (effect) => {
    record(hook, { effect: effect.toObject(), ...actorSummary(effect.parent) });
  });
}

Hooks.once("ready", () => {
  const character = game.user.character;
  if (!character) {
    ui.notifications.warn("dnd-probe: no character is assigned to this user.");
    return;
  }
  record("snapshot", { actor: character.toObject(), ...actorSummary(character) });
  console.info("[dnd-probe] ready; capturing into window.__dndProbe");
});
