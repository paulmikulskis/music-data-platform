import { describe, it, expect, vi, afterEach } from "vitest";
import { createHmac, randomUUID } from "node:crypto";
import { targetCsv } from "../src/csv.js";
import { verifyWebhook, webhookPayload } from "../src/webhook.js";
import { dbtCloud } from "../src/service.js";
import { authenticate, checkOrigin } from "../src/auth.js";
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
describe("operator boundaries", () => {
  it("supports quoted CSV and optional platform IDs", () => {
    expect(
      targetCsv(
        'platform,handle,display_name\nfixture,test,"synthetic, fixture"',
      )[0]?.display_name,
    ).toBe("synthetic, fixture");
    expect(() => targetCsv("platform,unexpected\nfixture,x")).toThrow();
    expect(() => targetCsv("platform,handle\nfixture,x\nfixture,x")).toThrow();
  });
  it("verifies webhook bytes and rejects changed payloads", () => {
    const secret = randomUUID();
    const sig = createHmac("sha256", secret).update("{}").digest("hex");
    expect(() => verifyWebhook("{}", sig, secret)).not.toThrow();
    expect(() => verifyWebhook("{ }", sig, secret)).toThrow();
    expect(() => verifyWebhook("{}", null, secret)).toThrow();
  });
  it("normalizes dbt Cloud events", () =>
    expect(
      webhookPayload({
        eventId: "evt-test",
        eventType: "job.run.errored",
        data: { runId: 123, jobId: 4, runStatusCode: 20 },
      }).status,
    ).toBe("failed"));
  it("reads .data from dbt Admin API envelopes", async () => {
    vi.stubEnv("DBT_CLOUD_TOKEN", randomUUID());
    vi.stubEnv("DBT_CLOUD_ACCOUNT_ID", "test");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ data: { id: 7 }, status: { is_success: true } }),
        ),
    );
    expect(await dbtCloud("jobs/7/run/", {})).toEqual({ id: 7 });
  });
  it("returns dbt_api_unavailable without a token", async () => {
    vi.stubEnv("DBT_CLOUD_TOKEN", "");
    await expect(dbtCloud("jobs/")).rejects.toMatchObject({
      error_class: "dbt_api_unavailable",
      status: 503,
    });
  });
  it("requires explicit dev mode and fixes its identity", async () => {
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("MDP_AUTH_MODE", "dev");
    expect(
      (await authenticate(new Request("http://localhost/ops"))).actor,
    ).toBe("dev-user");
    await expect(
      authenticate(
        new Request("http://localhost/ops", {
          headers: { "x-mdp-dev-user": "impersonated" },
        }),
      ),
    ).rejects.toMatchObject({ status: 401 });
    vi.stubEnv("MDP_AUTH_MODE", "");
    await expect(
      authenticate(new Request("http://localhost/ops")),
    ).rejects.toMatchObject({ status: 401 });
  });
  it("refuses cross-origin browser mutations", () =>
    expect(() =>
      checkOrigin(
        new Request("http://localhost/actions/run", {
          headers: { origin: "https://invalid.example" },
        }),
      ),
    ).toThrow());
});
