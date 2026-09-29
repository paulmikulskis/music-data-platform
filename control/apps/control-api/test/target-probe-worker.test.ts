import { afterEach, beforeEach, expect, it, vi } from "vitest";
const pending = vi.hoisted(() => vi.fn());
const probe = vi.hoisted(() => vi.fn());
const unsafe = vi.hoisted(() => vi.fn());
vi.mock("../src/target-probe.js", () => ({ pendingProbeTargets: pending, probeTargets: probe }));
vi.mock("../src/db.js", () => ({ database: () => ({ begin: (body: (db: unknown) => unknown) => body({ unsafe }) }) }));
import { serviceDeadline } from "../src/service.js";
import { drainTargetProbes, startTargetProbeWorker } from "../src/target-probe-worker.js";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  unsafe.mockResolvedValue([{ locked: true }]);
  pending.mockResolvedValue(Array.from({ length: 20 }, (_, n) => ({ id: String(n), seed: n === 0 })));
  probe.mockImplementation(async (_db, ids: string[]) => {
    expect(serviceDeadline.getStore()).toBe(15000);
    vi.setSystemTime(Date.now() + 8000);
    return { results: [{ id: ids[0], status: "ok" }] };
  });
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });
it("bounds a pass and records checks separately from activation", async () => {
  await drainTargetProbes();
  expect(probe).toHaveBeenCalledTimes(2);
  expect(unsafe).toHaveBeenCalledWith("SET LOCAL statement_timeout='1s'");
  expect(unsafe).toHaveBeenCalledWith(expect.stringContaining("targets.probeChecked"), ["1", '{"status":"ok"}']);
  expect(unsafe).toHaveBeenCalledWith(expect.stringContaining("SET activated_at=now()"), ["0"]);
});
it("leaves failed seed probes inactive", async () => {
  pending.mockResolvedValue([{ id: "0", seed: true }]);
  probe.mockResolvedValue({ results: [{ id: "0", status: "stale_target" }] });
  await drainTargetProbes();
  expect(unsafe.mock.calls.some(([sql]) => String(sql).includes("SET activated_at"))).toBe(false);
});
it("fails open on database errors and skips a pass another worker owns", async () => {
  const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
  unsafe.mockRejectedValueOnce(new Error("unavailable"));
  await expect(drainTargetProbes()).resolves.toBeUndefined();
  expect(warning).toHaveBeenCalledOnce();
  unsafe.mockResolvedValue([{ locked: false }]);
  await drainTargetProbes();
  expect(probe).not.toHaveBeenCalled();
  warning.mockRestore();
});
it("never overlaps worker passes", async () => {
  let finish!: () => void;
  const drain = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  const stop = startTargetProbeWorker(drain);
  try {
    await vi.advanceTimersByTimeAsync(120000);
    expect(drain).toHaveBeenCalledOnce();
    finish();
    await vi.advanceTimersByTimeAsync(60000);
    expect(drain).toHaveBeenCalledTimes(2);
    finish();
  } finally { stop(); }
});

it("retries a failed job on the next tick", async () => {
  const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
  const drain = vi.fn().mockRejectedValueOnce(new Error("unavailable")).mockResolvedValue(undefined);
  const stop = startTargetProbeWorker(drain);
  try {
    await vi.advanceTimersByTimeAsync(60000);
    expect(drain).toHaveBeenCalledTimes(2);
    expect(warning).toHaveBeenCalledOnce();
  } finally { stop(); warning.mockRestore(); }
});
