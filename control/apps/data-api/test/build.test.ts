import { beforeAll, afterAll, describe, expect, it, vi } from "vitest";
import postgres from "postgres";
import { z } from "zod";
import { serve } from "@hono/node-server";
import { createApp } from "../src/app.js";
import { martBuild, createDataClient } from "@mdp/data-sdk";
import { readMart, type ReadContext } from "../src/read.js";

const url = process.env.MDP_WAREHOUSE_TEST_URL;
describe.skipIf(!url)("mart build provenance and read deadline", () => {
  const sql = postgres(url!, { max: 3, onnotice: () => {} });
  const row = z.object({ id: z.string() });
  const metadata = { name: "mart_build_fixture", schema: "marts", tenant_scoped: false, tenant_readable: true,
    columns: ["id"], grain: ["id"], grain_types: ["text"], time_columns: [] };
  const context = (pause?: () => Promise<void>): ReadContext => ({ request: new Request("http://data.local"), warehouse: sql, keys: sql, ...(pause ? {pause} : {}) });
  const read = (input: {limit:number;cursor?:string;filters?:unknown} = {limit:10}, tenant = false, pause?: () => Promise<void>) =>
    readMart(context(pause), {...metadata,tenant_scoped:tenant},row,value=>row.parse(value),input);
  beforeAll(async () => {
    vi.stubEnv("CLERK_SECRET_KEY", ""); vi.stubEnv("MDP_AUTH_MODE", "dev");
    vi.stubEnv("MDP_DEV_TENANT_ID", "00000000-0000-4000-8000-000000000001"); vi.stubEnv("MDP_DEV_TENANT_SLUG", "build-fixture");
    await sql`CREATE SCHEMA IF NOT EXISTS marts`;
    await sql`CREATE TABLE IF NOT EXISTS marts._build (relation text primary key, cycle_id text not null, close_no bigint, built_at timestamptz not null)`;
    await sql`DROP TABLE IF EXISTS marts.mart_build_fixture CASCADE`;
    await sql`DROP SCHEMA IF EXISTS "tenant_build-fixture_marts" CASCADE`;
    await sql`DELETE FROM marts._build WHERE relation IN ('marts.mart_build_fixture','tenant_build-fixture_marts.mart_build_fixture')`;
    await sql`CREATE TABLE marts.mart_build_fixture (id text)`;
    await sql`CREATE SCHEMA "tenant_build-fixture_marts"`;
    await sql`CREATE TABLE "tenant_build-fixture_marts".mart_build_fixture (id text)`;
    await sql`INSERT INTO marts.mart_build_fixture VALUES ('a'),('b')`;
    await sql`INSERT INTO marts._build VALUES ('marts.mart_build_fixture','cycle-a',9007199254740993,'2026-09-25T12:00:00+02')`;
  });
  afterAll(async () => {
    await sql`DROP TABLE marts.mart_build_fixture CASCADE`;
    await sql`DROP SCHEMA "tenant_build-fixture_marts" CASCADE`;
    await sql`DELETE FROM marts._build WHERE relation IN ('marts.mart_build_fixture','tenant_build-fixture_marts.mart_build_fixture')`;
    await sql.end(); vi.unstubAllEnvs();
  });
  it("keeps the build envelope through a generated route and SDK decoding", async () => {
    await sql`CREATE TABLE marts.mart_chart_history (
      chart_name text, chart_week date, chart_position integer, track_title text, artist_name text,
      song_key text, billboard_match_method text, confidence double precision, matched_by_group boolean,
      weeks_on_chart integer, is_debut boolean, source_key text, learning_eligible boolean,
      _cycle_id text, _built_by text, resale_permitted boolean, source_keys text)`;
    await sql`INSERT INTO marts._build VALUES ('marts.mart_chart_history','sdk-cycle',9007199254740993,'2026-09-25T00:00:00Z')`;
    const server = serve({fetch:createApp(sql,sql).fetch,hostname:"127.0.0.1",port:0});
    try {
      if (!server.listening) await new Promise<void>(resolve=>server.once("listening",resolve));
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Use a TCP test listener");
      const page = await createDataClient(`http://127.0.0.1:${address.port}`).mart_chart_history({limit:1});
      expect(page.rows).toEqual([]);
      expect(page.build).toEqual({relation:"marts.mart_chart_history",scope:"global",tenant_slug:null,stamped:true,cycle_id:"sdk-cycle",close_no:"9007199254740993",built_at:"2026-09-25T00:00:00.000Z"});
    } finally {
      await new Promise<void>((resolve,reject)=>server.close(error=>error ? reject(error) : resolve()));
      await sql`DELETE FROM marts._build WHERE relation='marts.mart_chart_history'`;
      await sql`DROP TABLE marts.mart_chart_history`;
    }
  });
  it("returns the global build on short and empty pages even with a tenant identity", async () => {
    for (const filters of [{}, {id:"absent"}]) {
      const page = await read({limit:10,filters});
      expect(page.next_cursor).toBeNull();
      expect(martBuild.parse(page.build)).toEqual({relation:"marts.mart_build_fixture",scope:"global",tenant_slug:null,
        stamped:true,cycle_id:"cycle-a",close_no:"9007199254740993",built_at:"2026-09-25T10:00:00.000Z"});
    }
  });
  it("returns explicit missing provenance, then the tenant stamp on an empty tenant page", async () => {
    expect((await read({limit:10},true)).build).toEqual({relation:"tenant_build-fixture_marts.mart_build_fixture",scope:"tenant",tenant_slug:"build-fixture",stamped:false,cycle_id:null,close_no:null,built_at:null});
    await sql`INSERT INTO marts._build VALUES ('tenant_build-fixture_marts.mart_build_fixture','tenant-cycle',NULL,'2026-09-25T00:00:00Z')`;
    expect((await read({limit:10},true)).build).toMatchObject({stamped:true,cycle_id:"tenant-cycle",close_no:null});
  });
  it("rejects a continuation when the build changes", async () => {
    const first = await read({limit:1});
    await sql.begin(async tx => {
      await tx`CREATE TABLE marts.mart_build_fixture__dbt_tmp (id text)`;
      await tx`INSERT INTO marts.mart_build_fixture__dbt_tmp VALUES ('c')`;
      await tx`ALTER TABLE marts.mart_build_fixture RENAME TO mart_build_fixture__dbt_backup`;
      await tx`ALTER TABLE marts.mart_build_fixture__dbt_tmp RENAME TO mart_build_fixture`;
      await tx`UPDATE marts._build SET cycle_id='cycle-b' WHERE relation='marts.mart_build_fixture'`;
      await tx`DROP TABLE marts.mart_build_fixture__dbt_backup CASCADE`;
    });
    await expect(read({limit:1,cursor:first.next_cursor!})).rejects.toMatchObject({data:{error_class:"cursor_stale"}});
    expect((await read()).build.cycle_id).toBe("cycle-b");
  });
});
