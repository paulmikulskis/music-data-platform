import { describe, it, expect, vi, afterEach } from "vitest";
import { launchCore } from "../src/core-launcher.js";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
// Core Retry and Replay start a one-off core-runner machine in the cycle's scope.
describe("Core launcher", () => {
  it("starts a one-off machine on the scheduled machine's image with the cycle's scope", async () => {
    vi.stubEnv("MDP_CORE_LAUNCHER", "");
    vi.stubEnv("MDP_FLY_MACHINES_TOKEN", "fly-test-token");
    const calls: { url: string; method: string; body?: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (!init?.method)
        return Response.json([{ name: "mdp-daily", region: "ewr", config: { image: "registry.fly.io/mdp-core-runner:release-abc" } }]);
      return Response.json({ id: "m-123" });
    });
    const launched = await launchCore("daily", "tenant:t-1", "acme", ["--reason-category", "other"]);
    expect(launched).toMatchObject({ launcher: "fly", machine_id: "m-123", command: ["daily", "--reason-category", "other"] });
    expect(calls[1]).toEqual({
      url: "https://api.machines.dev/v1/apps/mdp-core-runner/machines",
      method: "POST",
      body: {
        region: "ewr",
        config: {
          image: "registry.fly.io/mdp-core-runner:release-abc",
          env: { DBT_MDP_SCOPE: "tenant:t-1", MDP_RUN_REASON_CATEGORY: "other", MDP_TENANT_SLUG: "acme" },
          init: { cmd: ["daily", "--reason-category", "other"] }, auto_destroy: true, restart: { policy: "no" },
          guest: { cpu_kind: "shared", cpus: 2, memory_mb: 2048 },
        },
      },
    });
  });
  it("records instead of starting when asked, and refuses without a token", async () => {
    vi.stubEnv("MDP_CORE_LAUNCHER", "record");
    expect(await launchCore("weekly", "global", null, ["--cycle-id", "c-1"])).toEqual({
      launcher: "record", machine_id: null, cadence: "weekly", scope: "global", command: ["weekly", "--cycle-id", "c-1"],
      env: { DBT_MDP_SCOPE: "global", MDP_RUN_REASON_CATEGORY: "other" },
    });
    vi.stubEnv("MDP_CORE_LAUNCHER", "");
    vi.stubEnv("MDP_FLY_MACHINES_TOKEN", "");
    await expect(launchCore("daily", "global", null, [])).rejects.toMatchObject({ error_class: "launcher_unavailable" });
  });
});
