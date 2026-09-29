// Disposable HTTP and Postgres fixtures. Run with MDP_VIEWER_FIXTURE_DB on loopback.
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { serve } from "@hono/node-server";
import { Hono } from "hono";
import postgres from "postgres";
import { z } from "zod";
import { functionPage, poll, platformSources, runDto } from "@mdp/contracts";
import { rows } from "../../src/db.js";

const dbUrl =
  process.env.MDP_VIEWER_FIXTURE_DB ??
  "postgresql://postgres:postgres@127.0.0.1:55407/control";
if (new URL(dbUrl).hostname !== "127.0.0.1")
  throw Error("Use a disposable loopback database.");
const db = postgres(dbUrl, { onnotice: () => {} });
const template = z
  .object({ response: functionPage })
  .parse(
    JSON.parse(
      readFileSync(
        new URL(
          "../../../../packages/contracts/test/fixtures/_v1_functions_source_key__get.json",
          import.meta.url,
        ),
        "utf8",
      ),
    ),
  ).response;
const key = "fixture_playlist";
let streams =
  await db`SELECT id FROM control.streamline WHERE source_key=${key}`;
if (!streams.length) {
  streams =
    await db`INSERT INTO control.streamline(source_key,layer,cadence_tag,writes,batch_size) VALUES (${key},'bronze','daily',ARRAY['raw.fixture_playlists','raw.fixture_items'],5) RETURNING id`;
  await db`INSERT INTO control.rights_source(source_key,provider,category) VALUES (${key},'Fixture playlists','public')`;
  const wh = await db`SELECT id FROM control.warehouse WHERE is_production`;
  const set =
    await db`INSERT INTO control.target_set(kind,name) VALUES ('playlist','playlist set') RETURNING id`;
  const members: string[] = [];
  for (let i = 0; i < 139; i++) {
    const target =
      await db`INSERT INTO control.target(target_set_id,platform,platform_account_id,display_name,resolution_status,activated_at) VALUES (${set[0]!.id},${i < 56 ? "spotify" : "apple"},${`fixture-${i}`},${`Fixture list ${String(i + 1).padStart(3, "0")}`},'resolved',now()) RETURNING id`;
    if (i < 36) members.push(String(target[0]!.id));
  }
  for (let i = 19; i >= 0; i--) {
    const at = new Date(Date.now() - (i * 12 + 3) * 3600000).toISOString();
    const cycle =
      await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,opened_at,closed_at) VALUES ('daily','global',${`core:daily:global:${randomUUID()}`},'closed',${at},${at}) RETURNING id`;
    const count = i === 0 ? 4812 : 2000 + ((i * 271) % 4000);
    const run =
      await db`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status,coverage,rows_written,cycle_id,created_at,resolved_config) VALUES ('invoke',${`viewer:${randomUUID()}`},'global',${streams[0]!.id},${wh[0]!.id},${i === 2 ? "failed" : "succeeded"},${i === 3 ? "partial" : "full"},${i === 2 ? 0 : count},${cycle[0]!.id},${at},'{}') RETURNING id`;
    if (i === 0) {
      await db`INSERT INTO control.batch(run_id,index,target_ids,status,cursor_checkpoint) VALUES (${run[0]!.id},0,${members},'succeeded',${db.json({ completed_targets: members })})`;
      for (const [index, target] of members.entries())
        await db`INSERT INTO control.call_ledger(run_id,target_id,vendor,endpoint,attempt,http_status,created_at) VALUES (${run[0]!.id},${target},'fixture','/fixture',1,${index === 0 ? 304 : 200},${at})`;
    }
    const dump =
      await db`INSERT INTO control.dump(kind,run_id,streamline_id,cycle_id,uri_prefix,row_count,created_at) VALUES ('output',${run[0]!.id},${streams[0]!.id},${cycle[0]!.id},'file:///fixture',${count},${at}) RETURNING id`;
    await db`INSERT INTO control.load(dump_id,warehouse_id,target_table,status,rows_inserted,loaded_at) VALUES (${dump[0]!.id},${wh[0]!.id},'raw.fixture_playlists','loaded',${count},${at})`;
  }
  const failing =
    await db`INSERT INTO control.streamline(source_key,layer,cadence_tag) VALUES ('fixture_duration','bronze','daily') RETURNING id`;
  for (let i = 0; i < 2; i++) {
    const cycle =
      await db`INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status) VALUES ('daily','global',${`core:daily:global:${randomUUID()}`},'open') RETURNING id`;
    await db`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status,cycle_id,error_class,error_message) VALUES ('invoke',${`viewer:${randomUUID()}`},'global',${failing[0]!.id},${wh[0]!.id},'failed',${cycle[0]!.id},'service_unreachable','Traceback plpy.Error: service_unreachable: Fixture service unavailable CONTEXT: compiled code at line 1')`;
  }
  // Preserve production source cardinality and label lengths, using fixture-only slugs and labels.
  const path = process.env.MDP_VIEWER_SHAPE;
  if (path) {
    const shape = platformSources.parse(JSON.parse(readFileSync(path, "utf8")));
    for (const [i, source] of shape.sources.entries()) {
      const slug = `fixture_source_${String(i + 1).padStart(2, "0")}`;
      await db`INSERT INTO control.streamline(source_key,layer,cadence_tag,enabled) VALUES (${slug},'bronze',${source.cadence},${source.enabled})`;
      await db`INSERT INTO control.rights_source(source_key,provider,category) VALUES (${slug},${`Fixture source ${i + 1} ${"reader ".repeat(Math.max(0, Math.ceil(source.display_name.length / 8) - 3)).trim()}`},'public')`;
    }
  }
}
const service = new Hono();
let receiptBuilds = 0;
async function buildReceipts(run: z.infer<typeof runDto>) {
  receiptBuilds++;
  const loads =
    await db`SELECT d.id AS dump_id,d.landed_seq,l.status,l.generation,l.rows_inserted
    FROM control.dump d JOIN control.load l ON l.dump_id=d.id WHERE d.run_id=${run.id}`;
  return [
    {
      ...template.receipts[0]?.[0],
      run_id: run.id,
      status: run.status,
      coverage: run.coverage,
      rows_written: run.rows_written,
      rows_rejected: run.rows_rejected,
      dump_id: loads[0] ? String(loads[0].dump_id) : null,
      landed_seq: loads[0]?.landed_seq ? String(loads[0].landed_seq) : null,
      trace_url: `/traces/${run.id}`,
      message: `Fixture receipt: ${run.rows_written} rows. Open /runs/${run.id}.`,
      loads: loads.map((load) => ({
        dump_id: String(load.dump_id),
        status: String(load.status),
        generation: Number(load.generation),
        rows_inserted: String(load.rows_inserted),
      })),
      targets_total: 36,
      targets_succeeded: 36,
    },
  ];
}
service.get("/__fixture/receipt-builds", (c) =>
  c.json({ count: receiptBuilds }),
);
async function page(source: string, metadata: boolean) {
  const streams =
    await db`SELECT * FROM control.streamline WHERE source_key=${source}`;
  const runs = await rows(
    db,
    runDto,
    "SELECT * FROM control.run WHERE streamline_id=$1 ORDER BY created_at DESC LIMIT 20",
    [String(streams[0]!.id)],
  );
  const targets =
    await db`SELECT unnest(target_ids) AS id FROM control.batch WHERE run_id=${runs[0]?.id ?? null}`;
  const columns = [
    { name: "title", type: "text", nullable: false },
    { name: "platform", type: "text", nullable: false },
    { name: "followers", type: "bigint", nullable: false },
    { name: "track_count", type: "integer", nullable: false },
    { name: "observed_at", type: "timestamp", nullable: false },
    ...Array.from({ length: 24 }, (_, i) => ({
      name: `detail_${i}`,
      type: "text",
      nullable: true,
    })),
    { name: "_run_id", type: "uuid", nullable: false },
    { name: "_target_id", type: "uuid", nullable: false },
  ];
  const previews =
    source === key && !metadata
      ? ["raw.fixture_playlists", "raw.fixture_items"].map((table) => ({
          table,
          columns,
          next_cursor: null,
          rows: Array.from({ length: 100 }, (_, i) => ({
            title: `Fixture playlist ${i + 1}`,
            platform: "spotify",
            followers: 34100000 - i * 10000,
            track_count: 50,
            observed_at: runs[0]?.created_at,
            _run_id: runs[0]?.id,
            _target_id: String(targets[i % targets.length]?.id),
            ...Object.fromEntries(
              Array.from({ length: 24 }, (_, j) => [
                `detail_${j}`,
                `Fixture value ${j}`,
              ]),
            ),
          })),
        }))
      : [];
  return functionPage.parse({
    ...template,
    manifest: {
      source_key: source,
      targets: true,
      targets_kind: "playlist",
      reads: [],
      writes: ["raw.fixture_playlists", "raw.fixture_items"],
    },
    last_runs: runs,
    receipts: metadata ? [] : await Promise.all(runs.map(buildReceipts)),
    output_preview: previews,
    fingerprint_history: [],
    rejected_sample: [],
    log_url: `/functions/${source}/logs`,
  });
}
service.get("/v1/functions/:key", async (c) =>
  c.json(
    await page(c.req.param("key"), c.req.query("metadata_only") === "true"),
  ),
);
service.get("/v1/runs/:id", async (c) => {
  const run = (
    await rows(db, runDto, "SELECT * FROM control.run WHERE id=$1", [
      c.req.param("id"),
    ])
  )[0]!;
  return c.json(
    poll.parse({
      run,
      receipts: await buildReceipts(run),
      repairs_pending: 0,
    }),
  );
});
service.get("/v1/health/detail", (c) =>
  c.json({ status: "ok", components: {} }),
);
service.post("/v1/workbench/sandboxStatus", (c) => c.json({ sandboxes: [] }));
service.post("/v1/workbench/queries", (c) => c.json({ queries: [] }));
serve({ fetch: service.fetch, hostname: "127.0.0.1", port: 18408 });
process.env.MDP_CONTROL_RT_URL = dbUrl.replace(
  "postgres:postgres",
  "control_rt:control_rt",
);
process.env.MDP_AUTH_MODE = "dev";
process.env.MDP_WORKBENCH_URL = "http://127.0.0.1:18408";
process.env.MDP_SERVICE_URL = "http://127.0.0.1:18408";
process.env.MDP_SERVICE_TOKEN = "fixture";
const { app } = await import("../../src/app.js");
serve({ fetch: app.fetch, hostname: "0.0.0.0", port: 18409 });
console.log(
  "Fixture console: http://127.0.0.1:18409/functions/fixture_playlist",
);
