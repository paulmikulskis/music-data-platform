import { errorStatus } from "./unknown.js";
// One process owns the budget. Keep the deployed app at one machine.
export type Weight = "heavy" | "light" | "essential";
export type Cached<T> = {
  value: T;
  savedAt: string;
  state: "live" | "cached" | "busy";
};
export class ReadBudget {
  private active = { heavy: 0, light: 0 };
  private bucket(weight: Weight) {
    return weight === "essential" ? "light" : weight;
  }
  private sessions = new Map<string, number>();
  private queue: {
    weight: Weight;
    session?: string | undefined;
    resolve: (release: () => void) => void;
    reject: (e: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }[] = [];
  private cachedKeys = new Map<string, () => void>();
  private runner: { state: "idle" | "busy" | "unknown"; at: number } = {
    state: "unknown",
    at: 0,
  };
  constructor(
    private waitMs = 3000,
    private capacity = 20,
  ) {}
  setRunner(state: "idle" | "busy" | "unknown") {
    this.runner = { state, at: Date.now() };
  }
  get runnerBusy() {
    return this.runnerState !== "idle";
  }
  get runnerState() {
    return Date.now() - this.runner.at > 25000 ? "unknown" : this.runner.state;
  }
  private available(weight: Weight, session?: string) {
    return (
      this.active[this.bucket(weight)] <
        (weight === "heavy" ? 4 : weight === "essential" ? 8 : 6) &&
      (!session || (this.sessions.get(session) ?? 0) < 2)
    );
  }
  private take(weight: Weight, session?: string) {
    this.active[this.bucket(weight)]++;
    if (session)
      this.sessions.set(session, (this.sessions.get(session) ?? 0) + 1);
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.active[this.bucket(weight)]--;
      if (session) {
        const n = (this.sessions.get(session) ?? 1) - 1;
        if (n) this.sessions.set(session, n);
        else this.sessions.delete(session);
      }
      for (const pending of [...this.queue]) {
        if (!this.available(pending.weight, pending.session)) continue;
        this.queue.splice(this.queue.indexOf(pending), 1);
        clearTimeout(pending.timer);
        pending.resolve(this.take(pending.weight, pending.session));
      }
    };
  }
  acquire(weight: Weight, session?: string): Promise<() => void> {
    if (this.available(weight, session))
      return Promise.resolve(this.take(weight, session));
    if (this.queue.length >= this.capacity)
      return Promise.reject(new Error("Read queue full. Retry shortly."));
    return new Promise((resolve, reject) => {
      const pending = {
        weight,
        session,
        resolve,
        reject,
        timer: setTimeout(() => {
          this.queue.splice(this.queue.indexOf(pending), 1);
          reject(new Error("Read queue timed out. Retry shortly."));
        }, this.waitMs),
      };
      this.queue.push(pending);
    });
  }
  async run<T>(
    weight: Weight,
    work: () => Promise<T>,
    session?: string,
  ): Promise<T> {
    const release = await this.acquire(weight, session);
    try {
      return await work();
    } finally {
      release();
    }
  }
  cache<T>() {
    return new ReadCache<T>(this);
  }
  invalidate(prefix: string) {
    for (const key of this.cachedKeys.keys())
      if (key.startsWith(prefix)) this.invalidateKey(key);
  }
  invalidateKey(key: string) {
    this.cachedKeys.get(key)?.();
    this.cachedKeys.delete(key);
  }
  remember(key: string, discard: () => void) {
    if (key.startsWith("art:")) {
      const artworkKeys = [...this.cachedKeys.keys()].filter((k) =>
        k.startsWith("art:"),
      );
      const first = artworkKeys[0];
      if (artworkKeys.length >= 64 && first !== undefined)
        this.invalidateKey(first);
    }
    // A refreshed key moves to the back, so the oldest untouched key is evicted first.
    this.cachedKeys.delete(key);
    const first = this.cachedKeys.keys().next().value;
    if (this.cachedKeys.size >= 512 && first !== undefined)
      this.invalidateKey(first);
    this.cachedKeys.set(key, discard);
  }
}
export class ReadCache<T> {
  private flights = new Map<string, Promise<Cached<T>>>();
  private cache = new Map<string, Cached<T>>();
  constructor(private gate: ReadBudget) {}
  async read(
    key: string,
    weight: Weight,
    work: () => Promise<T>,
    ttlMs = 30000,
    session?: string,
    schedule: (work: () => Promise<T>) => Promise<T> = (work) => work(),
  ): Promise<Cached<T>> {
    const old = this.cache.get(key);
    const busy = weight === "heavy" && this.gate.runnerBusy;
    if (busy) {
      if (old) return { ...old, state: "busy" };
      throw new Error("Collection is active or unknown. Retry shortly.");
    }
    if (old && Date.now() - Date.parse(old.savedAt) < ttlMs) return old;
    const flight = this.flights.get(key);
    if (flight) return flight;
    const promise = schedule(() =>
      this.gate.run(
        weight,
        () => {
          if (weight === "heavy" && this.gate.runnerBusy)
            throw new Error("Collection is active or unknown. Retry shortly.");
          return work();
        },
        session,
      ),
    )
      .then((value) => {
        const result: Cached<T> = {
          value,
          savedAt: new Date().toISOString(),
          state: "live",
        };
        this.gate.remember(key, () => this.cache.delete(key));
        this.cache.set(key, result);
        return result;
      })
      .catch((error: unknown): Cached<T> => {
        const status = errorStatus(error);
        if (status === 401 || status === 403) {
          this.gate.invalidateKey(key);
          throw error;
        }
        if (old) return { ...old, state: "cached" };
        throw error;
      })
      .finally(() => this.flights.delete(key));
    this.flights.set(key, promise);
    return promise;
  }
}
export const budget = (globalThis.showcaseBudget ??= new ReadBudget());

declare global {
  var showcaseBudget: ReadBudget | undefined;
}
