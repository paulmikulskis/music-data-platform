import { afterEach, expect, it, vi } from "vitest";

vi.mock("../src/console-data.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/console-data.js")>();
  return { ...original, consoleFailures: vi.fn().mockResolvedValue([]) };
});
vi.mock("../src/auth.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/auth.js")>();
  return {
    ...original,
    authenticate: vi.fn().mockResolvedValue({
      actor: "runbook-reader",
      tenant_id: null,
      tenant_slug: null,
      admin: false,
      staff: true,
    }),
  };
});
vi.mock("../src/db.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/db.js")>();
  return {
    ...original,
    one: vi.fn(async (_db: unknown, _schema: unknown, _query: string, params: string[]) => {
      if (params[0] === "runners-held") {
        throw new original.AppError("not_found", "Open /ops.", 404);
      }
      return { title: "service unreachable", body_md: "Check authenticated health, network reachability and service configuration before retrying." };
    }),
  };
});
import { app } from "../src/app.js";

afterEach(() => vi.unstubAllEnvs());

it("serves the held runner link even when the database has no guide row", async () => {
  vi.stubEnv("MDP_CONTROL_RT_URL", "postgresql://127.0.0.1:1/unused");
  const response = await app.request("/runbooks/runners-held");
  expect(response.status).toBe(200);
  const html = await response.text();
  expect(html).toContain("fly machines list");
  expect(html).toContain("mdp_deploy_hold");
  expect(html).toContain("MDP_DEPLOY_TAKEOVER=1");
  expect(html).toContain('href="/ops"');
});

it("serves heartbeat setup and its anchor even with the old database summary", async () => {
  vi.stubEnv("MDP_CONTROL_RT_URL", "postgresql://127.0.0.1:1/unused");
  const response = await app.request("/runbooks/service-unreachable#external-heartbeat");
  expect(response.status).toBe(200);
  const html = await response.text();
  expect(html).toContain('id="external-heartbeat"');
  expect(html).toContain("MDP_HEARTBEAT_URL");
  expect(html).toContain("mdp-core-runner");
  expect(html).toContain("Check the monitor for that first ping");
  expect(html).toContain('href="/ops"');
});
