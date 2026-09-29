import { describe, it, expect, beforeAll, afterAll } from "vitest";
import postgres from "postgres";
import { serve, type ServerType } from "@hono/node-server";
import { createRouterClient } from "@orpc/server";
import { createDataClient, type mart_shazam_chart_daily } from "@mdp/data-sdk";
import { router as controlRouter } from "../../control-api/src/router.js";
import { database } from "../../control-api/src/db.js";
import { createApp } from "../src/app.js";
import { readerTypes } from "../src/read.js";

// acceptance against ops/evidence/free-sources/accept_free.py: run after its land-charts and the
// harness's global and tenant builds, with MDP_FREE_SOURCES_ACCEPT=1. The Shazam chart mart is global and served
// to every tenant key; each subject act's wikipedia row reaches its own tenant only.
const enabled = process.env.MDP_FREE_SOURCES_ACCEPT === "1";
function required(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name}`);
  return value;
}
type Chart = mart_shazam_chart_daily;
// The data API orders text grain columns in byte order, then the date, then the position.
function inGrainOrder(a: Chart, b: Chart) {
  const byChart = Buffer.compare(Buffer.from(a.chart), Buffer.from(b.chart));
  if (byChart !== 0) return byChart;
  if (a.chart_date !== b.chart_date) return a.chart_date < b.chart_date ? -1 : 1;
  return a.position - b.position;
}
const BOSTON = "shazam:top-50:united-states:boston";
describe.skipIf(!enabled)("free-source marts through the data API", () => {
  let server: ServerType | undefined;
  let url = "";
  let warehouse: postgres.Sql;
  let keys: postgres.Sql;
  const control = createRouterClient(controlRouter, {
    context: () => ({ identity: { actor: "free-sources-acceptance", tenant_id: null, tenant_slug: null, admin: true }, db: database() }),
  });
  const issued: Record<string, string> = {};
  const client = (key: string) => createDataClient(url, { "x-api-key": key });
  async function charts(key: string, limit: number) {
    const rows: Chart[] = [];
    let cursor: string | undefined;
    do {
      const page = await client(key).mart_shazam_chart_daily({ limit, cursor });
      rows.push(...page.rows);
      cursor = page.next_cursor ?? undefined;
    } while (cursor);
    return rows;
  }
  beforeAll(async () => {
    warehouse = postgres(required("MDP_READER_URL"), { max: 2, types: readerTypes });
    keys = postgres(required("MDP_API_KEY_READER_URL"), { max: 1 });
    await new Promise<void>((done) => {
      server = serve({ fetch: createApp(warehouse, keys).fetch, port: 0, hostname: "127.0.0.1" }, (info) => {
        url = `http://127.0.0.1:${info.port}`;
        done();
      });
    });
    for (const slug of ["acme", "bravo-co"]) {
      issued[slug] = (await control.apiKeys.create({ tenant_slug: slug, label: `${slug} free sources` })).api_key;
    }
  });
  afterAll(async () => {
    server?.close();
    await warehouse.end();
    await keys.end();
    await database().end();
  });

  it("serves the Shazam chart mart to every tenant key in grain order, with its annotations", async () => {
    const acme = await charts(issued.acme ?? "", 2);
    // Byte order puts "top-200" before "top-50".
    expect(acme.map((r) => [r.chart, r.chart_date, r.position, r.apple_song_id])).toEqual([
      ["shazam:top-200:united-states", "2026-09-02", 1, "6000000101"],
      ["shazam:top-200:united-states", "2026-09-03", 1, "6000000101"],
      ["shazam:top-200:united-states", "2026-09-03", 2, "6000000102"],
      [BOSTON, "2026-09-02", 1, "6000000202"],
      [BOSTON, "2026-09-03", 1, "6000000201"],
      [BOSTON, "2026-09-03", 2, "6000000202"],
    ]);
    expect(acme).toEqual([...acme].sort(inGrainOrder));
    expect(acme.every((r) => JSON.parse(r.source_keys).includes("sz_chart") && !r.learning_eligible && !r.resale_permitted)).toBe(true);
    expect(acme.find((r) => r.apple_song_id === "6000000202")).toMatchObject({
      city: "boston", chart_type: "top-50", apple_primary_artist_id: "1000000202", multi_artist_credit: true,
    });
    // Global rows: another tenant's key reads the same ones.
    expect(await charts(issued["bravo-co"] ?? "", 100)).toEqual(acme);
  });

  it("filters the chart mart by equality and a half-open date range", async () => {
    const boston = await client(issued.acme ?? "").mart_shazam_chart_daily({
      limit: 100, filters: { chart: BOSTON }, range: { chart_date: { from: "2026-09-03", to: "2026-09-04" } },
    });
    expect(boston.rows.map((r) => [r.chart_date, r.position, r.apple_song_id])).toEqual([
      ["2026-09-03", 1, "6000000201"],
      ["2026-09-03", 2, "6000000202"],
    ]);
  });
});
