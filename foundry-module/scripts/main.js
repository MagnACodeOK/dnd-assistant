// D&D Table Assistant: sends this client's chat, rolls, combat turns and your character's
// state to the assistant running on your own PC. Does nothing unless "Send to my PC" is on.
import { mapActor, mapChatMessage, mapCombat } from "./mapping.js";
import { Sender } from "./transport.js";

const ID = "dnd-assistant";
const STATE_DEBOUNCE_MS = 300;

Hooks.once("init", () => {
  game.settings.register(ID, "enabled", {
    name: game.i18n.localize("DNDA.enabled.name"),
    hint: game.i18n.localize("DNDA.enabled.hint"),
    scope: "client",
    config: true,
    type: Boolean,
    default: false,
    requiresReload: true,
  });
  game.settings.register(ID, "port", {
    name: game.i18n.localize("DNDA.port.name"),
    hint: game.i18n.localize("DNDA.port.hint"),
    scope: "client",
    config: true,
    type: Number,
    default: 8765,
    requiresReload: true,
  });
  game.settings.register(ID, "token", {
    name: game.i18n.localize("DNDA.token.name"),
    hint: game.i18n.localize("DNDA.token.hint"),
    scope: "client",
    config: true,
    type: String,
    default: "",
    requiresReload: true,
  });
});

Hooks.once("ready", () => {
  if (!game.settings.get(ID, "enabled")) return;

  const sender = new Sender({
    fetchFn: (url, init) => fetch(url, init),
    url: `http://127.0.0.1:${game.settings.get(ID, "port")}/event`,
    token: game.settings.get(ID, "token"),
    notify: (message) => ui.notifications.warn(message),
  });
  const send = (ev) => {
    if (ev) sender.send(ev);
  };

  // Many document updates arrive in bursts (a scene change updates every actor), so the
  // character's state is sent once things settle.
  let stateTimer = null;
  const sendState = () => {
    clearTimeout(stateTimer);
    stateTimer = setTimeout(() => {
      const character = game.user.character;
      if (character) send(mapActor(character));
    }, STATE_DEBOUNCE_MS);
  };
  const isMine = (actor) => actor && game.user.character && actor.id === game.user.character.id;

  if (game.user.character) send(mapActor(game.user.character));
  else ui.notifications.warn(game.i18n.localize("DNDA.noCharacter"));

  Hooks.on("createChatMessage", (message) => {
    // Foundry only delivers what this user may see; skip anything it still marks hidden.
    if (!message.visible || message.isContentVisible === false) return;
    const rolls = (message.rolls ?? []).map((r) => r.toJSON());
    for (const ev of mapChatMessage(message.toObject(), rolls, message.alias)) send(ev);
  });

  Hooks.on("updateCombat", (combat, changed) => {
    if (!("round" in changed) && !("turn" in changed)) return;
    send(mapCombat({ round: combat.round, turn: combat.turn, combatantName: combat.combatant?.name, started: combat.started }));
  });
  Hooks.on("deleteCombat", (combat) => {
    if (combat.started) send(mapCombat({ round: combat.round, turn: combat.turn, ended: true }));
  });

  Hooks.on("updateActor", (actor) => {
    if (isMine(actor)) sendState();
  });
  Hooks.on("updateItem", (item) => {
    if (isMine(item.parent)) sendState();
  });
  for (const hook of ["createActiveEffect", "deleteActiveEffect", "updateActiveEffect"]) {
    Hooks.on(hook, (effect) => {
      if (isMine(effect.parent)) sendState();
    });
  }
});
