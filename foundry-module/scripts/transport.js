// Posts events to the hub on this PC. While the hub is unreachable, events wait in memory
// (oldest dropped past maxQueue) and are retried with backoff, in order.

const MAX_BACKOFF_MS = 30000;

export class Sender {
  constructor({ fetchFn, url, token, notify, maxQueue = 500, setTimeoutFn = setTimeout }) {
    this.fetchFn = fetchFn;
    this.url = url;
    this.token = token;
    this.notify = notify;
    this.maxQueue = maxQueue;
    this.setTimeoutFn = setTimeoutFn;
    this.queue = [];
    this.backoffMs = 1000;
    this.down = false;
    this.flushing = false;
    this.retryScheduled = false;
  }

  async send(ev) {
    this.queue.push(ev);
    if (this.queue.length > this.maxQueue) this.queue.splice(0, this.queue.length - this.maxQueue);
    // While a retry is pending, new events just wait their turn behind the queue.
    if (!this.retryScheduled) await this.flush();
  }

  async flush() {
    if (this.flushing) return;
    this.flushing = true;
    try {
      while (this.queue.length) {
        const result = await this.post(this.queue[0]);
        if (result === "retry") {
          this.scheduleRetry();
          return;
        }
        this.queue.shift(); // sent, or rejected as malformed (retrying would never succeed)
      }
      this.down = false;
      this.backoffMs = 1000;
    } finally {
      this.flushing = false;
    }
  }

  async post(ev) {
    let res;
    try {
      res = await this.fetchFn(this.url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Token": this.token },
        body: JSON.stringify(ev),
      });
    } catch (err) {
      this.outage("D&D Assistant: can't reach the hub on this PC; events are queued.");
      return "retry";
    }
    if (res.ok) return "sent";
    if (res.status === 401) {
      this.outage("D&D Assistant: the hub rejected the token; check the module's token setting.");
      return "retry";
    }
    if (res.status >= 500) {
      this.outage(`D&D Assistant: hub error ${res.status}; events are queued.`);
      return "retry";
    }
    console.warn("[dnd-assistant] hub rejected an event", res.status, ev);
    return "rejected";
  }

  outage(message) {
    if (!this.down) {
      this.down = true;
      this.notify(message);
    }
  }

  scheduleRetry() {
    if (this.retryScheduled) return;
    this.retryScheduled = true;
    const delay = this.backoffMs;
    this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
    // Call unbound: window.setTimeout throws "Illegal invocation" with any other `this`.
    const setTimeoutFn = this.setTimeoutFn;
    setTimeoutFn(async () => {
      this.retryScheduled = false;
      await this.flush();
    }, delay);
  }
}
