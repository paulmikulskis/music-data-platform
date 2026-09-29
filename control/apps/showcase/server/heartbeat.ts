import "server-only";
import { z } from "zod";
import type { Person } from "@mdp/showcase-auth";
import { events, type Event, type Events } from "./platform";
import { budget } from "./read-budget";
import { createPlatformEventReader } from "./platform-events";
export type PulseEvent = Event & { id: string };
type Listener = {
  person: Person;
  send: (
    event: PulseEvent | null,
    online: boolean,
    runner: Events["runner"],
  ) => void;
};
const checkpointSchema = z.object({
  key: z.string(),
  cursor: z.string().optional(),
});
function resume(id: string) {
  try {
    return checkpointSchema.parse(
      JSON.parse(Buffer.from(id, "base64url").toString()),
    );
  } catch {
    return null;
  }
}
export class Heartbeat {
  private listeners = new Set<Listener>();
  private history: PulseEvent[] = [];
  private read?: ReturnType<typeof createPlatformEventReader>;
  private readerPerson?: Person;
  private after?: string;
  private checkedAt = 0;
  private online = false;
  private runner: Events["runner"] = {
    state: "unknown",
    busy: true,
    sessions: [],
    next_scheduled_at: null,
    next_step: "Open /status.",
  };
  private timer?: ReturnType<typeof setTimeout>;
  private flight?: Promise<void>;
  subscribe(listener: Listener, after: string) {
    this.listeners.add(listener);
    listener.send(null, this.online, this.runner);
    const checkpoint = resume(after);
    const index = checkpoint
      ? this.history.findIndex((event) => event.key === checkpoint.key)
      : -1;
    // A new tab needs the current state; only a reconnect asks for saved events.
    if (checkpoint)
      for (const event of this.history.slice(index + 1))
        listener.send(event, true, this.runner);
    // A fresh instance resumes the API overlap window carried by the last delivered event.
    if (!this.checkedAt && checkpoint?.cursor) this.after = checkpoint.cursor;
    void this.refresh(listener.person);
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) {
        clearTimeout(this.timer);
        this.timer = undefined;
      }
    };
  }
  private schedule() {
    clearTimeout(this.timer);
    if (this.listeners.size)
      this.timer = setTimeout(() => {
        const next = this.listeners.values().next().value;
        if (next) void this.refresh(next.person);
      }, 10000);
  }
  refresh(person: Person) {
    if (this.flight) return this.flight;
    if (Date.now() - this.checkedAt < 9500) {
      this.schedule();
      return Promise.resolve();
    }
    this.checkedAt = Date.now();
    clearTimeout(this.timer);
    this.readerPerson = person;
    this.read ??= createPlatformEventReader(
      (input) => events(this.readerPerson!, input.after),
      this.after,
    );
    this.flight = (async () => {
      try {
        // Pages share the same poller and admission gate. The next tick continues a large backlog.
        for (let page = 0; page < 10; page++) {
          const previous = this.after;
          const result = await this.read!(500);
          this.online = true;
          this.runner = result.runner;
          budget.setRunner(result.runner.state);
          for (const event of result.events) {
            const pulse = {
              ...event,
              id: Buffer.from(
                JSON.stringify({ cursor: previous, key: event.key }),
              ).toString("base64url"),
            };
            this.history.push(pulse);
            for (const listener of this.listeners)
              listener.send(pulse, true, this.runner);
          }
          this.after = result.next_cursor;
          this.history = this.history.slice(-500);
          if (!result.has_more || result.runner.state !== "idle") break;
        }
        for (const listener of this.listeners)
          listener.send(null, true, this.runner);
      } catch {
        this.online = false;
        this.runner = {
          ...this.runner,
          state: "unknown",
          busy: true,
          next_scheduled_at: null,
        };
        budget.setRunner("unknown");
        for (const listener of this.listeners)
          listener.send(null, false, this.runner);
      }
    })().finally(() => {
      this.flight = undefined;
      this.schedule();
    });
    return this.flight;
  }
}
export const heartbeat = (globalThis.showcaseHeartbeat ??= new Heartbeat());

declare global {
  var showcaseHeartbeat: Heartbeat | undefined;
}
