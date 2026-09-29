import { describe, it, expect, vi } from "vitest";
import { ReadBudget } from "../server/read-budget";
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
describe("read admission", () => {
  it("holds four heavy slots through completion and queues the next read", async () => {
    const budget = new ReadBudget();
    const releases = await Promise.all(
      Array.from({ length: 4 }, () => budget.acquire("heavy")),
    );
    let started = false;
    const pending = budget.run("heavy", async () => {
      started = true;
    });
    await Promise.resolve();
    expect(started).toBe(false);
    releases[0]();
    await pending;
    expect(started).toBe(true);
    releases.slice(1).forEach((r) => r());
  });
  it("bounds the queue and times out queued work without starting it", async () => {
    const budget = new ReadBudget(15, 1);
    const release = await Promise.all(
      Array.from({ length: 6 }, () => budget.acquire("light")),
    );
    const queued = expect(budget.acquire("light")).rejects.toThrow("timed out");
    await expect(budget.acquire("light")).rejects.toThrow("full");
    await queued;
    release.forEach((r) => r());
  });
  it("counts console work globally and permits only two requests per session", async () => {
    const budget = new ReadBudget();
    const a = await budget.acquire("light", "one");
    const b = await budget.acquire("light", "one");
    let entered = false;
    const p = budget.run(
      "light",
      async () => {
        entered = true;
      },
      "one",
    );
    await Promise.resolve();
    expect(entered).toBe(false);
    const other = await budget.acquire("light", "two");
    other();
    a();
    await p;
    b();
  });
  it("single-flights and keeps the original timestamp on failure or scheduled work", async () => {
    const budget = new ReadBudget();
    const cache = budget.cache<string>();
    budget.setRunner("idle");
    const work = deferred<string>();
    const spy = vi.fn(() => work.promise);
    const first = cache.read("global:a", "heavy", spy, 0);
    const second = cache.read("global:a", "heavy", spy, 0);
    work.resolve("fact");
    const fresh = await first;
    expect(await second).toEqual(fresh);
    expect(spy).toHaveBeenCalledTimes(1);
    budget.setRunner("busy");
    expect(await cache.read("global:a", "heavy", spy)).toEqual({
      ...fresh,
      state: "busy",
    });
    expect(spy).toHaveBeenCalledTimes(1);
    budget.setRunner("idle");
    expect(
      await cache.read(
        "global:a",
        "heavy",
        async () => {
          throw Error("offline");
        },
        0,
      ),
    ).toEqual({ ...fresh, state: "cached" });
  });
  it("fails closed on unknown runner state, including stale idle state", async () => {
    vi.useFakeTimers();
    const budget = new ReadBudget();
    const cache = budget.cache<string>();
    const spy = vi.fn();
    await expect(cache.read("x", "heavy", spy)).rejects.toThrow("unknown");
    budget.setRunner("idle");
    vi.advanceTimersByTime(25001);
    await expect(cache.read("x", "heavy", spy)).rejects.toThrow("unknown");
    expect(spy).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
  it("never uses private last-good values after upstream authentication failure", async () => {
    const budget = new ReadBudget();
    const cache = budget.cache<string>();
    await cache.read("person:a", "light", async () => "private", 0);
    await expect(
      cache.read(
        "person:a",
        "light",
        async () => {
          throw { status: 403 };
        },
        0,
      ),
    ).rejects.toEqual({ status: 403 });
    await expect(
      cache.read(
        "person:a",
        "light",
        async () => {
          throw Error("offline");
        },
        0,
      ),
    ).rejects.toThrow("offline");
  });
});
it("rechecks scheduled work when a queued heavy read finally gets a slot", async () => {
  const gate = new ReadBudget();
  const cache = gate.cache<string>();
  gate.setRunner("idle");
  const releases = await Promise.all(
    Array.from({ length: 4 }, () => gate.acquire("heavy")),
  );
  const work = vi.fn(async () => "new fact");
  const result = expect(cache.read("queued", "heavy", work)).rejects.toThrow(
    "unknown",
  );
  gate.setRunner("busy");
  releases.forEach((release) => release());
  await result;
  expect(work).not.toHaveBeenCalled();
});

it("reserves two of the eight light slots for sessions and heartbeat reads", async () => {
  const gate = new ReadBudget(20);
  const regular = await Promise.all(
    Array.from({ length: 6 }, () => gate.acquire("light")),
  );
  const extra = expect(gate.acquire("light")).rejects.toThrow("timed out");
  const essential = await Promise.all([
    gate.acquire("essential"),
    gate.acquire("essential"),
  ]);
  await extra;
  essential.concat(regular).forEach((release) => release());
});

it("shares eviction limits and prefix invalidation across typed caches", async () => {
  const gate = new ReadBudget();
  const labels = gate.cache<string>();
  const counts = gate.cache<number>();
  await labels.read("person:one:label", "light", async () => "saved");
  await counts.read("person:one:count", "light", async () => 2);
  gate.invalidate("person:one:");
  expect(
    (await labels.read("person:one:label", "light", async () => "fresh")).value,
  ).toBe("fresh");
  expect(
    (await counts.read("person:one:count", "light", async () => 3)).value,
  ).toBe(3);
  for (let i = 0; i < 510; i++)
    await counts.read(`count:${i}`, "light", async () => i);
  await counts.read("count:next", "light", async () => 510);
  expect(
    (await labels.read("person:one:label", "light", async () => "evicted"))
      .value,
  ).toBe("evicted");
  await labels.read("art:first", "light", async () => "saved cover");
  for (let i = 0; i < 64; i++)
    await counts.read(`art:${i}`, "light", async () => i);
  expect(
    (await labels.read("art:first", "light", async () => "new cover")).value,
  ).toBe("new cover");
});
it("uses last-good data after an unknown failure without changing its timestamp", async () => {
  const cache = new ReadBudget().cache<string>();
  const fresh = await cache.read("saved", "light", async () => "fact", 0);
  expect(
    await cache.read(
      "saved",
      "light",
      async () => {
        throw null;
      },
      0,
    ),
  ).toEqual({ ...fresh, state: "cached" });
  await expect(
    cache.read(
      "saved",
      "light",
      async () => {
        throw { statusCode: 401 };
      },
      0,
    ),
  ).rejects.toEqual({ statusCode: 401 });
  await expect(
    cache.read(
      "saved",
      "light",
      async () => {
        throw Error("offline");
      },
      0,
    ),
  ).rejects.toThrow("offline");
});
