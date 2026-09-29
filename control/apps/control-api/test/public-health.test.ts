import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ read: vi.fn(), authenticate: vi.fn() }));
vi.mock("../src/db.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/db.js")>();
  return { ...original, platformRead: mocks.read };
});
vi.mock("../src/auth.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/auth.js")>();
  return { ...original, authenticate: mocks.authenticate };
});
import { app } from "../src/app.js";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

it("serves public health without auth, shares reads and fails closed on database outage", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime("2026-09-26T12:00:00Z");
  vi.stubEnv("MDP_CONTROL_RT_URL", "postgresql://127.0.0.1:1/unused");
  const summary = {
    checked_at: new Date().toISOString(),
    ok: true,
    overdue: [],
    next_step: "Open /ops and follow the runner recovery guide.",
  };
  mocks.read.mockResolvedValueOnce(summary);
  for (let request = 0; request < 2; request++) {
    const response = await app.request("/health/status");
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual(summary);
  }
  expect(mocks.read).toHaveBeenCalledTimes(1);
  expect(mocks.authenticate).not.toHaveBeenCalled();
  vi.setSystemTime("2026-09-26T12:01:00Z");
  mocks.read.mockRejectedValueOnce(new Error("private connection details"));
  const response = await app.request("/health/status");
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ ok: false, next_step: "Open /ops and check the control API connection." });
});
