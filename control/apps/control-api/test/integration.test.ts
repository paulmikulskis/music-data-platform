import { describe, it, expect, afterAll, vi } from "vitest";
import postgres from "postgres";
import { execFileSync } from "node:child_process";
import { randomUUID, createHash } from "node:crypto";
import { z } from "zod";
import { createRouterClient } from "@orpc/server";
import { router } from "../src/router.js";
import { database } from "../src/db.js";
import { authenticate } from "../src/auth.js";
import { drainAlerts } from "../src/email.js";
const enabled = process.env.MDP_CONTROL_INTEGRATION === "1";
describe.skipIf(!enabled)(
  "real control boundaries on the isolated database",
  () => {
    const identity = {
      actor: "control-test",
      admin: true,
      tenant_id: null,
      tenant_slug: null,
    };
    const producer = () => {
      const configured = process.env.MDP_FUNCTIONS_TEST_URL;
      if (!configured) throw new Error("Set MDP_FUNCTIONS_TEST_URL for the real alert producer");
      return postgres(configured);
    };
    const client = () =>
      createRouterClient(router, { context: { identity, db: database() } });
    afterAll(async () => {
      if (enabled) await database().end();
    });
    it("refuses unresolved activation atomically and audits both success and refusal", async () => {
      const set = await client().targets.createSet({
        kind: `test-${randomUUID()}`,
        name: "Synthetic test set",
        tenant_id: null,
      });
      const imported = await client().targets.importTargets({
        target_set_id: set.id,
        csv: "platform,handle\nfixture,synthetic_target",
        dry_run: false,
      });
      const row = imported.rows[0];
      const id = String(row?.id);
      await expect(
        client().targets.bulkActivate({ ids: [id], active: true }),
      ).rejects.toMatchObject({ data: { error_class: "target_unresolved" } });
      expect(
        (await client().targets.list({ target_set_id: set.id }))[0]
          ?.activated_at,
      ).toBeNull();
      await client().targets.resolve({ id, platform_account_id: randomUUID() });
      expect(
        (await client().targets.bulkActivate({ ids: [id], active: true }))[0]
          ?.activated_at,
      ).not.toBeNull();
      const logs =
        await database()`SELECT after FROM control.audit_log WHERE actor='control-test' AND action='targets.bulkActivate' ORDER BY at DESC LIMIT 2`;
      expect(logs.map((l) => l.after.state)).toEqual(["succeeded", "failed"]);
      await database()`DELETE FROM control.target WHERE target_set_id=${set.id}`;
      await database()`DELETE FROM control.target_set WHERE id=${set.id}`;
    });
    it("undoes only untouched pending imports through an audited operation", async () => {
      const set=await client().targets.createSet({kind:`test-${randomUUID()}`,name:"Synthetic undo test",tenant_id:null});
      try {
        const imported=await client().targets.importTargets({target_set_id:set.id,csv:"platform,handle\nfixture,synthetic_undo",dry_run:false});
        const logs=await database()`SELECT id FROM control.audit_log WHERE action='targets.importTargets' AND subject=${set.id} AND after->>'state'='succeeded' ORDER BY at DESC LIMIT 1`;
        const auditId=String(logs[0]?.id);
        expect(await client().targets.undoImport({audit_id:auditId})).toEqual({count:1});
        expect(await client().targets.list({target_set_id:set.id})).toHaveLength(0);
        await expect(client().targets.undoImport({audit_id:auditId})).rejects.toMatchObject({status:409});
      } finally {await database()`DELETE FROM control.target WHERE target_set_id=${set.id}`;await database()`DELETE FROM control.target_set WHERE id=${set.id}`;}
    });
    it("freezes a promoter's target spec once; keys the spec already holds win", async () => {
      const set = await client().targets.createSet({kind:`test-${randomUUID()}`,name:"Synthetic spec test",tenant_id:null});
      try {
        const imported = await client().targets.importTargets({target_set_id:set.id,csv:"platform,platform_account_id,handle\nspotify,US:37i9dQZF1DXcBWIGoYBM5M,37i9dQZF1DXcBWIGoYBM5M",dry_run:false});
        const id = String(imported.rows[0]?.id);
        const first = await client().targets.setSpec({id,resource_kind:"playlist",canonical_key:"sp:playlist:US:37i9dQZF1DXcBWIGoYBM5M",params_json:{cadence:"daily",render:true},promotion_reason:"subject_list_promote"});
        expect(first).toMatchObject({target_id:id,resource_kind:"playlist",params_json:{cadence:"daily",render:true}});
        const second = await client().targets.setSpec({id,resource_kind:"playlist",canonical_key:"sp:playlist:US:other",params_json:{cadence:"weekly",owner_class:"curator"}});
        expect(second).toMatchObject({canonical_key:"sp:playlist:US:37i9dQZF1DXcBWIGoYBM5M",params_json:{cadence:"daily",render:true,owner_class:"curator"}});
        const logs = await database()`SELECT after FROM control.audit_log WHERE action='targets.setSpec' AND subject=${id} ORDER BY at DESC LIMIT 1`;
        expect(logs[0]?.after.state).toBe("succeeded");
        await expect(client().targets.setSpec({id:randomUUID(),resource_kind:"playlist",canonical_key:"x",params_json:{}})).rejects.toMatchObject({status:404});
      } finally {
        await database()`DELETE FROM control.target_spec WHERE target_id IN (SELECT id FROM control.target WHERE target_set_id=${set.id})`;
        await database()`DELETE FROM control.target WHERE target_set_id=${set.id}`;
        await database()`DELETE FROM control.target_set WHERE id=${set.id}`;
      }
    });
    it("serializes raises and never exceeds the ceiling", async () => {
      const rows =
        await database()`INSERT INTO control.budget(scope,period,cap_cents,soft_pct,hard_action,ceiling_cents) VALUES ('global','control-test',10,80,'warn',20) RETURNING id`;
      const id = String(rows[0]?.id);
      await expect(
        client().budgets.raise({ id, cap_cents: "21" }),
      ).rejects.toMatchObject({ data: { error_class: "budget_ceiling" } });
      expect(
        (await client().budgets.raise({ id, cap_cents: "20" })).cap_cents,
      ).toBe("20");
      await expect(
        client().budgets.raise({ id, cap_cents: "19" }),
      ).rejects.toMatchObject({ data: { error_class: "budget_ceiling" } });
      await database()`DELETE FROM control.budget WHERE id=${id}`;
    });
    it("accepts hashed machine keys and rejects revoked keys and reader administration", async () => {
      const secret = randomUUID();
      const hash = createHash("sha256").update(secret).digest("hex");
      const keys =
        await database()`INSERT INTO control.api_key(key_hash,label,role) VALUES (${hash},'control-test','reader') RETURNING id`;
      const request = new Request("http://localhost/ops", {
        headers: { "x-api-key": secret },
      });
      const machine = await authenticate(request);
      expect(machine.admin).toBe(false);
      const reader = createRouterClient(router, {
        context: { identity: machine, db: database() },
      });
      await expect(reader.tenants.list({})).rejects.toMatchObject({
        status: 403,
      });
      await database()`UPDATE control.api_key SET revoked_at=now() WHERE key_hash=${hash}`;
      await expect(authenticate(request)).rejects.toMatchObject({
        status: 401,
      });
      await database()`DELETE FROM control.api_key WHERE key_hash=${hash}`;
    });
    it("rejects inactive tenant keys with 403 and allows active tenant keys", async () => {
      const tenant = await client().tenants.create({ slug: `test-${randomUUID()}`, name: "Synthetic tenant" });
      const secret = randomUUID(), hash = createHash("sha256").update(secret).digest("hex");
      try {
        await database()`INSERT INTO control.api_key(key_hash,label,role,tenant_id) VALUES (${hash},'control-test','reader',${tenant.id})`;
        const req = new Request("http://localhost/", { headers: { "x-api-key": secret } });
        expect((await authenticate(req)).tenant_id).toBe(tenant.id);
        await client().tenants.patch({ id: tenant.id, status: "inactive" });
        await expect(authenticate(req)).rejects.toMatchObject({ status: 403, error_class: "forbidden" });
      } finally {
        await database()`DELETE FROM control.api_key WHERE key_hash=${hash}`;
        // Creating a tenant seeds its target sets.
        await database()`DELETE FROM control.target_set WHERE tenant_id=${tenant.id}`;
        await database()`DELETE FROM control.tenant WHERE id=${tenant.id}`;
      }
    });
    it("finds old unresolved alerts behind more than 100 resolved alerts and paginates", async () => {
      const subject = `control-${randomUUID()}`;
      const writer = producer();
      try {
        await writer`INSERT INTO control.alert(class,severity,subject_type,subject_id,opened_at) SELECT 'schema_drift','warning','test',${subject},now()-interval '2 days' FROM generate_series(1,101)`;
        await writer`INSERT INTO control.alert(class,severity,subject_type,subject_id) SELECT 'schema_drift','warning','test',${subject} FROM generate_series(1,105)`;
        await database()`UPDATE control.alert SET resolved_at=now() WHERE subject_id=${subject} AND opened_at>now()-interval '1 day'`;
        const first = await client().alerts.list({ subject_id: subject });
        const second = await client().alerts.list({ subject_id: subject, offset: 100 });
        expect(first).toHaveLength(100); expect(second).toHaveLength(1);
        expect(new Set([...first,...second].map(a=>a.id)).size).toBe(101);
        expect((await client().alerts.list({subject_id: subject, resolved:true}))).toHaveLength(100);
      } finally { await database()`UPDATE control.alert SET resolved_at=now() WHERE subject_id=${subject}`; await writer.end(); }
    });
    it("audits prior state, sanitized input and affected identity on failures", async () => {
      const budget = await client().budgets.create({ scope:"global",period:`test-${randomUUID()}`,cap_cents:"10",ceiling_cents:"20",soft_pct:80,hard_action:"warn" });
      try {
        await expect(client().budgets.raise({id:budget.id,cap_cents:"21"})).rejects.toMatchObject({ status:409 });
        const logs = await database()`SELECT subject,before,after FROM control.audit_log WHERE action='budgets.raise' AND subject=${budget.id} ORDER BY at DESC LIMIT 1`;
        expect(logs[0]?.before[0].cap_cents).toBe("10");
        expect(logs[0]?.after).toMatchObject({state:"failed",subject_type:"budget",subject_id:budget.id,input:{id:budget.id,cap_cents:"21"}});
      } finally { await database()`DELETE FROM control.budget WHERE id=${budget.id}`; }
    });
    it("dispatches Cloud Run now to the registered cadence and scope and records correlation", async () => {
      const previous = await client().dbt.runnerMode.get({});
      const jobId = `test-${randomUUID()}`, key = randomUUID();
      const job = await database()`SELECT job_id FROM control.dbt_job WHERE runner='cloud' AND cadence='hourly' AND scope='global'`;
      if (job.length) throw new Error("Cloud dispatch test requires an isolated cadence without a registered Cloud job");
      try {
        await database()`INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES (${jobId},'cloud','hourly','global')`;
        await client().dbt.runnerMode.set({runner:"cloud"});
        vi.stubEnv("DBT_CLOUD_TOKEN",randomUUID()); vi.stubEnv("DBT_CLOUD_ACCOUNT_ID","fixture");
        const mocked = vi.fn().mockResolvedValue(Response.json({ data: { id: 12345 } }));
        vi.stubGlobal("fetch", mocked);
        const result = await client().streamlines.runNow({source_key:"fixture_accounts",key,scope:"global"});
        expect(result).toMatchObject({path:"cloud",dbt_run_id:"12345",job_id:jobId});
        expect(mocked.mock.calls[0]?.[0]).toContain(`/jobs/${jobId}/run/`);
        const logs = await database()`SELECT after FROM control.audit_log WHERE action='streamlines.runNow' AND after->'input'->>'key'=${key}`;
        expect(logs[0]?.after).toMatchObject({state:"succeeded",input:{key}});
      } finally {
        vi.unstubAllGlobals(); vi.unstubAllEnvs();
        await client().dbt.runnerMode.set(previous);
        await database()`DELETE FROM control.dbt_job WHERE job_id=${jobId}`;
      }
    });
    it("retains the remote receipt and correlation when response decoding fails", async () => {
      const externalId=`test-${randomUUID()}`;
      vi.stubGlobal("fetch",vi.fn(async()=>Response.json({run_id:"invalid-remote-shape",status:"queued"})));
      try {
        await expect(client().dbt.webhook({event_id:randomUUID(),run_id:externalId,job_id:"local:daily",status:"running"})).rejects.toThrow();
        const logs=await database()`SELECT after FROM control.audit_log WHERE action='dbt.webhook' AND after->'input'->>'run_id'=${externalId} ORDER BY at DESC LIMIT 1`;
        expect(logs[0]?.after).toMatchObject({state:"failed",subject_type:"run",input:{run_id:externalId},remote:[{path:"/v1/dbt/webhook",result:{run_id:"invalid-remote-shape"}}]});
      } finally {vi.unstubAllGlobals();}
    });
    it("lists a stamp cycle's own dumps in lineage, not its scope's whole history", async () => {
      // The ledger writer creates the rows; no role may delete them, so they stay in a scope of their own.
      const db = producer();
      try {
        const scope = `tenant:${randomUUID()}`;
        const [warehouse] = await db`SELECT id FROM control.warehouse ORDER BY created_at LIMIT 1`;
        const cycles = await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at,manifest_mode,close_no)
          VALUES ('daily',${scope},${`test:${randomUUID()}`},'closed',now(),'stamp',1),
                 ('daily',${scope},${`test:${randomUUID()}`},'closed',now(),'stamp',2) RETURNING id,close_no`;
        const dumps: string[] = [];
        for (const cycle of cycles) {
          const [run] = await db`INSERT INTO control.run(kind,work_key,scope,warehouse_id,cycle_id)
            VALUES ('invoke',${randomUUID()},${scope},${warehouse?.id},${cycle.id}) RETURNING id`;
          const [dump] = await db`INSERT INTO control.dump(kind,run_id,uri_prefix,cycle_id,close_no)
            VALUES ('output',${run?.id},'lineage-test',${cycle.id},${cycle.close_no}) RETURNING id`;
          dumps.push(String(dump?.id));
        }
        const second = String(cycles[1]?.id);
        // The second close's stamp manifest holds both dumps; its lineage holds only its own.
        const manifest = await db`SELECT dump_id FROM control.cycle_manifest(${second})`;
        expect(manifest.map((m) => String(m.dump_id)).sort()).toEqual([...dumps].sort());
        const chain = await client().lineage.chain({ cycle_id: second });
        expect(chain.dumps.map((d) => String(d.id))).toEqual([dumps[1]]);
        expect(chain.receipts).toEqual([]);
      } finally {
        await db.end();
      }
    });
    it("delivers real service-produced warning alerts and commits earlier sends through later failure", async () => {
      const run = await client().dbt.webhook({event_id:randomUUID(),run_id:`test-${randomUUID()}`,job_id:"local:daily",status:"running"});
      const script = `import os, psycopg
from mdp_functions.control_db import alert
with psycopg.connect(os.environ['MDP_FUNCTIONS_TEST_URL']) as conn:
 for kind in ['schema_breaking','llm_budget_exceeded','cost_cap_hit','control_db_unavailable','object_store_unavailable','warehouse_unavailable','litellm_unavailable','dbt_api_unavailable']:
  alert(conn, '${run.run_id}', kind, '${run.run_id}')
`;
      execFileSync("uv", ["run","--project","../functions","python","-c",script],{stdio:"pipe"});
      vi.stubEnv("RESEND_API_KEY",randomUUID());vi.stubEnv("MDP_EMAIL_FROM","fixture@example.invalid");vi.stubEnv("MDP_EMAIL_TO","fixture@example.invalid");
      let calls = 0;
      vi.stubGlobal("fetch",vi.fn(async()=> ++calls === 2 ? new Response("",{status:503}) : Response.json({id:"fixture"})));
      const sentCount = async () => (await database()`SELECT count(*)::int AS n FROM control.audit_log l JOIN control.alert a ON a.id::text=l.subject WHERE a.run_id=${run.run_id} AND l.action='email.sent'`)[0]?.n;
      try {
        await drainAlerts();
        const first = await sentCount();
        expect(first).toBeGreaterThan(0);
        // The failed send waits out its backoff before the next attempt.
        await drainAlerts();
        expect(await sentCount()).toBe(first);
        await database()`UPDATE control.audit_log l SET at=l.at-interval '1 hour' FROM control.alert a WHERE a.id::text=l.subject AND a.run_id=${run.run_id} AND l.action='email.failed'`;
        await drainAlerts();
        const all = await database()`SELECT a.id,count(l.id)::int AS n FROM control.alert a LEFT JOIN control.audit_log l ON a.id::text=l.subject AND l.action='email.sent' WHERE a.run_id=${run.run_id} GROUP BY a.id`;
        expect(all).toHaveLength(8); expect(all.every(a=>a.n===1)).toBe(true);
      } finally { vi.unstubAllGlobals();vi.unstubAllEnvs(); await database()`UPDATE control.alert SET resolved_at=now() WHERE run_id=${run.run_id}`; }
    });
    const openAlert = async (alertClass = "warehouse_unavailable") => {
      const db = producer();
      try {
        const [row] = await db`INSERT INTO control.alert(class,severity,subject_type,subject_id) VALUES (${alertClass},'warning','email_test',${randomUUID()}) RETURNING id::text AS id`;
        return String(row?.id);
      } finally { await db.end(); }
    };
    const emailRows = (id: string) =>
      database()`SELECT action,after FROM control.audit_log WHERE subject=${id} AND action LIKE 'email.%' ORDER BY at,(after->>'attempt')::int NULLS FIRST`;
    // Counts Resend calls per alert through the idempotency key; ids in `failing` get a 503.
    const resendStub = (failing: Set<string>) => {
      vi.stubEnv("RESEND_API_KEY", randomUUID()); vi.stubEnv("MDP_EMAIL_FROM", "fixture@example.invalid"); vi.stubEnv("MDP_EMAIL_TO", "fixture@example.invalid");
      const calls = new Map<string, number>();
      vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
        const id = String(new Headers(init?.headers).get("idempotency-key")).replace("mdp-alert-", "");
        calls.set(id, (calls.get(id) ?? 0) + 1);
        return failing.has(id) ? new Response("", { status: 503 }) : Response.json({ id: "fixture" });
      }));
      return calls;
    };
    it("attempts nothing without a transport and records one skipped row per open alert", async () => {
      vi.stubEnv("RESEND_API_KEY", ""); vi.stubEnv("SMTP_URL", ""); vi.stubEnv("MDP_AUTH_MODE", "production");
      const fetchSpy = vi.fn(); vi.stubGlobal("fetch", fetchSpy);
      const [open, acknowledged, resolved] = [await openAlert(), await openAlert(), await openAlert()];
      try {
        await database()`UPDATE control.alert SET acknowledged_by='email-test' WHERE id=${acknowledged}`;
        await database()`UPDATE control.alert SET resolved_at=now() WHERE id=${resolved}`;
        for (let i = 0; i < 3; i++) await drainAlerts();
        expect((await emailRows(open)).map((r) => [r.action, r.after])).toMatchObject([["email.skipped", { state: "skipped", reason: "not_configured", error_class: "email_delivery_unconfigured" }]]);
        expect(await emailRows(acknowledged)).toHaveLength(0);
        expect(await emailRows(resolved)).toHaveLength(0);
        expect(fetchSpy).not.toHaveBeenCalled();
      } finally { vi.unstubAllGlobals(); vi.unstubAllEnvs(); await database()`UPDATE control.alert SET resolved_at=now() WHERE id IN (${open},${acknowledged})`; }
    });
    it("backs off failed deliveries, ignores unnumbered legacy failures, and gives up after ten attempts", async () => {
      const id = await openAlert();
      const calls = resendStub(new Set([id]));
      try {
        // Rows the unbounded loop wrote carry no attempt number and do not count.
        for (let i = 0; i < 3; i++)
          await database()`INSERT INTO control.audit_log(actor,action,subject,after) VALUES ('email-worker','email.failed',${id},${database().json({ state: "retry_pending", error_class: "email_delivery_failed" })})`;
        await drainAlerts();
        await drainAlerts();
        expect(calls.get(id)).toBe(1);
        for (let attempt = 2; attempt <= 12; attempt++) {
          await database()`UPDATE control.audit_log SET at=at-interval '2 hours' WHERE subject=${id} AND action='email.failed'`;
          await drainAlerts();
        }
        expect(calls.get(id)).toBe(10);
        const numbered = (await emailRows(id)).filter((r) => r.action === "email.failed" && r.after.attempt !== undefined);
        expect(numbered.map((r) => r.after.attempt)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
        expect(numbered.map((r) => r.after.retry_after_s ?? null)).toEqual([30, 60, 120, 240, 480, 960, 1920, 3600, 3600, null]);
        expect(numbered.at(-1)?.after.state).toBe("gave_up");
        const gaveUp = (await emailRows(id)).filter((r) => r.action === "email.gave_up");
        expect(gaveUp.map((r) => r.after)).toEqual([{ state: "gave_up", transport: "resend", attempts: 10, error_class: "email_delivery_failed" }]);
        expect((await emailRows(id)).some((r) => r.action === "email.sent")).toBe(false);
      } finally { vi.unstubAllGlobals(); vi.unstubAllEnvs(); await database()`UPDATE control.alert SET resolved_at=now() WHERE id=${id}`; }
    });
    it("never emails acknowledged or resolved alerts, including one acknowledged while its retry waits", async () => {
      const [open, acknowledged, resolved, retrying] = [await openAlert(), await openAlert(), await openAlert("schema_breaking"), await openAlert()];
      const calls = resendStub(new Set([retrying]));
      try {
        await database()`UPDATE control.alert SET acknowledged_by='email-test' WHERE id=${acknowledged}`;
        await database()`UPDATE control.alert SET resolved_at=now() WHERE id=${resolved}`;
        await drainAlerts();
        expect(calls.get(open)).toBe(1);
        expect(calls.get(retrying)).toBe(1);
        await database()`UPDATE control.alert SET acknowledged_by='email-test' WHERE id=${retrying}`;
        await database()`UPDATE control.audit_log SET at=at-interval '2 hours' WHERE subject=${retrying} AND action='email.failed'`;
        await drainAlerts();
        expect(calls.get(retrying)).toBe(1);
        expect(calls.has(acknowledged) || calls.has(resolved)).toBe(false);
        expect(await emailRows(acknowledged)).toHaveLength(0);
        expect(await emailRows(resolved)).toHaveLength(0);
        expect((await emailRows(open)).map((r) => r.action)).toEqual(["email.sent"]);
        expect((await emailRows(retrying)).map((r) => r.action)).toEqual(["email.failed"]);
      } finally { vi.unstubAllGlobals(); vi.unstubAllEnvs(); await database()`UPDATE control.alert SET resolved_at=now() WHERE id IN (${open},${acknowledged},${retrying})`; }
    });
    it("forwards dbt events idempotently and emails critical alerts with a runbook", async () => {
      const job = "local:daily";
      const payload = {
        event_id: randomUUID(),
        run_id: `test-${randomUUID()}`,
        job_id: job,
        status: "failed",
        message: "Synthetic webhook failure",
      };
      const normalized = {
        ...payload,
        status: z.literal("failed").parse("failed"),
      };
      const first = await client().dbt.webhook(normalized);
      const again = await client().dbt.webhook(normalized);
      expect(first.run_id).toBe(again.run_id);
      const events =
        await database()`SELECT count(*)::int AS n FROM control.run_event WHERE run_id=${first.run_id}`;
      expect(events[0]?.n).toBe(1);
      await drainAlerts();
      await drainAlerts();
      const mail =
        await database()`SELECT l.after FROM control.audit_log l JOIN control.alert a ON a.id::text=l.subject WHERE a.run_id=${first.run_id} AND l.action='email.sent'`;
      expect(mail.length).toBe(1);
      expect(mail[0]?.after.runbook_url).toContain("/runbooks/dbt-failure");
      const alerts = await client().alerts.list({});
      const alert = alerts.find((a) => a.run_id === first.run_id);
      expect(alert).toBeDefined();
      if (alert) await client().alerts.resolve({ id: alert.id });
    });
  },
);
