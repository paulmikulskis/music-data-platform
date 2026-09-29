import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import postgres from "postgres";
import { z } from "zod";
import { errorHint } from "@mdp/contracts";
import { drainAlerts, emailRetry } from "../src/email.js";
import { isolatedControl } from "./isolated-control.js";

const base = process.env.MDP_TENANTS_TEST_URL;
const auditRows = z.array(
  z.object({ action: z.string(), after: z.record(z.string(), z.unknown()) }),
);

describe.skipIf(!base)("alert email outbox on PostgreSQL", () => {
  let admin: postgres.Sql;
  let worker: postgres.Sql;
  let workerUrl: string;
  let close: (() => Promise<void>) | undefined;
  beforeAll(async () => {
    const fixture = await isolatedControl(base!);
    admin = fixture.db;
    close = fixture.close;
    const url = new URL(base!);
    url.pathname = `/${admin.options.database}`;
    url.username = "control_rt";
    url.password = "control_rt";
    workerUrl = url.toString();
    worker = postgres(workerUrl, { max: 4, onnotice: () => {} });
  }, 60000);
  beforeEach(async () => {
    await admin`TRUNCATE control.alert,control.audit_log CASCADE`;
    vi.stubEnv("RESEND_API_KEY", "");
    vi.stubEnv("SMTP_URL", "");
    vi.stubEnv("MDP_EMAIL_FROM", "");
    vi.stubEnv("MDP_EMAIL_TO", "");
    vi.stubEnv("MDP_AUTH_MODE", "production");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ id: "fixture" })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });
  afterAll(async () => {
    await worker?.end();
    await close?.();
  });
  async function alert() {
    const result =
      await admin`INSERT INTO control.alert(class,severity,subject_type,subject_id)
      VALUES ('warehouse_unavailable','critical','email_test','fixture') RETURNING id::text`;
    return z.array(z.object({ id: z.string() })).parse(result)[0]!.id;
  }
  async function audits(id: string) {
    return auditRows.parse(
      await admin`SELECT action,after FROM control.audit_log WHERE subject=${id} ORDER BY at,id`,
    );
  }
  function configured() {
    vi.stubEnv("RESEND_API_KEY", "fixture");
    vi.stubEnv("MDP_EMAIL_FROM", "fixture@example.invalid");
    vi.stubEnv("MDP_EMAIL_TO", "fixture@example.invalid");
  }
  it.each(["transport", "sender", "recipient"])(
    "records one setup state across retries and a restart without %s",
    async (missing) => {
      if (missing !== "transport") {
        configured();
        vi.stubEnv(
          missing === "sender" ? "MDP_EMAIL_FROM" : "MDP_EMAIL_TO",
          "",
        );
      }
      const id = await alert();
      const resolved = await alert();
      const acknowledged = await alert();
      await admin`UPDATE control.alert SET resolved_at=now() WHERE id=${resolved}`;
      await admin`UPDATE control.alert SET acknowledged_by='fixture' WHERE id=${acknowledged}`;
      await Promise.all(Array.from({ length: 12 }, () => drainAlerts(worker)));
      // A new connection represents a restarted worker; the state is held in the database.
      const restarted = postgres(workerUrl, { max: 1, onnotice: () => {} });
      try {
        for (let n = 0; n < 5; n++) await drainAlerts(restarted);
      } finally {
        await restarted.end();
      }
      expect(await audits(id)).toEqual([
        {
          action: "email.skipped",
          after: {
            state: "skipped",
            reason: "not_configured",
            error_class: "email_delivery_unconfigured",
            ...errorHint("email_delivery_unconfigured"),
          },
        },
      ]);
      expect(await audits(resolved)).toEqual([]);
      expect(await audits(acknowledged)).toEqual([]);
      expect(fetch).not.toHaveBeenCalled();
      configured();
      await drainAlerts(worker);
      await drainAlerts(worker);
      expect((await audits(id)).map((row) => row.action)).toEqual([
        "email.skipped",
        "email.sent",
      ]);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );
  it("backs off failures and stops after ten attempts without rewriting earlier audit rows", async () => {
    configured();
    const id = await alert();
    await admin`INSERT INTO control.audit_log(actor,action,subject,after)
      VALUES ('fixture','email.failed',${id},'{"state":"retry_pending","error_class":"email_delivery_failed"}')`;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 503 })),
    );
    vi.spyOn(console, "warn").mockImplementation(() => {});
    await drainAlerts(worker);
    await drainAlerts(worker);
    expect(fetch).toHaveBeenCalledTimes(1);
    for (let n = 1; n < emailRetry.maxAttempts + 2; n++) {
      // Only fixture timestamps advance the retry clock; the worker has no audit UPDATE grant.
      await admin`UPDATE control.audit_log SET at=at-interval '2 hours' WHERE subject=${id}`;
      await drainAlerts(worker);
    }
    expect(fetch).toHaveBeenCalledTimes(10);
    const rows = await audits(id);
    expect(rows.filter((row) => row.action === "email.failed")).toHaveLength(
      11,
    );
    expect(rows.filter((row) => row.action === "email.gave_up")).toHaveLength(
      1,
    );
    expect(
      rows.find(
        (row) =>
          row.after.attempt === undefined && row.action === "email.failed",
      )?.after,
    ).toEqual({
      state: "retry_pending",
      error_class: "email_delivery_failed",
    });
  });
});
