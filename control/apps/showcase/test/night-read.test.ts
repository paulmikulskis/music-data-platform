import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Person } from "@mdp/showcase-auth";
import { nightFixture } from "./browser/night-fixture";
import { nightMoments, nightResponse } from "../lib/night";
import { budget } from "../server/read-budget";
const mocks = vi.hoisted(() => ({
  stamps: vi.fn<typeof import("../server/relation-counts").currentBuilds>(),
  read: vi.fn<
    (input: {
      since: string;
      until: string;
    }) => Promise<ReturnType<typeof nightFixture>>
  >(),
}));
vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({
  controlClient: () => ({ platform: { night: mocks.read } }),
  warehouse: () => ({}),
}));
vi.mock("../server/relation-counts", async (original) => ({
  ...(await original<typeof import("../server/relation-counts")>()),
  currentBuilds: mocks.stamps,
}));
import { night } from "../server/night";
import { readRunnerState } from "@mdp/contracts/runner-state";
const person: Person = {
  handle: "fixture",
  display_name: "Test viewer",
  admin_key: "fixture",
  api_key_id: "00000000-0000-4000-8000-000000000071",
};
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-27T13:00:00Z"));
  budget.invalidate("");
  mocks.read.mockReset();
  mocks.stamps.mockReset();
});
afterEach(() => vi.useRealTimers());
it("keeps readings through a stamp lock timeout and keeps busy ready marks", async () => {
  const window = {
    since: "2026-09-26T22:00:00Z",
    until: "2026-09-27T13:00:00Z",
  };
  mocks.read.mockResolvedValue(nightFixture(window));
  mocks.stamps.mockRejectedValueOnce(
    Object.assign(new Error("locked"), { code: "55P03" }),
  );
  budget.setRunner("idle");
  const locked = nightResponse.parse(await night(person, window));
  expect(locked.value.runs).toHaveLength(2);
  expect(locked.value.ready_state).toBe("unavailable");
  expect(locked.value.ready).toEqual([]);
  mocks.stamps.mockResolvedValue([
    {
      relation: "marts.mart_top_movers_current",
      cycle_id: "00000000-0000-4000-8000-000000000041",
      close_no: null,
      built_at: "2026-09-27T06:12:00.000000Z",
    },
  ]);
  const ready = nightResponse.parse(await night(person, window));
  expect(ready.value.ready).toHaveLength(1);
  expect(ready.value.ready[0].close_no).toBeNull();
  vi.advanceTimersByTime(120000);
  budget.setRunner("busy");
  const busy = nightResponse.parse(await night(person, window));
  expect(busy.value.runs).toHaveLength(2);
  expect(busy.value.ready_state).toBe("busy");
  expect(busy.value.ready).toEqual(ready.value.ready);
  expect(busy.value.ready_saved_at).toBe(ready.value.ready_saved_at);
  const cloud = await readRunnerState({
    unsafe: async () => [{ runner: "cloud" }],
  });
  expect(cloud.state).toBe("unknown");
  budget.setRunner(cloud.state);
  const unknown = nightResponse.parse(await night(person, window));
  expect(unknown.value.ready).toEqual(ready.value.ready);
  expect(unknown.value.ready_saved_at).toBe(ready.value.ready_saved_at);
  vi.advanceTimersByTime(120000);
  budget.setRunner("idle");
  mocks.stamps.mockRejectedValueOnce(new Error("locked"));
  const cached = nightResponse.parse(await night(person, window));
  expect(cached.value.ready_state).toBe("cached");
  expect(cached.value.ready).toEqual(ready.value.ready);
  expect(cached.value.ready_saved_at).toBe(ready.value.ready_saved_at);
});

it.each(["busy", "unknown"] as const)(
  "keeps no-stamp %s reads unavailable",
  async (state) => {
    const window = {
      since: "2026-09-26T22:00:00Z",
      until: "2026-09-27T13:00:00Z",
    };
    mocks.read.mockResolvedValue(nightFixture(window));
    budget.setRunner(state);
    const result = nightResponse.parse(await night(person, window));
    expect(result.value.ready_state).toBe("unavailable");
    expect(result.value.ready_saved_at).toBeNull();
    expect(result.value.ready).toEqual([]);
    expect(mocks.stamps).not.toHaveBeenCalled();
  },
);

it.each(["busy", "cached"] as const)(
  "keeps a successful empty stamp read unavailable when its next read is %s",
  async (state) => {
    const window = {
      since: "2026-09-26T22:00:00Z",
      until: "2026-09-27T13:00:00Z",
    };
    mocks.read.mockResolvedValue(nightFixture(window));
    mocks.stamps.mockResolvedValue([]);
    budget.setRunner("idle");
    const live = nightResponse.parse(await night(person, window));
    expect(live.value.ready_state).toBe("live");
    expect(live.value.ready_saved_at).not.toBeNull();
    expect(live.value.ready).toEqual([]);
    vi.advanceTimersByTime(120000);
    budget.setRunner(state === "busy" ? "busy" : "idle");
    if (state === "cached")
      mocks.stamps.mockRejectedValueOnce(new Error("locked"));
    const saved = nightResponse.parse(await night(person, window));
    expect(saved.value.ready_state).toBe("unavailable");
    expect(saved.value.ready_saved_at).toBeNull();
    expect(saved.value.ready).toEqual([]);
    expect(saved.value.runs).toEqual(live.value.runs);
    expect(
      nightMoments(saved.value).filter((moment) => moment.lane === "Ready"),
    ).toEqual([]);
    expect(mocks.stamps).toHaveBeenCalledTimes(state === "busy" ? 1 : 2);
  },
);
