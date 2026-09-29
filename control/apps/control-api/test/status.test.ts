import { healthSummary } from "../src/status.js";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import postgres from "postgres";
import { isolatedControl } from "./isolated-control.js";
import { createRouterClient } from "@orpc/server";
import { router } from "../src/router.js";
import { createApp } from "../../data-api/src/app.js";
import { formatStatus } from "../../../packages/mdp-cli/src/status.js";
import { consoleFailures } from "../src/console-data.js";
import { StatusCard } from "../src/pages.js";
import type { PlatformStatus } from "@mdp/contracts";
import type { DB } from "../src/db.js";

const identity = { actor: "status-test", admin: true, tenant_id: null, tenant_slug: null };
const now = "2026-09-24T12:00:00.000Z";
afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.useRealTimers(); });
it("serves the data build without authentication or database access", async () => {
  vi.stubEnv("MDP_BUILD_SHA", "build-a");
  const db = postgres("postgresql://127.0.0.1:1/unused");
  try {
    const response = await createApp(db, db).request("/version");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ git_sha: "build-a" });
  } finally { await db.end(); }
});

// control-ci provisions this disposable database. Each scenario rolls its fixtures back.
describe.skipIf(!process.env.MDP_STATUS_TEST_URL)("status router over seeded control rows", () => {
  let db: postgres.Sql;
  let close: (() => Promise<void>) | undefined;
  beforeAll(async () => { if (process.env.MDP_STATUS_TEST_URL) ({ db, close } = await isolatedControl(process.env.MDP_STATUS_TEST_URL)); });
  afterAll(async () => close?.());
  async function scenario(check: (tx: postgres.TransactionSql, read: () => Promise<PlatformStatus>) => Promise<void>) {
    vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(now);
    vi.stubEnv("MDP_BUILD_SHA", "build-a"); vi.stubEnv("MDP_DATA_API_URL", "http://data.invalid");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ git_sha: "build-a" })));
    const rollback = new Error("rollback fixture");
    await db.begin(async tx => {
      await tx`TRUNCATE control.cycle,control.alert,control.audit_log,control.dbt_job,control.tenant,control.runbook CASCADE`;
      await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,opened_at,closed_at,close_no,git_sha,image_digest)
        SELECT cadence,'global',cadence,'closed',${now}::timestamptz-interval '10 minutes',${now}::timestamptz-interval '5 minutes',n,'cycle-build','sha256:fixture'
        FROM (VALUES ('hourly',1),('daily',2),('weekly',3)) v(cadence,n)`;
      const client = createRouterClient(router, { context: { identity, db: tx } });
      const read = async () => {
        await tx`SET LOCAL ROLE control_rt`;
        try { return await client.status({}); } finally { await tx`RESET ROLE`; }
      };
      await check(tx, read);
      throw rollback;
    }).catch(error => { if (error !== rollback) throw error; });
  }
  it("is healthy with current closes and young open cycles; reads as control_rt without audit writes", async () => scenario(async (tx, read) => {
    await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at) VALUES ('hourly','global','open',${now}::timestamptz-interval '2 minutes')`;
    const status = await read();
    expect(status.verdict).toBe("healthy");
    expect(status.cadences).toHaveLength(3);
    expect(status.cadences[0]).toMatchObject({ overdue: false, last_closed: { close_no: "1", age_seconds: 300, git_sha: "cycle-build", image_digest: "sha256:fixture" } });
    expect(status.cadences[0]?.open[0]?.age_seconds).toBe(120);
    expect((await tx`SELECT count(*)::int AS n FROM control.audit_log`)[0]?.n).toBe(0);
    expect(formatStatus(status)).toContain("HEALTHY");
  }));
  it("shows the alert email setup step while delivery is unconfigured", async () => scenario(async (_tx, read) => {
    vi.stubEnv("RESEND_API_KEY", "");
    vi.stubEnv("SMTP_URL", "");
    vi.stubEnv("MDP_AUTH_MODE", "production");
    const html = String(await StatusCard({ status: await read() }));
    expect(html).toContain("Alert email delivery is not set up");
    expect(html).toContain('href="/runbooks/service-unreachable#alert-email"');
    expect(html).toContain("Set up alert email");
  }));
  it.each([
    { token: "", mode: "", state: "missing", message: "Set MDP_FLY_MACHINES_TOKEN", next: "on control-api" },
    { token: "fixture-launch-token", mode: "", state: "configured", message: "Core Retry and Replay are configured", next: "pnpm --dir control mdp retry" },
    { token: "", mode: "record", state: "configured", message: "Local recording mode", next: "without starting a machine" },
  ])("keeps launcher guidance in the API, card and CLI: $state / $mode", async ({ token, mode, state, message, next }) => scenario(async (_tx, read) => {
    vi.stubEnv("MDP_FLY_MACHINES_TOKEN", token);
    vi.stubEnv("MDP_CORE_LAUNCHER", mode);
    const status = await read();
    expect(status.retry_launcher).toMatchObject({ state, message: expect.stringContaining(message) });
    const html = String(await StatusCard({ status }));
    const cli = formatStatus(status);
    expect(html).toContain(`>${state}</span>`);
    expect(html).toContain('href="/runbooks/cadence-failed"');
    expect(cli).toContain(`Retry launcher: ${state}`);
    for (const output of [html, cli]) {
      expect(output).toContain(message);
      expect(output).toContain(next);
      expect(output).not.toContain("[object Object]");
      expect(output).not.toContain("fixture-launch-token");
    }
    // Configuration guidance is advisory and does not alter cadence health.
    expect(status.verdict).toBe("healthy");
  }));
  it("uses cadence boundaries, latest close and open age, including registered active scopes with no close", async () => scenario(async (tx, read) => {
    await tx`UPDATE control.cycle SET closed_at=${now}::timestamptz-interval '1 hour' WHERE cadence='hourly'`;
    expect((await read()).verdict).toBe("healthy");
    await tx`UPDATE control.cycle SET closed_at=${now}::timestamptz-interval '1 hour 1 second' WHERE cadence='hourly'`;
    expect((await read()).verdict).toBe("attention");
    await tx`UPDATE control.cycle SET closed_at=${now}::timestamptz-interval '2 hours' WHERE cadence='hourly'`;
    expect((await read()).verdict).toBe("attention");
    await tx`UPDATE control.cycle SET closed_at=${now}::timestamptz-interval '2 hours 1 second' WHERE cadence='hourly'`;
    expect((await read()).verdict).toBe("broken");
    await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at,close_no) VALUES ('hourly','global','new','closed',${now},'9007199254740993')`;
    expect((await read()).cadences[0]?.last_closed?.close_no).toBe("9007199254740993");
    expect((await read()).verdict).toBe("healthy");
    await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at) VALUES ('daily','global','stuck',${now}::timestamptz-interval '49 hours')`;
    expect((await read()).verdict).toBe("broken");
    await tx`DELETE FROM control.cycle WHERE status='open'`;
    const tenants = await tx`INSERT INTO control.tenant(slug,name) VALUES ('status-fixture','Fixture tenant') RETURNING id`;
    await tx`INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('fixture','core','weekly',${`tenant:${tenants[0]!.id}`})`;
    expect((await read()).verdict).toBe("attention");
    expect((await read()).cadences.at(-1)?.last_closed).toBeNull();
    await tx`UPDATE control.tenant SET status='inactive'`;
    expect((await read()).verdict).toBe("healthy");
  }));
  it.each(["manual:canary:probe", "manual:operator", "backfill:probe", "canary:probe"])(
    "keeps scheduled freshness while %s opens and closes", async opener => scenario(async (tx, read) => {
      await tx`UPDATE control.cycle SET closed_at=${now}::timestamptz-interval '16 days'
        WHERE cadence IN ('daily','weekly')`;
      const before = await read();
      const failures = await consoleFailures(tx);
      expect(before.cadences.filter(c=>c.cadence!=="hourly").every(c=>c.overdue)).toBe(true);
      for (const cadence of ["daily", "weekly"]) {
        const [probe] = await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at)
          VALUES (${cadence},'global',${opener},${now}::timestamptz-interval '20 days') RETURNING id`;
        // An old probe cannot create a cadence incident or hide a missing scheduled close.
        expect((await read()).cadences).toEqual(before.cadences);
        expect(await consoleFailures(tx)).toEqual(failures);
        await tx`UPDATE control.cycle SET status='closed',closed_at=${now},close_no=50 WHERE id=${probe!.id}`;
        const after = await read();
        expect(after.cadences).toEqual(before.cadences);
        expect(after.verdict).toEqual(before.verdict);
        expect(await consoleFailures(tx)).toEqual(failures);
        const html = String(await StatusCard({status:after}));
        expect(html).toContain("Overdue");
        expect(html).not.toContain("#50");
        expect(formatStatus(after)).toEqual(formatStatus(before));
      }
    }),
  );
  it("groups only unacknowledged unresolved alerts, joins actual guides, and counts email attempts and pending work", async () => scenario(async (tx, read) => {
    await tx`INSERT INTO control.runbook(slug,title,body_md) VALUES ('surface-drift','Fixture guide','Inspect the fixture')`;
    await tx`INSERT INTO control.alert(class,severity,subject_type,subject_id,opened_at) VALUES
      ('surface_drift','warning','fixture','one',${now}),('surface_drift','warning','fixture','two',${now}),
      ('missing_guide','info','fixture','three',${now})`;
    let status = await read();
    expect(status.verdict).toBe("attention");
    expect(status.alerts.find(a => a.class === "surface_drift")).toMatchObject({ count: "2", runbook_urls: ["/runbooks/surface-drift"], no_guide: false });
    expect(status.alerts.find(a => a.class === "missing_guide")?.no_guide).toBe(true);
    await tx`UPDATE control.alert SET acknowledged_by='fixture' WHERE subject_id='one'`;
    await tx`UPDATE control.alert SET resolved_at=${now} WHERE subject_id='two'`;
    const alerts = await tx`INSERT INTO control.alert(class,severity,subject_type,subject_id,opened_at) VALUES ('warehouse_unavailable','critical','fixture','critical',${now}) RETURNING id`;
    const id = String(alerts[0]!.id);
    await tx`INSERT INTO control.audit_log(actor,action,subject,at) VALUES
      ('fixture','email.failed',${id},${now}),('fixture','email.failed',${id},${now}),
      ('fixture','email.skipped',${id},${now}),('fixture','email.failed',${id},${now}::timestamptz-interval '25 hours')`;
    status = await read();
    expect(status.verdict).toBe("broken");
    expect(status.alerts).toHaveLength(2);
    expect(status.sends).toEqual({ failed: "2", pending: "1", skipped: "1", gave_up: "0" });
    expect(formatStatus(status)).toContain("no guide");
    await tx`INSERT INTO control.audit_log(actor,action,subject,at) VALUES ('fixture','email.sent',${id},${now})`;
    expect((await read()).sends.pending).toBe("0");
    await tx`UPDATE control.alert SET acknowledged_by='fixture'`;
    expect((await read()).verdict).toBe("attention"); // Failed attempts still visible after acknowledgement.
  }));
  it("shows skipped, sent and failed heartbeat results with cadence, age and setup", async () => scenario(async (tx, read) => {
    expect((await read()).heartbeat).toBeNull();
    for (const action of ["heartbeat.skipped", "heartbeat.sent", "heartbeat.failed"]) {
      await tx`DELETE FROM control.audit_log`;
      await tx`INSERT INTO control.audit_log(actor,action,subject,at,"after")
        VALUES ('system:runner',${action},'fixture',${now}::timestamptz-interval '5 minutes',
          ${tx.json({ cadence: "daily", scope: "global", configured: action !== "heartbeat.skipped" })})`;
      const status = await read();
      expect(status.heartbeat).toMatchObject({ action, cadence: "daily", age_seconds: 300 });
      const html = String(await StatusCard({ status }));
      expect(html).toContain(action === "heartbeat.skipped" ? "not configured" : action.replace("heartbeat.", ""));
      expect(html).toContain("5m ago");
      expect(html).toContain("Set MDP_HEARTBEAT_URL");
      expect(formatStatus(status)).toContain("daily · 5m ago");
    }
  }));
  it("publishes only cadence health and links overdue closes to recovery", async () => scenario(async (tx, read) => {
    expect(await healthSummary(tx)).toEqual({ checked_at: now, ok: true, overdue: [], next_step: "Open /ops and follow the runner recovery guide." });
    await tx`UPDATE control.cycle SET closed_at=${now}::timestamptz-interval '2 hours' WHERE cadence='hourly'`;
    const summary = await healthSummary(tx);
    expect(summary).toMatchObject({ ok: false, overdue: ["hourly"] });
    expect(Object.keys(summary).sort()).toEqual(["checked_at", "next_step", "ok", "overdue"]);
    expect(String(await StatusCard({ status: await read() }))).toContain('href="/runbooks/runners-held"');
    await tx`DELETE FROM control.cycle WHERE cadence='weekly'`;
    expect((await healthSummary(tx)).overdue).toEqual(["hourly", "weekly"]);
  }));
  it("compares running builds and handles missing configuration, malformed versions and outages", async () => scenario(async (_tx, read) => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ git_sha: "older-build" })));
    expect((await read()).versions.state).toBe("mismatch");
    expect((await read()).verdict).toBe("attention");
    vi.stubEnv("MDP_DATA_API_URL", "");
    expect((await read()).versions.state).toBe("unknown");
    vi.stubEnv("MDP_DATA_API_URL", "http://data.invalid");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ git_sha: null })));
    expect((await read()).verdict).toBe("attention");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ unexpected: true })));
    expect((await read()).verdict).toBe("broken");
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    expect((await read()).versions.state).toBe("unavailable");
  }));
});
it("refuses status to non-admin callers before any control read", async () => {
  const unsafe = vi.fn();
  const client = createRouterClient(router, { context: { identity: { ...identity, admin: false }, db: { unsafe } as unknown as DB } });
  await expect(client.status({})).rejects.toMatchObject({ status: 403 });
  expect(unsafe).not.toHaveBeenCalled();
});
