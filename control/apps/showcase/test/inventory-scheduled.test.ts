import { afterEach, beforeEach, expect, it, vi } from "vitest";
const fixtures = vi.hoisted(() => ({
  begin: vi.fn(),
  tx: vi.fn(),
  runner: vi.fn(),
  inventory: vi.fn(),
}));
vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({
  controlStore: () => ({ begin: fixtures.begin }),
  warehouse: () => ({}),
}));
vi.mock("@mdp/contracts/runner-state", () => ({
  readRunnerState: fixtures.runner,
}));
vi.mock("../server/inventory.js", () => ({
  readInventory: fixtures.inventory,
}));
import { startScheduledInventory } from "../server/inventory-job";
import { budget } from "../server/read-budget";
const warehouse = "00000000-0000-4000-8000-000000000001";
let stop: (() => void) | undefined;
beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-25T00:29:59Z"));
  vi.stubEnv("MDP_SERVICE_URL", "http://functions.invalid");
  vi.stubEnv("MDP_SERVICE_TOKEN", "test-token");
  vi.spyOn(console, "error").mockImplementation(() => {});
  fixtures.runner.mockResolvedValue({ state: "idle" });
  fixtures.begin.mockImplementation(async (work) => work(fixtures.tx));
  fixtures.tx.mockImplementation(async (query: TemplateStringsArray) => {
    if (query.join("").includes("SELECT id")) return [{ id: warehouse }];
    if (query.join("").includes("pg_try_advisory")) return [{ locked: true }];
    return [];
  });
  fixtures.inventory.mockResolvedValue([
    { layer: "marts", relations: 1, rows_est: "1", bytes: "1", complete: true },
  ]);
});
afterEach(() => {
  stop?.();
  stop = undefined;
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it.each([
  "admission",
  "transaction",
  "warehouse lookup",
  "warehouse read",
] as const)(
  "reports a scheduled %s failure and retains the independent alert subject",
  async (failure) => {
    const fail = () => {
      if (failure === "admission")
        vi.spyOn(budget, "run").mockRejectedValueOnce(
          new Error("fixture admission"),
        );
      if (failure === "transaction")
        fixtures.begin.mockRejectedValueOnce(new Error("fixture transaction"));
      if (failure === "warehouse lookup")
        fixtures.tx.mockRejectedValueOnce(new Error("fixture lookup"));
      if (failure === "warehouse read")
        fixtures.inventory.mockRejectedValueOnce(new Error("fixture read"));
    };
    const request = vi.fn<typeof fetch>(async () =>
      Response.json({ status: "recorded", warehouse }),
    );
    vi.stubGlobal("fetch", request);
    fail();
    stop = startScheduledInventory();
    expect(request).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(request).toHaveBeenCalledTimes(1);
    expect(String(request.mock.calls[0]![0])).toBe(
      "http://functions.invalid/v1/alerts/showcase_inventory_failed",
    );
    expect(request.mock.calls[0]![1]).toMatchObject({
      method: "POST",
      body: "{}",
      headers: { Authorization: "Bearer test-token" },
    });
    fail();
    await vi.advanceTimersByTimeAsync(24 * 3600000);
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[1]![1]).toMatchObject({
      body: JSON.stringify({ warehouse }),
    });
  },
);
it("retries alert delivery after 30 then 60 seconds without repeating the capture", async () => {
  fixtures.begin.mockRejectedValue(new Error("fixture transaction"));
  const request = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(new Response("", { status: 503 }))
    .mockRejectedValueOnce(new Error("fixture offline"))
    .mockResolvedValueOnce(Response.json({ warehouse }));
  vi.stubGlobal("fetch", request);
  stop = startScheduledInventory();
  await vi.advanceTimersByTimeAsync(1000);
  expect(request).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(29999);
  expect(request).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(request).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(59999);
  expect(request).toHaveBeenCalledTimes(2);
  await vi.advanceTimersByTimeAsync(1);
  expect(request).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(120000);
  expect(request).toHaveBeenCalledTimes(3);
  expect(fixtures.begin).toHaveBeenCalledTimes(1);
});
it("retries alert admission and cancels delivery when the job stops", async () => {
  fixtures.begin.mockRejectedValue(new Error("fixture transaction"));
  const original = budget.run.bind(budget);
  let calls = 0;
  vi.spyOn(budget, "run").mockImplementation((...args) =>
    ++calls === 3
      ? Promise.reject(new Error("fixture alert admission"))
      : original(...args),
  );
  const request = vi.fn();
  vi.stubGlobal("fetch", request);
  stop = startScheduledInventory();
  await vi.advanceTimersByTimeAsync(1000);
  expect(request).not.toHaveBeenCalled();
  stop();
  await vi.advanceTimersByTimeAsync(30000);
  expect(request).not.toHaveBeenCalled();
});
