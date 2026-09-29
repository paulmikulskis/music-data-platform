import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import postgres from "postgres";
import { z } from "zod";
import { readMart, readerTypes, type ReadContext } from "../src/read.js";

// A dbt rebuild's rename swap racing a page read. The page is consistent with the build id
// it returns, or the continuation answers cursor_stale; it is never empty under the old build id.
const enabled = process.env.MDP_SERVING_ACCEPT === "1";
// A tenant marts schema: the warehouse event trigger grants reader_wh each swapped-in table, as it does for
// a dbt rebuild, and reader_wh reads marts and tenant_*_marts only.
const schema = "tenant_servingswap_marts";
const relation = `${schema}.mart_swap`;
const metadata = { name: "mart_swap", schema, tenant_scoped: false, tenant_readable: true, columns: ["id", "build"], grain: ["id"], grain_types: ["text"], time_columns: [] };
const row = z.object({ id: z.string(), build: z.string() });
const parse = (value: unknown) => row.parse(value);
describe.skipIf(!enabled)("page reads against a concurrent rename swap", () => {
  let owner: postgres.Sql;
  let watcher: postgres.Sql;
  let reader: postgres.Sql;
  const context = (extra: Partial<ReadContext> = {}): ReadContext => ({
    request: new Request("http://data.local/"), warehouse: reader, keys: reader, ...extra,
  });
  // The statements dbt's table materialization runs, in one transaction with the build stamp.
  const swapSql = (build: string) => [
    `CREATE TABLE ${schema}.mart_swap__dbt_tmp AS SELECT 'r' || g AS id, '${build}' AS build FROM generate_series(1, 6) g`,
    `ALTER TABLE ${relation} RENAME TO mart_swap__dbt_backup`,
    `ALTER TABLE ${schema}.mart_swap__dbt_tmp RENAME TO mart_swap`,
    `INSERT INTO marts._build(relation,cycle_id,close_no,built_at) VALUES ('${relation}','${build}',1,clock_timestamp())
     ON CONFLICT (relation) DO UPDATE SET cycle_id=excluded.cycle_id, close_no=excluded.close_no, built_at=excluded.built_at`,
  ];
  async function rebuild(build: string, gate?: { locked: () => void; release: Promise<void> }) {
    await owner.begin(async (tx) => {
      for (const statement of swapSql(build)) await tx.unsafe(statement);
      gate?.locked();
      await gate?.release;
    });
    await owner.unsafe(`DROP TABLE ${schema}.mart_swap__dbt_backup`);
  }
  // Resolves once a session waits on a lock with the given query text, or the work finished.
  async function waitingOrDone(fragment: string, work: Promise<unknown>) {
    let done = false;
    void work.then(() => (done = true), () => (done = true));
    for (let i = 0; i < 250 && !done; i++) {
      const found = await watcher`SELECT count(*)::int AS n FROM pg_stat_activity WHERE wait_event_type = 'Lock' AND query ILIKE ${"%" + fragment + "%"}`;
      if (found[0]?.n) return "waiting";
      await new Promise((r) => setTimeout(r, 20));
    }
    return done ? "done" : "timeout";
  }
  beforeAll(async () => {
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("MDP_AUTH_MODE", "dev");
    owner = postgres(z.string().parse(process.env.MDP_WAREHOUSE_TEST_URL), { max: 2, onnotice: () => {} });
    watcher = postgres(z.string().parse(process.env.MDP_WAREHOUSE_TEST_URL), { max: 1 });
    reader = postgres(z.string().parse(process.env.MDP_READER_URL), { max: 3, types: readerTypes });
    await owner.unsafe(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
    await owner.unsafe(`CREATE SCHEMA ${schema}`);
    await owner.unsafe("CREATE SCHEMA IF NOT EXISTS marts");
    await owner.unsafe("CREATE TABLE IF NOT EXISTS marts._build (relation text primary key, cycle_id text not null, close_no bigint, built_at timestamp with time zone not null)");
    await owner.unsafe(`CREATE TABLE ${relation} AS SELECT 'r' || g AS id, 'b1' AS build FROM generate_series(1, 6) g`);
    await owner.unsafe(`INSERT INTO marts._build VALUES ('${relation}','b1',1,clock_timestamp()) ON CONFLICT (relation) DO UPDATE SET cycle_id='b1', built_at=clock_timestamp()`);
    await owner.unsafe(`GRANT USAGE ON SCHEMA ${schema} TO reader_wh`);
    await owner.unsafe(`GRANT SELECT ON ${relation} TO reader_wh`);
  });
  afterAll(async () => {
    await owner.unsafe(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);
    await owner.unsafe(`DELETE FROM marts._build WHERE relation='${relation}'`);
    await Promise.all([owner.end(), watcher.end(), reader.end()]);
    vi.unstubAllEnvs();
  });

  it("holds a swap requested between the build-row read and the page read until the page is read", async () => {
    const first = await readMart(context(), metadata, row, parse, { limit: 2 });
    expect(first.rows.map((r) => r.build)).toEqual(["b1", "b1"]);
    let swap: Promise<void> | undefined;
    let during = "";
    const second = await readMart(
      context({
        pause: async () => {
          swap = rebuild("b2");
          // With the share lock held, the rename waits; without it the swap would commit here.
          during = await waitingOrDone("RENAME TO", swap);
        },
      }),
      metadata, row, parse, { limit: 2, cursor: first.next_cursor ?? "" },
    );
    // The page matches the build id its cursor carries; without the lock it came back empty here.
    expect(second.rows).toEqual([{ id: "r3", build: "b1" }, { id: "r4", build: "b1" }]);
    expect(during).toBe("waiting");
    await swap;
    await expect(readMart(context(), metadata, row, parse, { limit: 2, cursor: second.next_cursor ?? "" }))
      .rejects.toMatchObject({ data: { error_class: "cursor_stale" } });
    const fresh = await readMart(context(), metadata, row, parse, { limit: 10 });
    expect(fresh.rows.map((r) => r.build)).toEqual(Array(6).fill("b2"));
  });

  it("waits for a swap already holding its lock, then reads the new build whole", async () => {
    const opened = await readMart(context(), metadata, row, parse, { limit: 2 });
    let locked!: () => void;
    let release!: () => void;
    const holding = new Promise<void>((r) => (locked = r));
    const swap = rebuild("b3", { locked, release: new Promise<void>((r) => (release = r)) });
    await holding;
    const continuing = readMart(context(), metadata, row, parse, { limit: 2, cursor: opened.next_cursor ?? "" });
    const starting = readMart(context(), metadata, row, parse, { limit: 10 });
    expect(await waitingOrDone("LOCK TABLE", Promise.all([continuing, starting]))).toBe("waiting");
    release();
    await swap;
    await expect(continuing).rejects.toMatchObject({ data: { error_class: "cursor_stale" } });
    expect((await starting).rows.map((r) => r.build)).toEqual(Array(6).fill("b3"));
  });
});
