import {
  afterAll,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync, execFile } from "node:child_process";
import { promisify } from "node:util";
import { serve, type ServerType } from "@hono/node-server";
import postgres from "postgres";
import { createRouterClient } from "@orpc/server";
import { platformSources, sourceFallback, sourceWording } from "@mdp/contracts";
import { router } from "../src/router.js";
import { app } from "../src/app.js";
import { costSql, readNextScheduledRun } from "../src/platform.js";
import * as platformReads from "../src/platform.js";
import { database, type DB } from "../src/db.js";
import { readInventory } from "../../showcase/server/inventory.js";
import { createPlatformEventReader } from "../../showcase/server/platform-events.js";

vi.mock("server-only", () => ({}));
import { captureInventory } from "../../showcase/server/inventory-snapshot.js";

const day = (ago: number) =>
  new Date(Date.now() - ago * 86400000).toISOString().slice(0, 10);
const since = day(7) + "T00:00:00Z";
const adminUrl = process.env.MDP_TENANTS_TEST_URL;
describe.skipIf(!adminUrl)("platform callers on isolated databases", () => {
  const suffix = randomUUID().replaceAll("-", "");
  const controlName = `platform_${suffix}`,
    warehouseName = `inventory_${suffix}`;
  const identity = {
    actor: "platform-test",
    admin: true,
    tenant_id: null,
    tenant_slug: null,
  };
  let admin: postgres.Sql,
    owner: postgres.Sql,
    wh: postgres.Sql,
    inventory: postgres.Sql,
    root: postgres.Sql;
  let warehouse: string, source: string, server: ServerType, base: string;
  const client = (admin = true) =>
    createRouterClient(router, {
      context: {
        identity: { ...identity, admin, staff: !admin },
        db: database(),
      },
    });
  function url(db: string, role?: string) {
    const u = new URL(adminUrl!);
    u.pathname = "/" + db;
    if (role) {
      u.username = role;
      u.password = role;
    }
    return u.toString();
  }
  beforeAll(async () => {
    root = postgres(url("postgres"), { onnotice: () => {} });
    await root.unsafe(`CREATE DATABASE ${controlName}`);
    await root.unsafe(`CREATE DATABASE ${warehouseName}`);
    const ddl = execFileSync("pg_dump", [
      "--schema-only",
      "--schema=control",
      "--dbname",
      adminUrl!,
    ]);
    execFileSync(
      "psql",
      ["-X", "-v", "ON_ERROR_STOP=1", "--dbname", url(controlName)],
      { input: ddl, stdio: ["pipe", "ignore", "pipe"] },
    );
    admin = postgres(url(controlName), { onnotice: () => {} });
    owner = postgres(url(controlName, "functions_rt"), { onnotice: () => {} });
    wh = postgres(url(warehouseName), { onnotice: () => {} });
    vi.stubEnv("MDP_CONTROL_RT_URL", url(controlName, "control_rt"));
    vi.stubEnv("MDP_READER_URL", "");
    inventory = postgres(url(warehouseName, "showcase_wh"), { max: 1 });
    vi.stubEnv("MDP_AUTH_MODE", "dev");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    warehouse = String(
      (
        await admin`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres',${warehouseName},'unused',true) RETURNING id`
      )[0]!.id,
    );
    source = String(
      (
        await admin`INSERT INTO control.streamline(source_key,layer,writes,reads,external,tenant_bound) VALUES ('platform_fixture','bronze',ARRAY['raw.observations'],'{}',true,false) RETURNING id`
      )[0]!.id,
    );
    await admin`INSERT INTO control.runner_mode(id,runner) VALUES(true,'core')`;
    base = await new Promise((resolve) => {
      server = serve(
        { fetch: app.fetch, hostname: "127.0.0.1", port: 0 },
        (info) => resolve(`http://127.0.0.1:${info.port}`),
      );
    });
  }, 30000);
  beforeEach(async () => {
    await admin.unsafe(
      "TRUNCATE control.cycle,control.run,control.alert,control.showcase_inventory,control.dbt_job CASCADE",
    );
    await admin`DELETE FROM control.streamline WHERE id<>${source}`;
    await admin`DELETE FROM control.rights_source`;
    await admin`INSERT INTO control.streamline(id,source_key,layer) VALUES (${source},'platform_fixture','bronze')
      ON CONFLICT (id) DO UPDATE SET source_key='platform_fixture',layer='bronze',enabled=true,cadence_tag=NULL`;
  });
  afterAll(async () => {
    if (server)
      await new Promise<void>((resolve) => server.close(() => resolve()));
    if (admin) await admin.end();
    if (owner) await owner.end();
    if (wh) await wh.end();
    if (inventory) await inventory.end();
    await database().end();
    if (root) {
      await root.unsafe(`DROP DATABASE ${controlName} WITH (FORCE)`);
      await root.unsafe(`DROP DATABASE ${warehouseName} WITH (FORCE)`);
      await root.end();
    }
    vi.unstubAllEnvs();
  });
  const cycle = async (opener = "scheduled-fixture") =>
    String(
      (
        await owner`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id) VALUES ('daily','global',${opener}) RETURNING id`
      )[0]!.id,
    );
  const run = async (
    cycleId: string,
    config = {},
    key: string = randomUUID(),
  ) =>
    String(
      (
        await owner`INSERT INTO control.run(kind,work_key,scope,warehouse_id,streamline_id,cycle_id,resolved_config,created_at) VALUES ('invoke',${key},'global',${warehouse},${source},${cycleId},${owner.json(config)},'2025-01-02') RETURNING id`
      )[0]!.id,
    );
  async function dump(
    runId: string,
    cycleId: string,
    table = "raw.observations",
    amount = 7,
    destination = warehouse,
  ) {
    const id = String(
      (
        await owner`INSERT INTO control.dump(kind,run_id,streamline_id,cycle_id,uri_prefix) VALUES ('output',${runId},${source},${cycleId},'file:///fixture') RETURNING id`
      )[0]!.id,
    );
    await owner`INSERT INTO control.load(dump_id,warehouse_id,target_table,status,rows_inserted,loaded_at) VALUES (${id},${destination},${table},'loaded',${amount},${day(6)})`;
    return id;
  }
  it("returns no sources when no streamlines exist", async () => {
    await admin`DELETE FROM control.streamline`;
    expect(await client().platform.sources({})).toMatchObject({
      sources: [],
      next_step: "Open /functions to see each source's runs.",
    });
  });
  it("counts production output loads by UTC day and keeps all-time load bounds", async () => {
    const c = await cycle();
    const r = await run(c);
    await admin`UPDATE control.streamline SET source_key='sp_playlist',cadence_tag='daily',enabled=false WHERE id=${source}`;
    const first = await dump(r, c, "raw.observations", 11);
    const earlier = await dump(r, c, "raw.observations", 7);
    const today = await dump(r, c, "raw.observations", 13);
    const midnight = day(0) + "T00:00:00.000Z";
    const firstTime = day(40) + "T01:02:03.000Z";
    const earlierTime = day(3) + "T04:05:06.000Z";
    await owner`UPDATE control.load SET loaded_at=${firstTime} WHERE dump_id=${first}`;
    await owner`UPDATE control.load SET loaded_at=${earlierTime} WHERE dump_id=${earlier}`;
    await owner`UPDATE control.load SET loaded_at=${midnight} WHERE dump_id=${today}`;
    await admin`INSERT INTO control.streamline(source_key,layer,enabled) VALUES ('hidden_source','bronze',false)`;
    const result = await client().platform.sources({});
    expect(result.sources).toHaveLength(2);
    expect(result.sources[0]).toMatchObject({
      source_key: "sp_playlist",
      display_name: sourceWording.sp_playlist!.name,
      brand: "spotify",
      family: "playlists",
      description: sourceWording.sp_playlist!.plain,
      cadence: "daily",
      enabled: false,
      first_collected: firstTime,
      last_read: midnight,
      entries_today: "13",
      targets: null,
      days: Array.from({ length: 14 }, (_, i) => ({
        day: day(13 - i),
        entries: i === 10 ? "7" : i === 13 ? "13" : "0",
      })),
    });
    await owner`UPDATE control.load SET loaded_at=${day(14)} WHERE dump_id IN (${earlier},${today})`;
    expect(
      (await client().platform.sources({})).sources.map(
        (row) => row.source_key,
      ),
    ).toContain("sp_playlist");
  });
  it("excludes test work, fixture configs, manual cycles, metadata and other warehouses", async () => {
    const c = await cycle();
    const valid = await run(c);
    const validDump = await dump(valid, c, "raw.observations", 5);
    for (const prefix of ["test", "fixture", "manual", "backfill", "canary"]) {
      await dump(
        await run(c, {}, prefix + ":" + randomUUID()),
        c,
        "raw.observations",
        500,
      );
    }
    for (const config of [
      { fixture: true },
      { is_test: true },
      { fixture_scenario: "normal" },
    ]) {
      await dump(await run(c, config), c, "raw.observations", 500);
    }
    for (const opener of [
      "manual:sample",
      "backfill:sample",
      "canary:sample",
    ]) {
      const otherCycle = await cycle(opener);
      await dump(await run(otherCycle), otherCycle, "raw.observations", 500);
    }
    for (const table of [
      "raw._run_completion",
      "raw.cost_ledger",
      "raw.cycles",
      "raw.cycle_inputs",
      "raw.cycle_attempts",
      "raw.dump_stamps",
      "raw.targets_current",
      "raw.targets_history",
      "marts.observations",
    ]) {
      await dump(valid, c, table, 500);
    }
    const otherWarehouse = String(
      (
        await admin`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref)
      VALUES ('postgres','source_copy','unused') RETURNING id`
      )[0]!.id,
    );
    await dump(valid, c, "raw.observations", 500, otherWarehouse);
    const otherRun = await run(c);
    await owner`UPDATE control.run SET warehouse_id=${otherWarehouse} WHERE id=${otherRun}`;
    await dump(otherRun, c, "raw.observations", 500, otherWarehouse);
    await dump(otherRun, c, "raw.observations", 500);
    const input = await dump(valid, c, "raw.observations", 500);
    await owner`UPDATE control.dump SET kind='input' WHERE id=${input}`;
    const pending = await dump(valid, c, "raw.observations", 500);
    await owner`UPDATE control.load SET status='pending' WHERE dump_id=${pending}`;
    const now = new Date().toISOString();
    await owner`UPDATE control.load SET loaded_at=${now}`;
    await owner`UPDATE control.load SET loaded_at=${day(40)} WHERE dump_id IN (
      SELECT d.id FROM control.dump d JOIN control.run r ON r.id=d.run_id WHERE r.work_key LIKE 'test:%'
    )`;
    const midnight = day(0) + "T00:00:00.000Z";
    await owner`UPDATE control.load SET loaded_at=${midnight} WHERE dump_id=${validDump}`;
    const result = (await client().platform.sources({})).sources[0]!;
    expect(result).toMatchObject({
      entries_today: "5",
      first_collected: midnight,
      last_read: midnight,
    });
    expect(result.days.map((d) => d.entries)).toEqual([
      ...Array.from({ length: 13 }, () => "0"),
      "5",
    ]);
  });
  it("finds load bounds through hundreds of runs with excluded runs at both ends", async () => {
    const c = await cycle();
    await admin`INSERT INTO control.run(kind,work_key,scope,warehouse_id,streamline_id,cycle_id,status,created_at)
      SELECT 'invoke',CASE WHEN n<=3 THEN 'test:' ELSE 'sources-history:' END || n,
        'global',${warehouse},${source},${c},
        CASE WHEN n>=398 THEN 'failed'::control.run_status ELSE 'succeeded'::control.run_status END,
        '2026-01-01T00:00:00Z'::timestamptz+n*interval '1 hour'
      FROM generate_series(1,400) n`;
    await admin`INSERT INTO control.dump(kind,run_id,streamline_id,cycle_id,uri_prefix)
      SELECT 'output',id,streamline_id,cycle_id,'file:///history'
      FROM control.run WHERE streamline_id=${source}`;
    await admin`INSERT INTO control.load(dump_id,warehouse_id,target_table,status,rows_inserted,loaded_at)
      SELECT d.id,r.warehouse_id,'raw.observations',
        CASE WHEN r.status='failed' THEN 'pending'::control.load_status ELSE 'loaded'::control.load_status END,
        7,r.created_at+interval '5 minutes'
      FROM control.dump d JOIN control.run r ON r.id=d.run_id WHERE r.streamline_id=${source}`;
    // An interior run's delayed load does not replace the newest qualifying run.
    await admin`UPDATE control.load SET loaded_at='2026-02-01T00:00:00Z'
      WHERE dump_id IN (SELECT d.id FROM control.dump d JOIN control.run r ON r.id=d.run_id
        WHERE r.work_key='sources-history:200')`;
    expect((await client().platform.sources({})).sources[0]).toMatchObject({
      first_collected: "2026-01-01T04:05:00.000Z",
      last_read: "2026-01-17T13:05:00.000Z",
    });
  });
  it("renders the other platform cards when sources cannot be read", async () => {
    const failure = vi
      .spyOn(platformReads, "readSources")
      .mockRejectedValueOnce(new Error("sources unavailable"));
    try {
      const page = await app.request(`${base}/ops/platform`);
      expect(page.status).toBe(200);
      const html = await page.text();
      expect(html).toContain("<h2>Sources</h2><p>Sources are unavailable.</p>");
      expect(html).toContain('<a href="/functions">See each source');
      for (const heading of [
        "Collected rows",
        "Inventory history",
        "Vendor usage",
        "Events",
      ]) {
        expect(html).toContain(`<h2>${heading}</h2>`);
      }
      expect(failure).toHaveBeenCalledOnce();
    } finally {
      failure.mockRestore();
    }
  });
  it("uses the latest scheduled run with a revision for targets", async () => {
    const set = String(
      (
        await admin`INSERT INTO control.target_set(name,kind) VALUES ('source targets','account') RETURNING id`
      )[0]!.id,
    );
    for (const [ago, members, key] of [
      [5, 19, randomUUID()],
      [3, 23, randomUUID()],
      [1, 99, "manual:" + randomUUID()],
    ] as const) {
      const c = await cycle();
      const r = await run(c, {}, key);
      const revision = String(
        (
          await owner`INSERT INTO control.target_export(cycle_id,target_set_id,member_count)
        VALUES (${c},${set},${members}) RETURNING id`
        )[0]!.id,
      );
      await owner`UPDATE control.run SET revision_id=${revision},created_at=${day(ago)} WHERE id=${r}`;
    }
    const newest = await run(await cycle());
    await owner`UPDATE control.run SET created_at=${day(0)} WHERE id=${newest}`;
    expect((await client().platform.sources({})).sources[0]!.targets).toBe(23);
  });
  it("counts each reader's frozen membership", async () => {
    const c = await cycle();
    const set = String(
      (
        await admin`INSERT INTO control.target_set(name,kind) VALUES ('reader membership','playlist') RETURNING id`
      )[0]!.id,
    );
    const revision = String(
      (
        await owner`INSERT INTO control.target_export(cycle_id,target_set_id,member_count) VALUES (${c},${set},139) RETURNING id`
      )[0]!.id,
    );
    for (const [platform, cadence, count] of [
      ["apple_music", "daily", 48],
      ["spotify", "daily", 36],
      ["spotify", "weekly", 20],
      ["shazam", "daily", 58],
      ["soundcloud", "daily", 4],
      ["soundcloud", "weekly", 3],
      ["charts", "hourly", 10],
    ] as const) {
      for (let n = 0; n < count; n++) {
        const id = String(
          (
            await admin`INSERT INTO control.target(target_set_id,platform,platform_account_id,handle) VALUES (${set},${platform},${platform + n},${"public_" + n}) RETURNING id`
          )[0]!.id,
        );
        await owner`INSERT INTO control.target_export_member(revision_id,target_id,target_json,params_json)
          VALUES (${revision},${id},${owner.json({ platform, platform_account_id: platform + n, handle: "public_" + n })},${owner.json({ cadence, weekday_bucket: n % 7 })})`;
      }
    }
    for (const key of [
      "am_playlist",
      "am_playlist_weekly",
      "sp_playlist",
      "sp_playlist_weekly",
      "sz_chart",
      "sc_curator_playlists",
      "sc_curator_playlists_weekly",
    ]) {
      const id = String(
        (
          await admin`INSERT INTO control.streamline(source_key,layer,cadence_tag) VALUES (${key},'bronze','daily') RETURNING id`
        )[0]!.id,
      );
      await owner`INSERT INTO control.run(kind,work_key,scope,warehouse_id,streamline_id,cycle_id,revision_id) VALUES ('invoke',${randomUUID()},'global',${warehouse},${id},${c},${revision})`;
    }
    const records = (await client().platform.sources({})).sources;
    const counts = Object.fromEntries(
      records
        .filter((row) => row.tracked)
        .map((row) => [row.source_key, row.tracked!.count]),
    );
    expect(counts).toEqual({
      am_playlist: 48,
      am_playlist_weekly: 0,
      sp_playlist: 36,
      sp_playlist_weekly: 20,
      sz_chart: 58,
      sc_curator_playlists: 4,
      sc_curator_playlists_weekly: 3,
    });
    expect(
      counts.am_playlist! + counts.sp_playlist! + counts.sp_playlist_weekly!,
    ).toBe(104);
    expect(
      records.find((row) => row.source_key === "sp_playlist")!.targets,
    ).toBe(139);
    // Live edits cannot change a frozen count.
    await admin`UPDATE control.target SET platform='other' WHERE target_set_id=${set}`;
    expect(
      (await client().platform.sources({})).sources.find(
        (row) => row.source_key === "sp_playlist",
      )!.tracked?.count,
    ).toBe(36);
  });
  it("links incident copy only to the source or its latest attempt", async () => {
    const c = await cycle();
    const r = await run(c);
    await owner`INSERT INTO control.run_attempt(run_id,attempt_no,deadline_at,status) VALUES (${r},1,now(),'failed'),(${r},2,now(),'running')`;
    await owner`INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,attempt_no) VALUES ('old_failure','critical','run',${r},${r},1)`;
    await owner`INSERT INTO control.alert(class,severity,subject_type,subject_id) VALUES ('unrelated','critical','cycle',${c})`;
    expect(
      (await client().platform.sources({})).sources[0]!.evidence?.incident,
    ).toBeNull();
    await owner`INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,attempt_no) VALUES ('current_failure','warning','run',${r},${r},2)`;
    const evidence = (await client().platform.sources({})).sources[0]!.evidence;
    expect(evidence?.attempt).toMatchObject({
      run_id: r,
      attempt_no: 2,
      status: "running",
    });
    expect(evidence?.incident).toMatchObject({
      class: "current_failure",
      attempt_no: 2,
      remediation: null,
    });
    expect(
      (
        await admin`SELECT count(*)::int AS count FROM control.run_attempt WHERE run_id=${r}`
      )[0]!.count,
    ).toBe(2);
  });

  it("uses registry wording or the source key for unknown sources and sorts equal counts by name", async () => {
    await admin`INSERT INTO control.rights_source(source_key,provider,category) VALUES ('platform_fixture','A provider','other')`;
    for (const layer of ["silver", "gold", "universal"]) {
      await admin`INSERT INTO control.streamline(source_key,layer) VALUES (${"unknown_" + layer},${layer})`;
    }
    const result = (await client().platform.sources({})).sources;
    expect(result.map((row) => row.display_name)).toEqual([
      "A provider",
      "unknown_gold",
      "unknown_silver",
      "unknown_universal",
    ]);
    expect(result[0]).toMatchObject({
      family: "other",
      description: sourceFallback,
      brand: null,
      entries_today: "0",
      first_collected: null,
      last_read: null,
      targets: null,
      cadence: null,
      enabled: true,
    });
    for (const row of result.slice(1)) {
      expect(row).toMatchObject({
        family: "derived",
        brand: null,
        description: sourceFallback,
      });
      expect(row.days).toHaveLength(14);
      expect(row.days.every((d) => d.entries === "0")).toBe(true);
    }
    const c = await cycle();
    const loaded = await dump(await run(c), c, "raw.observations", 9);
    await owner`UPDATE control.dump SET streamline_id=(SELECT id FROM control.streamline WHERE source_key='unknown_silver') WHERE id=${loaded}`;
    await owner`UPDATE control.load SET loaded_at=${day(0)} WHERE dump_id=${loaded}`;
    expect((await client().platform.sources({})).sources[0]!.source_key).toBe(
      "unknown_silver",
    );
  });
  it("checks sources admin access through the typed client and HTTP and serves the CLI", async () => {
    await expect(client(false).platform.sources({})).rejects.toMatchObject({
      status: 403,
      data: { error_class: "forbidden" },
    });
    const key = randomUUID();
    await admin`INSERT INTO control.api_key(key_hash,role,label) VALUES (${createHash("sha256").update(key).digest("hex")},'staff','sources test')`;
    const denied = await app.request(`${base}/api/platform/sources`, {
      headers: { "x-api-key": key },
    });
    expect(denied.status).toBe(403);
    expect(await denied.json()).toMatchObject({
      data: { error_class: "forbidden" },
    });
    const response = await app.request(`${base}/api/platform/sources`);
    expect(response.status).toBe(200);
    const payload = platformSources.parse(await response.json());
    expect(payload.sources[0]!.source_key).toBe("platform_fixture");
    const result = await promisify(execFile)(
      "timeout",
      ["20", "pnpm", "mdp", "platform", "sources"],
      {
        env: {
          PATH: process.env.PATH,
          HOME: process.env.HOME,
          MDP_AUTH_MODE: "dev",
          MDP_CONTROL_API_URL: base,
        },
        timeout: 25000,
      },
    );
    // pnpm prints its script banner before the JSON.
    const output = platformSources.parse(
      JSON.parse(result.stdout.slice(result.stdout.indexOf("{"))),
    );
    expect(output.sources.map((row) => ({ ...row, evidence: null }))).toEqual(
      payload.sources.map((row) => ({ ...row, evidence: null })),
    );
    const page = await app.request(`${base}/ops/platform`);
    expect(page.status).toBe(200);
    const html = await page.text();
    expect(html).toContain("<h2>Sources</h2>");
    expect(html).toContain("See each source");
  }, 30000);
  it("suppresses current and historical row totals when synthetic snapshots remain", async () => {
    const c = await cycle(),
      r = await run(c);
    await dump(r, c);
    await dump(r, c, "raw._run_completion", 500);
    await dump(await run(c, { fixture: true }), c, "raw.observations", 500);
    await dump(
      await run(c, { fixture_scenario: "normal" }),
      c,
      "raw.observations",
      500,
    );
    await dump(await run(c, { is_test: true }), c, "raw.observations", 500);
    const manual = await cycle("manual:sample");
    await dump(await run(manual), manual, "raw.observations", 500);
    const other = String(
      (
        await admin`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref) VALUES ('postgres','copy','unused') RETURNING id`
      )[0]!.id,
    );
    await dump(r, c, "raw.observations", 500, other);
    await wh.unsafe(`CREATE SCHEMA raw; CREATE SCHEMA marts; CREATE SCHEMA sandbox_example; CREATE SCHEMA tenant_example_marts;
      CREATE TABLE raw.observations(id int); INSERT INTO raw.observations SELECT generate_series(1,3); ANALYZE raw.observations;
      CREATE TABLE raw.account_snapshots(platform text,platform_account_id text,handle text);
      INSERT INTO raw.account_snapshots SELECT 'fixture','fixture-' || lpad(n::text,3,'0'),'fixture_account_' || lpad(n::text,3,'0') FROM generate_series(1,3) n;
      INSERT INTO raw.account_snapshots VALUES ('fixture','public-artist','public_artist');
      ANALYZE raw.account_snapshots;
      CREATE TABLE raw._run_completion(id int); CREATE TABLE raw.observations__dbt_tmp(id int);
      CREATE TABLE marts.unknown(id int); CREATE VIEW marts.visible AS SELECT 1 AS id;
      CREATE TABLE marts.parent(id int) PARTITION BY RANGE(id); CREATE TABLE marts.leaf PARTITION OF marts.parent FOR VALUES FROM (0) TO (10); ANALYZE marts.leaf;
      CREATE TABLE sandbox_example.private(id int); CREATE TABLE tenant_example_marts.facts(id int); ANALYZE tenant_example_marts.facts;`);
    await admin`INSERT INTO control.showcase_inventory VALUES(${day(5)},${warehouse},'raw',2,7,8192,${day(5) + "T00:30:00Z"},true)`;
    const result = await client().platform.holdings({ since });
    expect(result.ingestion.rows).toEqual([
      { day: day(6), source_key: "platform_fixture", rows_inserted: "7" },
    ]);
    expect(result.ingestion.summary).toMatchObject({
      rows_inserted: "7",
      sources_live: "1",
      today_rows: "0",
      today_sources: "0",
      today_measured: false,
      days: [{ day: day(6), rows_inserted: "7" }],
    });
    expect(result.vendor_cost.has_current_rows).toBe(false);
    expect(result).not.toHaveProperty("inventory_now");
    const layers = await readInventory(inventory);
    expect(layers.find((r) => r.layer === "raw")).toMatchObject({
      relations: 2,
      rows_est: null,
      complete: false,
    });
    expect(layers.find((r) => r.layer === "marts")).toMatchObject({
      relations: 2,
      rows_est: null,
      complete: false,
    });
    expect(layers.map((r) => r.layer).sort()).toEqual([
      "marts",
      "raw",
      "tenant",
    ]);
    expect(result.inventory_history.days.flatMap((d) => d.layers)).toEqual([
      expect.objectContaining({
        layer: "raw",
        rows_est: null,
        complete: false,
      }),
    ]);
    expect(
      layers.every((layer) => layer.rows_est === null && !layer.complete),
    ).toBe(true);
    expect(
      (await wh`SELECT count(*)::int AS n FROM raw.account_snapshots`)[0]?.n,
    ).toBe(4);
    expect(
      (await admin`SELECT rows_est::text FROM control.showcase_inventory`)[0]
        ?.rows_est,
    ).toBe("7");
    expect(result.inventory_history.unavailable_before).toBe(day(5));
    expect(
      result.inventory_history.days.slice(0, 4).map((d) => d.state),
    ).toEqual(["unavailable", "unavailable", "available", "unavailable"]);
  });
  it("reads schedule eligibility from declared timezones and only scheduled cycle credit", async () => {
    const at = new Date("2026-09-25T04:00:00Z");
    expect(await readNextScheduledRun(database(), at)).toBeNull();
    await admin`INSERT INTO control.dbt_job(job_id,runner,cadence,scope,timezone,due_hour) VALUES ('daily','core','daily','global','America/New_York',2)`;
    expect(await readNextScheduledRun(database(), at)).toBe(
      "2026-09-25T06:00:00.000Z",
    );
    const c = await cycle("manual:fixture");
    await owner`UPDATE control.cycle SET opened_at='2026-09-25T04:00:00Z',status='closed',closed_at='2026-09-25T04:01:00Z' WHERE id=${c}`;
    expect(await readNextScheduledRun(database(), at)).toBe(
      "2026-09-25T06:00:00.000Z",
    );
    await owner`UPDATE control.cycle SET opened_by_dbt_run_id='scheduled-fixture' WHERE id=${c}`;
    expect(await readNextScheduledRun(database(), at)).toBe(
      "2026-09-26T06:00:00.000Z",
    );
    await admin`DELETE FROM control.cycle`;
    await admin`UPDATE control.dbt_job SET cadence='weekly',due_weekday=1,due_hour=3`;
    expect(await readNextScheduledRun(database(), at)).toBe(at.toISOString());
    const weekly = await cycle();
    await owner`UPDATE control.cycle SET cadence='weekly',opened_at='2026-09-21T07:00:00Z',status='closed',closed_at='2026-09-21T08:00:00Z' WHERE id=${weekly}`;
    expect(await readNextScheduledRun(database(), at)).toBe(
      "2026-09-28T07:00:00.000Z",
    );
    expect((await client().platform.events({})).runner).toHaveProperty(
      "next_scheduled_at",
    );
    await expect(client(false).platform.events({})).rejects.toMatchObject({
      status: 403,
    });
  });
  it("waits for the next period after two failures and for hourly credit to expire", async () => {
    const at = new Date("2026-09-25T12:00:00Z");
    await admin`INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('daily','core','daily','global')`;
    for (const time of ["10:00", "11:00"]) {
      const c = await cycle();
      await owner`UPDATE control.cycle SET opened_at=${"2026-09-25T" + time + ":00Z"} WHERE id=${c}`;
    }
    expect(await readNextScheduledRun(database(), at)).toBe(
      "2026-09-26T02:00:00.000Z",
    );
    await admin`UPDATE control.dbt_job SET cadence='hourly'`;
    await admin`DELETE FROM control.cycle`;
    for (const time of ["11:30", "11:40"]) {
      const c = await cycle();
      await owner`UPDATE control.cycle SET cadence='hourly',opened_at=${"2026-09-25T" + time + ":00Z"} WHERE id=${c}`;
    }
    expect(await readNextScheduledRun(database(), at)).toBe(
      "2026-09-25T12:15:00.000Z",
    );
    await owner`UPDATE control.cycle SET status='closed',closed_at=opened_at+interval '1 minute' WHERE opened_at='2026-09-25T11:40:00Z'`;
    expect(await readNextScheduledRun(database(), at)).toBe(
      "2026-09-25T12:25:00.000Z",
    );
  });
  it("captures storage through showcase_wh once per UTC day and rolls back failures", async () => {
    const now = new Date("2026-09-25T00:30:00Z");
    const connection = { control: database(), warehouse: inventory };
    expect(await captureInventory(connection, now)).toBe("captured");
    const saved =
      await admin`SELECT layer,rows_est::text,complete FROM control.showcase_inventory ORDER BY layer`;
    expect(saved).toContainEqual({
      layer: "marts",
      rows_est: null,
      complete: false,
    });
    expect(await captureInventory(connection, now)).toBe("recorded");
    // Later captures must fail or stop before they read the warehouse.
    const failure = vi
      .spyOn(inventory, "begin")
      .mockRejectedValue(new Error("test catalog failure"));
    try {
      await expect(
        captureInventory(connection, new Date("2026-09-26T00:30:00Z")),
      ).rejects.toThrow("/runbooks/showcase-inventory-failed");
      expect(
        await admin`SELECT 1 FROM control.showcase_inventory WHERE day='2026-09-26'`,
      ).toHaveLength(0);
      const lock = await admin.reserve();
      try {
        await lock`BEGIN`;
        await lock`SELECT pg_advisory_xact_lock(hashtext('showcase_inventory_capture'))`;
        expect(
          await captureInventory(connection, new Date("2026-09-27T00:30:00Z")),
        ).toBe("locked");
      } finally {
        await lock`ROLLBACK`;
        lock.release();
      }
      await admin`UPDATE control.runner_mode SET runner='cloud'`;
      expect(await captureInventory(connection, now)).toBe("deferred");
    } finally {
      failure.mockRestore();
      await admin`UPDATE control.runner_mode SET runner='core'`;
    }
  });
  it("normalizes each current cost row, rounds the sum, and keeps replacement usage days", async () => {
    const c = await cycle(),
      r = await run(c);
    async function cost(
      cents: number,
      micro: number,
      origin: string,
      current: boolean,
      request: string,
    ) {
      await owner`INSERT INTO control.cost_ledger(run_id,vendor,provider_request_id,unit,quantity,cost_cents,cost_microcents,origin,is_current,occurred_at)
        VALUES (${r},'litellm',${request},'token',1,${cents},${micro},${origin},${current},${day(6) + "T03:04:05Z"})`;
    }
    await cost(100, 0, "estimate", false, "replaced");
    await cost(2, 0, "litellm", true, "replaced");
    await cost(90, 400000, "estimate", true, "small-one");
    await cost(0, 400000, "estimate", true, "small-two");
    const result = await client().platform.holdings({ since });
    expect(result.vendor_cost).toMatchObject({
      cost_cents: "3",
      has_current_rows: true,
      label: "partly reconciled",
      infrastructure_included: false,
      days: [{ day: day(6), cost_cents: "3", label: "partly reconciled" }],
    });
    await owner`UPDATE control.cost_ledger SET is_current=false WHERE origin='estimate'`;
    expect(
      (await client().platform.holdings({ since })).vendor_cost.label,
    ).toBe("reconciled");
    await owner`UPDATE control.cost_ledger SET is_current=(origin='estimate')`;
    expect(
      (await client().platform.holdings({ since })).vendor_cost.label,
    ).toBe("estimate");
  });
  it("refuses holdings older than 90 days or in the future with a next step", async () => {
    for (const start of [day(91), day(-1)]) {
      await expect(
        client().platform.holdings({ since: start + "T00:00:00Z" }),
      ).rejects.toMatchObject({
        data: {
          error_class: "holdings_window_refused",
          next_step: expect.stringContaining("/ops/platform"),
        },
      });
    }
    expect(
      (
        await client().platform.holdings({
          since: new Date(Date.now() - 89 * 86400000).toISOString(),
        })
      ).inventory_history.days.length,
    ).toBeLessThanOrEqual(91);
  });
  it("keeps cost days and the total together when reconciliation commits during the read", async () => {
    const c = await cycle(),
      r = await run(c);
    await owner`INSERT INTO control.cost_ledger(run_id,vendor,provider_request_id,unit,quantity,cost_cents,origin,occurred_at)
      VALUES(${r},'litellm','concurrent-cost','token',1,2,'estimate',${day(6)})`;
    let costReads = 0;
    const pool: DB = {
      unsafe: database().unsafe,
      begin: (async (_options: string, read: (tx: DB) => Promise<unknown>) =>
        database().begin("read only", (tx) =>
          read({
            unsafe: ((query: string, params: never[]) => {
              if (query !== costSql) return tx.unsafe(query, params);
              costReads++;
              return tx.unsafe(query, params).then(async (result) => {
                await owner.begin(async (writer) => {
                  await writer`UPDATE control.cost_ledger SET is_current=false WHERE provider_request_id='concurrent-cost'`;
                  await writer`INSERT INTO control.cost_ledger(run_id,vendor,provider_request_id,unit,quantity,cost_cents,origin,occurred_at)
                VALUES(${r},'litellm','concurrent-cost','token',1,7,'litellm',${day(6)})`;
                });
                return result;
              });
            }) as DB["unsafe"],
          }),
        )) as NonNullable<DB["begin"]>,
    };
    const observed = createRouterClient(router, {
      context: { identity, db: pool },
    });
    expect(
      (await observed.platform.holdings({ since })).vendor_cost,
    ).toMatchObject({
      cost_cents: "2",
      label: "estimate",
      days: [{ cost_cents: "2", label: "estimate" }],
    });
    expect(costReads).toBe(1);
    expect(
      (await client().platform.holdings({ since })).vendor_cost,
    ).toMatchObject({
      cost_cents: "7",
      label: "reconciled",
      days: [{ cost_cents: "7", label: "reconciled" }],
    });
  });
  it("stops at 10,001 overlapping events without losing keys or advancing the cursor", async () => {
    await owner`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at)
      SELECT 'daily','global','scheduled-capacity','2025-01-03T12:00:00Z' FROM generate_series(1,10001)`;
    const positions: (string | undefined)[] = [];
    const next = createPlatformEventReader(async (input) => {
      positions.push(input.after);
      return client().platform.events(input);
    });
    const delivered = new Set<string>();
    for (let n = 0; n < 20; n++) {
      const page = await next(500);
      expect(page.has_more).toBe(true);
      for (const event of page.events) {
        expect(delivered.has(event.key)).toBe(false);
        delivered.add(event.key);
      }
    }
    expect(delivered.size).toBe(10000);
    for (let n = 0; n < 3; n++)
      await expect(next(500)).rejects.toMatchObject({
        error_class: "platform_events_capacity",
        next_step: expect.stringContaining("/ops/platform"),
      });
    expect(new Set(positions.slice(-3)).size).toBe(1);
    // Advancing the time window expires old keys only after they cannot replay.
    await owner`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at)
      VALUES('daily','global','scheduled-fresh','2025-01-03T12:02:01Z')`;
    expect((await next(500)).events).toHaveLength(2);
    expect((await next(500)).events).toEqual([]);
    await owner`UPDATE control.cycle SET opened_at='2025-01-03T12:04:02Z' WHERE opened_by_dbt_run_id='scheduled-fresh'`;
    expect((await next(500)).events).toEqual([]);
    expect((await next(500)).events).toEqual([]);
  }, 20000);
  it("ends a locked platform read at the local lock deadline", async () => {
    const lock = await admin.reserve();
    try {
      await lock`BEGIN`;
      await lock`LOCK control.cycle IN ACCESS EXCLUSIVE MODE`;
      await expect(client().platform.events({})).rejects.toMatchObject({
        data: { error_class: "platform_read_timeout" },
      });
    } finally {
      await lock`ROLLBACK`;
      lock.release();
    }
    expect((await client().platform.events({})).events).toEqual([]);
  });
  it("pages all event families with tied microsecond times in time and key order", async () => {
    const c = await cycle(),
      r = await run(c),
      manual = await cycle("manual:example");
    await owner`UPDATE control.cycle SET opened_at='2025-01-02T12:00:00.123456Z',closed_at='2025-01-02T12:00:00.123456Z',status='closed'`;
    await owner`UPDATE control.run SET created_at='2025-01-02T12:00:00.123456Z',updated_at='2025-01-02T12:00:00.123456Z',status='succeeded' WHERE id=${r}`;
    const a = String(
      (
        await owner`INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,opened_at)
      VALUES('fixture','warning','run',${r},${r},'2025-01-02T12:00:00.123456Z') RETURNING id`
      )[0]!.id,
    );
    await database().unsafe(
      "UPDATE control.alert SET resolved_at='2025-01-02T12:00:00.123456Z' WHERE id=$1",
      [a],
    );
    const expected = (await client().platform.events({ limit: 100 })).events;
    expect(expected).toHaveLength(8);
    expect(new Set(expected.map((e) => e.kind)).size).toBe(6);
    expect(expected.map((e) => e.key)).toEqual(
      expected.map((e) => e.key).sort(),
    );
    expect(
      expected.every(
        (e) =>
          e.key === `${e.kind}:${e.subject_id}` &&
          e.occurred_at === "2025-01-02T12:00:00.123456Z",
      ),
    ).toBe(true);
    expect(
      expected
        .filter((e) => e.cycle_id === manual)
        .every((e) => e.scheduled === false),
    ).toBe(true);
    expect(
      expected
        .filter((e) => e.cycle_id === c)
        .every((e) => e.scheduled === true),
    ).toBe(true);
    const seen = [];
    let after: string | undefined;
    for (let n = 0; n < 8; n++) {
      const page = await client().platform.events({ after, limit: 1 });
      expect(page.events).toHaveLength(1);
      expect(page.has_more).toBe(n < 7);
      seen.push(...page.events);
      after = page.next_cursor;
    }
    expect(seen).toEqual(expected);
    expect(
      (await client().platform.events({ after, limit: 100 })).events,
    ).toEqual(expected);
  });
  it("reads without blocking writes and shows a late old-run settlement once through the poll reader", async () => {
    const c = await cycle(),
      r = await run(c);
    await owner`UPDATE control.cycle SET opened_at='2025-01-03T12:00:00Z' WHERE id=${c}`;
    const pending = await owner.reserve();
    const next = createPlatformEventReader(client().platform.events);
    try {
      await pending`BEGIN`;
      await pending`UPDATE control.run SET status='succeeded',updated_at='2025-01-03T12:01:00Z' WHERE id=${r}`;
      // This separate write fails quickly if an event trigger holds a global writer lock.
      await owner.begin(async (tx) => {
        await tx`SET LOCAL lock_timeout='500ms'`;
        await tx`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,opened_at)
          VALUES('daily','global','scheduled-later','2025-01-03T12:02:00Z')`;
      });
      const first = await next(1);
      expect(first.events).toHaveLength(1);
      expect(first.events[0]!.kind).toBe("run_admitted");
      let page = first;
      while (page.has_more) page = await next(1);
      expect(page.events.map((e) => e.kind)).toEqual(["cycle_opened"]);
      await pending`COMMIT`;
      const delivered = [];
      for (let n = 0; n < 4; n++) {
        const poll = await next(1);
        delivered.push(...poll.events);
        if (!poll.has_more) break;
      }
      expect(delivered).toMatchObject([
        { key: `run_settled:${r}`, occurred_at: "2025-01-03T12:01:00.000000Z" },
      ]);
      expect(delivered).toHaveLength(1);
      const repeated = await next(100);
      expect(repeated.events).toEqual([]);
      expect(repeated.has_more).toBe(false);
      // The SQL time window includes its exact 120-second boundary.
      const raw = await client().platform.events({
        after: repeated.next_cursor,
      });
      expect(raw.events.some((e) => e.key === `cycle_opened:${c}`)).toBe(true);
      expect(raw.events.some((e) => e.key === `run_admitted:${r}`)).toBe(false);
    } finally {
      await pending`ROLLBACK`;
      pending.release();
    }
  });
  it("keeps an empty window cursor and runs in a read-only transaction", async () => {
    const empty = await client().platform.events({});
    expect(empty.events).toEqual([]);
    expect(empty.has_more).toBe(false);
    expect(
      (await client().platform.events({ after: empty.next_cursor }))
        .next_cursor,
    ).toBe(empty.next_cursor);
    const c = await cycle();
    const filled = await client().platform.events({ after: empty.next_cursor });
    expect(filled.events[0]).toMatchObject({ key: `cycle_opened:${c}` });
    await admin`DELETE FROM control.cycle WHERE id=${c}`;
    const cleared = await client().platform.events({
      after: filled.next_cursor,
    });
    expect(cleared.events).toEqual([]);
    expect(cleared.next_cursor).toBe(filled.next_cursor);
    const settings: string[][] = [];
    const pool: DB = {
      unsafe: database().unsafe,
      begin: (async (_options: string, read: (tx: DB) => Promise<unknown>) =>
        database().begin("read only", (tx) =>
          read({
            unsafe: ((query: string, params: never[]) => {
              if (
                query.startsWith("WITH candidates") ||
                query.includes("SELECT id,database") ||
                query.startsWith("WITH matched")
              ) {
                return tx
                  .unsafe(
                    "SELECT current_setting('transaction_read_only') AS ro,current_setting('statement_timeout') AS statement,current_setting('lock_timeout') AS lock",
                  )
                  .then((found) => {
                    settings.push([
                      found[0]!.ro,
                      found[0]!.statement,
                      found[0]!.lock,
                    ]);
                    return tx.unsafe(query, params);
                  });
              }
              return tx.unsafe(query, params);
            }) as DB["unsafe"],
          }),
        )) as NonNullable<DB["begin"]>,
    };
    const bounded = createRouterClient(router, {
      context: { identity, db: pool },
    });
    await bounded.platform.events({});
    await bounded.platform.holdings({ since });
    await bounded.lineage.chain({ run_id: randomUUID() });
    expect(settings).toHaveLength(5);
    expect(settings.every((row) => row.join() === "on,2s,500ms")).toBe(true);
    expect(
      (
        await admin`SELECT to_regclass('control.platform_event') AS event_table`
      )[0]!.event_table,
    ).toBeNull();
    expect(
      await admin`SELECT tgname FROM pg_trigger WHERE tgname IN ('platform_cycle_event','platform_run_event','platform_alert_event')`,
    ).toHaveLength(0);
  });
  it("reports lock holders as busy after close and treats unknown state as busy", async () => {
    const c = await cycle();
    await owner`UPDATE control.cycle SET status='closed',closed_at=now() WHERE id=${c}`;
    const holder = postgres(url(controlName, "control_rt"), { max: 1 });
    try {
      await holder`SELECT set_config('application_name',${`mdp-runner:42:scheduled:${Math.floor(Date.now() / 1000)}`},false)`;
      await holder`SELECT pg_advisory_lock(42)`;
      expect((await client().platform.events({})).runner).toMatchObject({
        state: "busy",
        busy: true,
        sessions: [{ lock: "42", kind: "scheduled" }],
      });
      await holder`SELECT set_config('application_name','mdp-runner:broken',false)`;
      expect((await client().platform.events({})).runner).toMatchObject({
        state: "unknown",
        busy: true,
      });
      await holder`SELECT pg_advisory_unlock(42)`;
      await holder`SELECT pg_advisory_lock(hashtext('core:hourly:global'))`;
      await holder`SELECT set_config('application_name','',false)`;
      expect((await client().platform.events({})).runner).toMatchObject({
        state: "unknown",
        busy: true,
      });
    } finally {
      await holder.end();
    }
    expect((await client().platform.events({})).runner).toMatchObject({
      state: "idle",
      busy: false,
    });
    await admin`UPDATE control.runner_mode SET runner='cloud'`;
    expect((await client().platform.events({})).runner).toMatchObject({
      state: "unknown",
      busy: true,
    });
    await admin`UPDATE control.runner_mode SET runner='core'`;
  });
  it("pages each lineage family and includes earlier dumps from the cycle manifest", async () => {
    const c = await cycle(),
      r = await run(c);
    for (let n = 0; n < 4; n++) {
      await dump(r, c);
      await owner`INSERT INTO control.call_ledger(run_id,vendor,endpoint,attempt,request_id) VALUES(${r},'fixture','/fixture',1,${String(n)})`;
    }
    const first = await client().lineage.chain({ cycle_id: c, limit: 2 });
    const second = await client().lineage.chain({
      cycle_id: c,
      limit: 2,
      after: first.next,
    });
    for (const kind of ["dumps", "receipts", "requests"] as const) {
      expect(
        new Set([...first[kind], ...second[kind]].map((r) => r.id)).size,
      ).toBe(4);
      expect(second.next[kind]).toBeNull();
    }
    expect(
      (await client().lineage.chain({ cycle_id: c, after: second.next })).dumps,
    ).toEqual([]);
    const later = await cycle("later");
    await owner`INSERT INTO control.cycle_input(cycle_id,dump_id,phase) SELECT ${later},id,'bronze' FROM control.dump WHERE cycle_id=${c}`;
    expect(
      (await client().lineage.chain({ cycle_id: later })).dumps,
    ).toHaveLength(4);
    await expect(client().lineage.chain({ limit: 1 })).rejects.toBeDefined();
  });
  it("checks admin explicitly and serves the page and CLI through HTTP", async () => {
    await expect(
      client(false).platform.holdings({ since }),
    ).rejects.toMatchObject({ status: 403 });
    await expect(client(false).platform.events({})).rejects.toMatchObject({
      status: 403,
    });
    await expect(
      client(false).lineage.chain({ cycle_id: randomUUID() }),
    ).rejects.toMatchObject({ status: 403 });
    await expect(
      client().platform.events({ after: "bad" }),
    ).rejects.toBeDefined();
    const first = await client().platform.events({});
    const result = await promisify(execFile)(
      "pnpm",
      ["mdp", "platform", "events", "--after", first.next_cursor],
      {
        env: {
          PATH: process.env.PATH,
          HOME: process.env.HOME,
          MDP_AUTH_MODE: "dev",
          MDP_CONTROL_API_URL: base,
        },
        timeout: 15000,
      },
    );
    expect(result.stdout).toContain('"next_cursor"');
    const response = await app.request(`${base}/ops/platform?since=${since}`);
    expect(response.status).toBe(200);
    expect(await response.text()).toContain("Read next events");
  }, 20000);
});
