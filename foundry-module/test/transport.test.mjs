import { test } from "node:test";
import assert from "node:assert/strict";
import { Sender } from "../scripts/transport.js";

/** A fetch that fails while `down` is true, and records every attempt. */
function fakeNetwork() {
  const net = { down: false, status: 202, calls: [] };
  net.fetchFn = async (url, init) => {
    net.calls.push({ url, init, body: JSON.parse(init.body) });
    if (net.down) throw new TypeError("Failed to fetch");
    return { ok: net.status < 300, status: net.status };
  };
  return net;
}

/** Captures scheduled retries instead of waiting for them. */
function fakeTimers() {
  const t = { pending: [] };
  t.setTimeoutFn = (fn, ms) => t.pending.push({ fn, ms });
  t.fire = async () => {
    const { fn } = t.pending.shift();
    await fn();
  };
  return t;
}

function makeSender(net, timers, extra = {}) {
  const notes = [];
  const sender = new Sender({
    fetchFn: net.fetchFn,
    url: "http://127.0.0.1:8765/event",
    token: "tok",
    notify: (m) => notes.push(m),
    setTimeoutFn: timers.setTimeoutFn,
    ...extra,
  });
  return { sender, notes };
}

const ev = (n) => ({ kind: "chat", speaker: "Paylon", text: `m${n}`, data: { style: "ic" }, ts: "x" });

test("sender sets X-Token header and posts JSON", async () => {
  const net = fakeNetwork();
  const { sender } = makeSender(net, fakeTimers());
  await sender.send(ev(1));
  const { url, init, body } = net.calls[0];
  assert.equal(url, "http://127.0.0.1:8765/event");
  assert.equal(init.method, "POST");
  assert.equal(init.headers["X-Token"], "tok");
  assert.equal(init.headers["Content-Type"], "application/json");
  assert.equal(body.text, "m1");
});

test("sender queues when down and flushes in order", async () => {
  const net = fakeNetwork();
  const timers = fakeTimers();
  const { sender } = makeSender(net, timers);
  net.down = true;
  await sender.send(ev(1));
  await sender.send(ev(2));
  await sender.send(ev(3));
  assert.equal(sender.queue.length, 3);
  net.down = false;
  net.calls.length = 0;
  await timers.fire();
  assert.deepEqual(net.calls.map((c) => c.body.text), ["m1", "m2", "m3"]);
  assert.equal(sender.queue.length, 0);
});

test("sender drops oldest over 500", async () => {
  const net = fakeNetwork();
  const { sender } = makeSender(net, fakeTimers());
  net.down = true;
  for (let i = 0; i < 503; i++) await sender.send(ev(i));
  assert.equal(sender.queue.length, 500);
  assert.equal(sender.queue[0].text, "m3");
  assert.equal(sender.queue.at(-1).text, "m502");
});

test("sender notifies once per outage", async () => {
  const net = fakeNetwork();
  const timers = fakeTimers();
  const { sender, notes } = makeSender(net, timers);
  net.down = true;
  await sender.send(ev(1));
  await sender.send(ev(2));
  await timers.fire();
  assert.equal(notes.length, 1);
  net.down = false;
  await timers.fire();
  await sender.send(ev(3));
  net.down = true;
  await sender.send(ev(4));
  assert.equal(notes.length, 2);
});

test("sender backs off 1, 2, 4 … 30 s", async () => {
  const net = fakeNetwork();
  const timers = fakeTimers();
  const { sender } = makeSender(net, timers);
  net.down = true;
  await sender.send(ev(1));
  const delays = [];
  for (let i = 0; i < 7; i++) {
    delays.push(timers.pending[0].ms);
    await timers.fire();
  }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 16000, 30000, 30000]);
  assert.equal(timers.pending.length, 1, "one retry scheduled at a time");
});

test("only one retry is scheduled while sends keep arriving during an outage", async () => {
  const net = fakeNetwork();
  const timers = fakeTimers();
  const { sender } = makeSender(net, timers);
  net.down = true;
  for (let i = 0; i < 5; i++) await sender.send(ev(i));
  assert.equal(timers.pending.length, 1);
});

test("a rejected event (400) is dropped, not retried forever", async () => {
  const net = fakeNetwork();
  const timers = fakeTimers();
  const { sender } = makeSender(net, timers);
  net.status = 400;
  await sender.send(ev(1));
  assert.equal(sender.queue.length, 0);
  assert.equal(timers.pending.length, 0);
});

test("a wrong token (401) is an outage with its own notice", async () => {
  const net = fakeNetwork();
  const timers = fakeTimers();
  const { sender, notes } = makeSender(net, timers);
  net.status = 401;
  await sender.send(ev(1));
  assert.equal(sender.queue.length, 1);
  assert.match(notes[0], /token/i);
});
