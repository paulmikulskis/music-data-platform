import { afterEach, expect, it, vi } from "vitest";
import { budget } from "../server/read-budget";
import { GET } from "../app/health/status/route";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

it.each([true, false])("exposes only health fields without credentials: %s", async (ok) => {
  vi.stubEnv("MDP_CONTROL_API_URL", "http://control.invalid");
  const fetch = vi.fn(async () => Response.json({
    checked_at: new Date().toISOString(),
    ok,
    overdue: ok ? [] : ["hourly"],
    next_step: "Open /ops and follow the runner recovery guide.",
    private_data: "must not leave",
  }, { status: ok ? 200 : 503 }));
  vi.stubGlobal("fetch", fetch);
  const admission = vi.spyOn(budget, "run");
  const response = await GET();
  expect(admission).toHaveBeenCalledWith("light", expect.any(Function));
  expect(response.status).toBe(ok ? 200 : 503);
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(Object.keys(await response.json()).sort()).toEqual(["checked_at", "next_step", "ok", "overdue"]);
  expect(fetch).toHaveBeenCalledWith(new URL("http://control.invalid/health/status"), expect.objectContaining({ redirect: "error", cache: "no-store" }));
});

it.each(["offline", "malformed"])("fails closed on %s with a next step", async (failure) => {
  vi.stubEnv("MDP_CONTROL_API_URL", "http://control.invalid");
  vi.stubGlobal("fetch", vi.fn(async () => {
    if (failure === "offline") throw new Error("secret connection detail");
    return Response.json({ private_data: "secret" });
  }));
  const admission = vi.spyOn(budget, "run");
  const response = await GET();
  expect(admission).toHaveBeenCalledWith("light", expect.any(Function));
  expect(response.status).toBe(503);
  expect(await response.json()).toEqual({ ok: false, next_step: "Open /ops and check the control API connection." });
});
