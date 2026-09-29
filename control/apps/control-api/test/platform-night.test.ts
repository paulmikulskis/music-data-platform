import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import postgres from "postgres";
import { errorHint, nightWindow } from "@mdp/contracts";
import { ingestionSql } from "@mdp/contracts/platform-sql";
import { createRouterClient } from "@orpc/server";
import { readNight, readRelationCounts } from "../src/platform-night.js";
import { router } from "../src/router.js";
import { night } from "../../showcase/server/night.js";
import { budget } from "../../showcase/server/read-budget.js";
import {
  controlClient,
  warehouse as warehouseClient,
} from "../../showcase/server/clients.js";
import {
  buildStamp,
  captureRelationCounts,
  exactCount,
  relationBuildKey,
  currentBuilds,
  countInputs,
} from "../../showcase/server/relation-counts.js";

// The marker belongs to the showcase package, outside this test's resolution root.
vi.mock("../../showcase/node_modules/server-only/index.js", () => ({}));
vi.mock("../../showcase/server/clients.js", () => ({
  controlClient: vi.fn(),
  warehouse: vi.fn(),
}));
const base = process.env.MDP_TENANTS_TEST_URL;
const window = {
  since: "2026-09-26T22:00:00.000Z",
  until: "2026-09-27T13:00:00.000Z",
};
const at = "2026-09-27T01:00:00.000Z";
const ended = "2026-09-27T02:00:00.000Z";
const built = "2026-09-27T03:00:00.000Z";
const id = () => randomUUID();
describe.skipIf(!base)("night and retained counts on PostgreSQL", () => {
  const database = `night_${id().replaceAll("-", "")}`;
  let root: postgres.Sql,
    db: postgres.Sql,
    control: postgres.Sql,
    wh: postgres.Sql;
  let warehouse: string, source: string, cycle: string;
  function url(role = "postgres") {
    const value = new URL(base!);
    value.pathname = "/" + database;
    value.username = role;
    value.password = role;
    return value.toString();
  }
  beforeAll(async () => {
    root = postgres(base!, { onnotice: () => {} });
    await root.unsafe(`CREATE DATABASE ${database}`);
    const ddl = execFileSync("pg_dump", [
      "--schema-only",
      "--schema=control",
      "--dbname",
      base!,
    ]);
    execFileSync("psql", ["-X", "-v", "ON_ERROR_STOP=1", "--dbname", url()], {
      input: ddl,
      stdio: ["pipe", "ignore", "pipe"],
    });
    db = postgres(url(), { onnotice: () => {} });
    control = postgres(url("control_rt"), { max: 4, onnotice: () => {} });
    wh = postgres(url("showcase_wh"), {
      max: 4,
      onnotice: () => {},
      connection: {
        statement_timeout: 5000,
        lock_timeout: 1000,
        default_transaction_read_only: true,
      },
    });
    await db.unsafe(`CREATE SCHEMA marts; CREATE SCHEMA catalog;
      CREATE TABLE marts._build(relation text PRIMARY KEY,cycle_id text,close_no bigint,built_at timestamptz);
      CREATE TABLE marts.mart_top_movers_current(n int);
      CREATE TABLE marts.mart_arrivals_current(n int);
      GRANT USAGE ON SCHEMA marts,catalog TO showcase_wh;
      GRANT SELECT ON marts.mart_top_movers_current,marts.mart_arrivals_current TO showcase_wh;`);
    const grants = readFileSync(
      new URL(
        "../../../../ops/fly/postgres/boot/init/10-warehouse-grants.sql",
        import.meta.url,
      ),
      "utf8",
    );
    const stamp = grants.slice(
      grants.indexOf("CREATE OR REPLACE FUNCTION catalog.snapshot_stamp"),
      grants.indexOf("REVOKE ALL ON FUNCTION catalog.snapshot_stamp"),
    );
    await db.unsafe(stamp);
    await db.unsafe(
      "GRANT EXECUTE ON FUNCTION catalog.snapshot_stamp(text) TO showcase_wh",
    );
  }, 30000);
  beforeEach(async () => {
    budget.invalidate("");
    await db.unsafe(
      "TRUNCATE control.showcase_relation_count,control.streamline,control.cycle,control.target_set,control.runner_mode CASCADE; TRUNCATE marts._build,marts.mart_top_movers_current,marts.mart_arrivals_current",
    );
    warehouse = String(
      (
        await db`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres',${database},'unused',true) ON CONFLICT (is_production) WHERE is_production DO UPDATE SET database=excluded.database RETURNING id`
      )[0]!.id,
    );
    source = String(
      (
        await db`INSERT INTO control.streamline(source_key,layer) VALUES ('sp_playlist','bronze') RETURNING id`
      )[0]!.id,
    );
    cycle = String(
      (
        await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at,closed_at,status,close_no) VALUES ('daily','global','core:night',${at},${ended},'closed',1) RETURNING id`
      )[0]!.id,
    );
    await db`INSERT INTO control.cycle_attempt(dbt_run_id,cycle_id,runner,reason_category) VALUES ('core:night',${cycle},'core','scheduled')`;
    await db`INSERT INTO control.runner_mode(id,runner) VALUES(true,'core')`;
  });
  afterAll(async () => {
    await Promise.all([control?.end(), wh?.end(), db?.end()]);
    if (root) {
      await root.unsafe(`DROP DATABASE ${database} WITH (FORCE)`);
      await root.end();
    }
  });
  async function run(status = "succeeded", key: string = id(), config = {}) {
    return String(
      (
        await db`INSERT INTO control.run(kind,work_key,scope,warehouse_id,streamline_id,cycle_id,resolved_config,status,created_at)
      VALUES ('invoke',${key},'global',${warehouse},${source},${cycle},${db.json(config)},${status},${at}) RETURNING id`
      )[0]!.id,
    );
  }
  async function attempt(
    runId: string,
    no = 1,
    status = "succeeded",
    dbt = "core:night",
    finish: string | null = ended,
  ) {
    return String(
      (
        await db`INSERT INTO control.run_attempt(run_id,attempt_no,dbt_run_id,started_at,ended_at,deadline_at,status)
      VALUES (${runId},${no},${dbt},${at},${finish},${window.until},${status}) RETURNING id`
      )[0]!.id,
    );
  }
  const read = () => readNight({ unsafe: db.unsafe.bind(db) }, window);
  const client = () =>
    createRouterClient(router, {
      context: {
        identity: {
          actor: "test",
          admin: true,
          staff: true,
          tenant_id: null,
          tenant_slug: null,
        },
        db: control,
      },
    });
  it("returns a retryable catalog error for a locked night read and succeeds after release", async () => {
    const runId = await run();
    await attempt(runId);
    const lock = await db.reserve();
    try {
      await lock`BEGIN`;
      await lock`LOCK control.run IN ACCESS EXCLUSIVE MODE`;
      await expect(client().platform.night(window)).rejects.toMatchObject({
        code: "SERVICE",
        status: 503,
        data: {
          error_class: "platform_read_timeout",
          next_step: errorHint("platform_read_timeout").next_step,
        },
      });
    } finally {
      await lock`ROLLBACK`;
      lock.release();
    }
    expect(
      (await client().platform.night(window)).runs.map((row) => row.run_id),
    ).toEqual([runId]);
  });
  async function targets(runId: string, count = 4) {
    const set = String(
      (
        await db`INSERT INTO control.target_set(name,kind) VALUES ('night','playlist') RETURNING id`
      )[0]!.id,
    );
    const revision = String(
      (
        await db`INSERT INTO control.target_export(cycle_id,target_set_id,member_count) VALUES (${cycle},${set},${count}) RETURNING id`
      )[0]!.id,
    );
    const members: string[] = [];
    for (let n = 0; n < count; n++) {
      const member = String(
        (
          await db`INSERT INTO control.target(target_set_id,platform,platform_account_id,handle) VALUES (${set},'spotify',${String(n)},${String(n)}) RETURNING id`
        )[0]!.id,
      );
      members.push(member);
      await db`INSERT INTO control.target_export_member(revision_id,target_id,resource_kind,target_json) VALUES (${revision},${member},'playlist','{"platform":"spotify"}')`;
    }
    await db`UPDATE control.run SET revision_id=${revision},resolved_config='{"target_coverage":{}}' WHERE id=${runId}`;
    return { members, revision };
  }
  it("keeps a failed run without dumps, queued and running work, and nullable end times", async () => {
    const failed = await run("failed");
    await attempt(failed, 1, "failed");
    const queued = await run("queued");
    const running = await run("running");
    await attempt(running, 1, "running", "core:night", null);
    const result = await read();
    expect(result.runs).toHaveLength(3);
    expect(result.runs.find((r) => r.run_id === failed)).toMatchObject({
      status: "failed",
      outputs: { dump_count: "0", rows_landed: "0" },
    });
    expect(result.runs.find((r) => r.run_id === queued)?.attempts).toEqual([]);
    expect(
      result.runs.find((r) => r.run_id === running)?.attempts[0]?.ended_at,
    ).toBeNull();
    expect(result.closes).toHaveLength(1);
  });
  it("keeps retries and superseded attempts without borrowing their latest coverage", async () => {
    const r = await run("partial");
    const a = await attempt(r, 1, "superseded");
    await db`INSERT INTO control.cycle_attempt(dbt_run_id,cycle_id,runner,reason_category) VALUES ('core:retry',${cycle},'core','other')`;
    await attempt(r, 2, "partial", "core:retry");
    const { members } = await targets(r);
    await db`INSERT INTO control.batch(run_id,index,target_ids,attempt_id,cursor_checkpoint,updated_at) VALUES (${r},0,${members},${a},${db.json({ completed_targets: [members[0]!, members[1]!, `rejected:${members[1]}`, `skipped:${members[2]}`, `stale_target:${members[3]}`] })},${ended})`;
    const result = (await read()).runs[0]!;
    expect(result.targets).toMatchObject({
      frozen_membership: 4,
      eligible: 3,
      succeeded: 1,
      skipped: 1,
    });
    expect(result.attempts.map((a) => a.trigger)).toEqual([
      "scheduled",
      "retry/restore",
    ]);
    expect(result.attempts.map((a) => a.targets)).toEqual([null, null]);
    expect(result.attempts[0]?.status).toBe("superseded");
  });
  it("unions repeated successes per reader, reconciles batches, and suppresses window ambiguity", async () => {
    const first = await run();
    const a = await attempt(first);
    const { members, revision } = await targets(first);
    for (let n = 0; n < 2; n++)
      await db`INSERT INTO control.batch(run_id,index,target_ids,attempt_id,cursor_checkpoint,updated_at) VALUES (${first},${n},${members},${a},${db.json({ completed_targets: [members[0]!, members[1]!] })},${ended})`;
    const second = await run();
    const b = await attempt(second);
    await db`UPDATE control.run SET revision_id=${revision},resolved_config='{"target_coverage":{}}' WHERE id=${second}`;
    await db`INSERT INTO control.batch(run_id,index,target_ids,attempt_id,cursor_checkpoint,updated_at) VALUES (${second},0,${members},${b},${db.json({ completed_targets: [members[0]!, members[1]!] })},${ended})`;
    expect((await read()).coverage[0]?.succeeded).toBe(2);
    await db`UPDATE control.run SET created_at='2026-09-26T21:00:00Z' WHERE id=${first}`;
    expect((await read()).coverage[0]?.succeeded).toBeNull();
  });
  it("counts multiple dumps and each production load once, apart from attempts", async () => {
    const r = await run();
    await attempt(r);
    await attempt(r, 2);
    for (let n = 0; n < 2; n++) {
      const dump = String(
        (
          await db`INSERT INTO control.dump(kind,run_id,uri_prefix) VALUES ('output',${r},'unused') RETURNING id`
        )[0]!.id,
      );
      await db`INSERT INTO control.load(dump_id,warehouse_id,target_table,status,rows_inserted) VALUES (${dump},${warehouse},'raw.observations','loaded',7),(${dump},${warehouse},'raw.other','loaded',3),(${dump},${warehouse},'raw.pending','pending',900)`;
    }
    expect((await read()).runs[0]?.outputs).toEqual({
      dump_count: "2",
      rows_landed: "20",
    });
  });
  it("distinguishes manual and unknown triggers from a scheduled opener", async () => {
    const manual = await run("succeeded", "manual:" + id());
    await attempt(manual, 1, "succeeded", "manual:operator");
    const unknown = await run();
    await attempt(unknown, 1, "succeeded", "unrecorded");
    const result = await read();
    expect(
      result.runs.find((r) => r.run_id === manual)?.attempts[0]?.trigger,
    ).toBe("manual");
    expect(
      result.runs.find((r) => r.run_id === unknown)?.attempts[0]?.trigger,
    ).toBe("unknown");
  });
  it("excludes tenant, fixture, sample, backfill, canary, workbench and nonproduction runs", async () => {
    for (const prefix of [
      "fixture",
      "test",
      "sample",
      "backfill",
      "canary",
      "workbench",
    ]) {
      const r = await run("succeeded", prefix + ":" + id());
      await attempt(r);
    }
    for (const config of [
      { fixture: true },
      { is_test: true },
      { sample: true },
      { fixture_scenario: "example" },
    ]) {
      const r = await run("succeeded", id(), config);
      await attempt(r);
    }
    const tenant = await run();
    await attempt(tenant);
    await db`UPDATE control.run SET scope='tenant:example' WHERE id=${tenant}`;
    const valid = await run();
    await attempt(valid);
    expect((await read()).runs.map((r) => r.run_id)).toEqual([valid]);
    await db.begin(async (tx) => {
      await tx`UPDATE control.warehouse SET is_production=false WHERE id=${warehouse}`;
      await tx`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres','new','unused',true)`;
    });
    expect((await read()).runs).toEqual([]);
  });
  it("returns only related alerts and uses catalog copy", async () => {
    const r = await run("failed");
    await attempt(r, 1, "failed");
    await db`INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,attempt_no,opened_at) VALUES ('partial_coverage','warning','run',${r},${r},1,${ended}),('surface_drift','critical','run',${r},${r},2,${ended}),('cadence_failed','critical','cadence','unrelated',NULL,NULL,${ended})`;
    const result = await read();
    expect(result.alerts).toHaveLength(1);
    expect(result.alerts[0]?.summary).toBeTruthy();
    expect(result.alerts[0]?.next_step).toBeTruthy();
  });
  it("refuses staff and validates UTC DST bounds without assuming a fifteen-hour night", async () => {
    const client = createRouterClient(router, {
      context: {
        identity: {
          actor: "test",
          admin: false,
          staff: true,
          tenant_id: null,
          tenant_slug: null,
        },
        db,
      },
    });
    await expect(client.platform.night(window)).rejects.toThrow();
    for (const bounds of [
      { since: "2026-03-07T23:00:00Z", until: "2026-03-08T13:00:00Z" },
      { since: "2026-10-31T22:00:00Z", until: "2026-11-01T14:00:00Z" },
    ])
      expect(nightWindow.safeParse(bounds).success).toBe(true);
    expect(
      nightWindow.safeParse({ ...window, until: "2026-09-30T00:00:00Z" })
        .success,
    ).toBe(false);
    expect(
      nightWindow.safeParse({ since: window.until, until: window.since })
        .success,
    ).toBe(false);
  });
  it.each(["result", "remote", "work_key"])(
    "excludes a probe-only cycle recorded by %s",
    async (shape) => {
      const key = id();
      const sample = await run("succeeded", `manual:${key}`);
      await attempt(sample, 1, "succeeded", "manual:sample");
      const receipt =
        shape === "work_key"
          ? { input: { key } }
          : shape === "remote"
            ? { remote: [{ result: { run_id: sample } }] }
            : { result: { run_id: sample } };
      await db`INSERT INTO control.audit_log(actor,action,subject,"after")
      VALUES ('test','streamlines.probe',${source},${db.json(receipt)})`;
      const result = await read();
      expect(result.runs).toEqual([]);
      expect(result.closes).toEqual([]);
    },
  );
  it("keeps recorded manual work but excludes the audited Run sample path", async () => {
    const manual = await run("succeeded", "manual:" + id());
    await attempt(manual, 1, "succeeded", "manual:operator");
    const sample = await run("succeeded", "manual:" + id());
    await attempt(sample, 1, "succeeded", "manual:sample");
    await db`INSERT INTO control.audit_log(actor,action,subject,"after")
      VALUES ('test','streamlines.probe',${source},${db.json({ state: "succeeded", result: { run_id: sample } })})`;
    expect((await read()).runs.map((row) => row.run_id)).toEqual([manual]);
    // The existing collection headline still excludes manual work.
    const dump = String(
      (
        await db`INSERT INTO control.dump(kind,run_id,streamline_id,cycle_id,uri_prefix)
      VALUES ('output',${manual},${source},${cycle},'unused') RETURNING id`
      )[0]!.id,
    );
    await db`INSERT INTO control.load(dump_id,warehouse_id,target_table,status,rows_inserted,loaded_at)
      VALUES (${dump},${warehouse},'raw.observations','loaded',9,${ended})`;
    const totals = await db.unsafe(ingestionSql, [window.since, window.until]);
    expect(totals).toEqual([]);
  });
  it("keeps pre-attempt runner failures and only verified cycle alerts", async () => {
    const r = await run("failed", "runner-failure:" + id());
    await db`INSERT INTO control.alert(class,severity,subject_type,subject_id,opened_at)
      VALUES ('cadence_failed','critical','cycle',${cycle},${ended}),
      ('cadence_failed','critical','cycle',${id()},${ended})`;
    const result = await read();
    expect(result.runs[0]?.run_id).toBe(r);
    expect(result.runs[0]?.attempts).toEqual([]);
    expect(result.alerts).toHaveLength(1);
  });
  it("caches one production window for sixty seconds and separates warehouse pins", async () => {
    await attempt(await run());
    const owner = { unsafe: db.unsafe.bind(db) };
    const first = await readNight(db, window, owner);
    await attempt(await run());
    expect((await readNight(db, window, owner)).runs).toHaveLength(1);
    expect(first.window).toMatchObject(window);
    await db.begin(async (tx) => {
      await tx`UPDATE control.warehouse SET is_production=false WHERE id=${warehouse}`;
      await tx`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres','replacement','unused',true)`;
    });
    expect((await readNight(db, window, owner)).runs).toHaveLength(0);
  });
  const input = () =>
    countInputs.find(
      (row) => row.relation === "marts.mart_top_movers_current",
    )!;
  async function stamp(time = built) {
    await db`INSERT INTO marts._build VALUES ('marts.mart_top_movers_current',${cycle},1,${time}) ON CONFLICT(relation) DO UPDATE SET built_at=excluded.built_at`;
    await db`INSERT INTO marts.mart_top_movers_current VALUES (1),(2),(3)`;
    return buildStamp.parse({
      relation: input().relation,
      cycle_id: cycle,
      close_no: "1",
      built_at: time,
    });
  }
  it.each([false, true])(
    "keeps runs through a warehouse lock with saved stamps=%s",
    async (saved) => {
      const runId = await run();
      await attempt(runId);
      await stamp();
      const person = {
        handle: "quartz",
        display_name: "Quartz",
        email: "quartz@example.invalid",
        admin_key: "test-key",
        api_key_id: id(),
      };
      vi.mocked(controlClient).mockReturnValue(client());
      vi.mocked(warehouseClient).mockReturnValue(wh);
      budget.setRunner("idle");
      const clock = vi.spyOn(Date, "now");
      const previous = saved ? await night(person, window) : null;
      if (previous) {
        expect(previous.value.ready).toHaveLength(1);
        const later = Date.now() + 60001;
        clock.mockReturnValue(later);
        budget.setRunner("idle");
      }
      const lock = await db.reserve();
      try {
        await lock`BEGIN`;
        await lock`LOCK marts._build IN ACCESS EXCLUSIVE MODE`;
        await expect(currentBuilds(wh, [input()])).rejects.toMatchObject({
          code: "55P03",
        });
        const degraded = await night(person, window);
        expect(degraded.value.runs.map((row) => row.run_id)).toEqual([runId]);
        expect(degraded.value.closes.map((row) => row.cycle_id)).toEqual([
          cycle,
        ]);
        expect(degraded.value.ready).toEqual(previous?.value.ready ?? []);
        expect(degraded.value.ready_state).toBe(
          saved ? "cached" : "unavailable",
        );
        expect(degraded.value.ready_saved_at).toBe(
          previous?.value.ready_saved_at ?? null,
        );
      } finally {
        await lock`ROLLBACK`;
        lock.release();
        clock.mockRestore();
      }
      budget.invalidate("global:night-builds:");
      const recovered = await night(person, window);
      expect(recovered.value.ready_state).toBe("live");
      expect(recovered.value.ready).toHaveLength(1);
    },
  );
  it("captures the production stamp while the target probe worker holds its lock", async () => {
    for (const file of ["warehouse.sql", "control.sql"]) {
      await db.unsafe(
        readFileSync(
          new URL(
            `../../../../ops/showcase/count-capture/${file}`,
            import.meta.url,
          ),
          "utf8",
        ),
      );
    }
    const reviewed = countInputs.filter(
      (row) => row.relation === "marts.mart_chart_history",
    );
    const stampJson = {
      built_at: "2026-09-27T14:55:00.132684+00:00",
      close_no: 105,
      cycle_id: "21c978b9-6cb0-42b5-bc2a-532cddb6ff15",
      relation: "marts.mart_chart_history",
    };
    const [found] =
      await wh`SELECT catalog.snapshot_stamp('marts.mart_chart_history') AS stamp`;
    expect(found?.stamp).toEqual(stampJson);
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    const holder = await control.reserve();
    try {
      await holder`SELECT pg_advisory_lock(hashtext('target-probes'))`;
      const captured = await captureRelationCounts(
        { control, warehouse: wh },
        reviewed,
      );
      expect(captured).toMatchObject({
        outcome: "captured",
        captured: 1,
        skipped: [],
      });
      expect(
        await db`SELECT relation,row_count,build_key FROM control.showcase_relation_count`,
      ).toEqual([
        {
          relation: stampJson.relation,
          row_count: "1",
          build_key: relationBuildKey(buildStamp.parse(stampJson)),
        },
      ]);
      expect(log).toHaveBeenCalledTimes(1);
      expect(log.mock.calls[0]?.[0]).toContain(
        '"event":"showcase_relation_count_capture"',
      );
      expect(log.mock.calls[0]?.[0]).toContain('"captured":1');
      expect(log.mock.calls[0]?.[0]).toContain("/ops");
      expect(log.mock.calls[0]?.[0]).not.toContain("fixture");
      log.mockClear();
      const repeated = await captureRelationCounts(
        { control, warehouse: wh },
        reviewed,
      );
      expect(repeated).toMatchObject({
        outcome: "skipped",
        captured: 0,
        skipped: [{ relation: stampJson.relation, reason: "already_captured" }],
      });
      expect(log).toHaveBeenCalledTimes(1);
    } finally {
      await holder`SELECT pg_advisory_unlock_all()`;
      holder.release();
      log.mockRestore();
    }
  });
  it("logs why a pass defers, and keeps unnamed Core locks conservative", async () => {
    await stamp();
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    const holder = await control.reserve();
    try {
      await holder`SELECT pg_advisory_lock(hashtext('core:hourly:global'))`;
      expect(
        await captureRelationCounts({ control, warehouse: wh }, [input()]),
      ).toMatchObject({
        outcome: "deferred",
        captured: 0,
        reason: "runner_unknown",
        stage: "runner",
      });
      expect(log).toHaveBeenCalledTimes(1);
      expect(
        await db`SELECT 1 FROM control.showcase_relation_count`,
      ).toHaveLength(0);
    } finally {
      await holder`SELECT pg_advisory_unlock_all()`;
      holder.release();
      log.mockRestore();
    }
  });
  it("recognizes an unnamed tenant Core lock with a negative hash", async () => {
    await db`INSERT INTO control.dbt_job(job_id,cadence,scope,runner)
      VALUES ('count-tenant','daily','tenant:fixture-1','core')`;
    const holder = await control.reserve();
    try {
      await holder`SELECT pg_advisory_lock(hashtext('core:daily:tenant:fixture-1'))`;
      expect(
        await captureRelationCounts({ control, warehouse: wh }, [input()]),
      ).toMatchObject({
        outcome: "deferred",
        reason: "runner_unknown",
        captured: 0,
      });
    } finally {
      await holder`SELECT pg_advisory_unlock_all()`;
      holder.release();
      await db`DELETE FROM control.dbt_job WHERE job_id='count-tenant'`;
    }
  });
  it("keeps close timing separate from build-ready evidence, and captures without viewers", async () => {
    const r = await run();
    await attempt(r);
    expect((await read()).closes[0]?.closed_at).toBe(ended);
    expect(await currentBuilds(wh, [input()])).toEqual([]);
    const build = await stamp();
    expect(
      new Date((await currentBuilds(wh, [input()]))[0]!.built_at).toISOString(),
    ).toBe(built);
    await captureRelationCounts({ control, warehouse: wh }, [input()]);
    const response = await readRelationCounts(control, {
      keys: [
        {
          relation: input().relation,
          build_key: relationBuildKey(build),
          input_hash: input().input_hash,
        },
      ],
    });
    expect(response.counts[0]).toMatchObject({
      denominator: "3",
      suppression: "none",
      capture: { basis: "exact", row_count: "3" },
    });
    // Restart and a second writer do not replace a completed fact.
    await Promise.all([
      captureRelationCounts({ control, warehouse: wh }, [input()]),
      captureRelationCounts({ control, warehouse: wh }, [input()]),
    ]);
    expect(
      await db`SELECT * FROM control.showcase_relation_count`,
    ).toHaveLength(1);
  });
  it("rejects a replaced build and leaves missed builds, filters and unstamped relations unknown", async () => {
    const old = await stamp();
    const replacement = await stamp("2026-09-27T14:00:00Z");
    expect(await exactCount(wh, input(), old)).toBeNull();
    await captureRelationCounts({ control, warehouse: wh }, [input()]);
    const keys = [
      {
        relation: input().relation,
        build_key: relationBuildKey(old),
        input_hash: input().input_hash,
      },
      {
        relation: input().relation,
        build_key: relationBuildKey(replacement),
        input_hash: "0".repeat(64),
      },
      {
        relation: input().relation,
        build_key: null,
        input_hash: input().input_hash,
      },
    ];
    const result = await readRelationCounts(control, { keys });
    expect(result.counts.map((row) => row.suppression)).toEqual([
      "not_captured",
      "different_input",
      "unstamped",
    ]);
    expect(result.counts.every((row) => row.denominator === null)).toBe(true);
    expect(result.counts[0]?.last_good?.row_count).toBe("6");
  });
  it("enforces immutable identities, exact nonnegative counts, uniqueness and narrow grants", async () => {
    const build = await stamp();
    await captureRelationCounts({ control, warehouse: wh }, [input()]);
    await expect(
      control`UPDATE control.showcase_relation_count SET input_hash=${"0".repeat(64)}`,
    ).rejects.toMatchObject({ code: "42501" });
    await expect(
      control`DELETE FROM control.showcase_relation_count`,
    ).rejects.toMatchObject({ code: "42501" });
    await expect(
      control`UPDATE control.showcase_relation_count SET row_count=-1`,
    ).rejects.toMatchObject({ code: "23514" });
    const permissions =
      await db`SELECT has_table_privilege('functions_rt','control.showcase_relation_count','SELECT') AS service,
      has_table_privilege('showcase_wh','control.showcase_relation_count','SELECT') AS warehouse`;
    expect(permissions[0]).toMatchObject({ service: false, warehouse: false });
    await expect(
      control`INSERT INTO control.showcase_relation_count SELECT * FROM control.showcase_relation_count`,
    ).rejects.toMatchObject({ code: "23505" });
    expect(relationBuildKey(build)).toContain(build.built_at);
  });
  it("leaves a timed-out capture unknown and retries after the table lock clears", async () => {
    const build = await stamp();
    let unlock: () => void = () => {};
    let acquired: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      unlock = resolve;
    });
    const locked = new Promise<void>((resolve) => {
      acquired = resolve;
    });
    const holder = db.begin(async (tx) => {
      await tx.unsafe(
        "LOCK TABLE marts.mart_top_movers_current IN ACCESS EXCLUSIVE MODE",
      );
      acquired();
      await held;
    });
    await locked;
    try {
      await captureRelationCounts({ control, warehouse: wh }, [input()]);
      expect(
        await db`SELECT 1 FROM control.showcase_relation_count`,
      ).toHaveLength(0);
    } finally {
      unlock();
      await holder;
    }
    await captureRelationCounts({ control, warehouse: wh }, [input()]);
    expect(
      (
        await readRelationCounts(control, {
          keys: [
            {
              relation: input().relation,
              build_key: relationBuildKey(build),
              input_hash: input().input_hash,
            },
          ],
        })
      ).counts[0]?.denominator,
    ).toBe("3");
  });
});
