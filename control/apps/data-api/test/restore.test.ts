import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash, randomBytes } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import postgres from "postgres";
import { serve, type ServerType } from "@hono/node-server";
import { createDataClient } from "@mdp/data-sdk";
import { database } from "../../control-api/src/db.js";
import { createApp } from "../src/app.js";
import { readerTypes } from "../src/read.js";

// The deploy's mart rebuild (ops/deploy.sh rebuild_marts) over marts the previous release built, on the
// accept-adversarial stack: ops/evidence/serving/2c1/restore-check.sh runs a scheduled weekly and hourly, then
// this suite with MDP_RESTORE_ACCEPT=1. The previous release built mart_chart_history and
// mart_playlist_events without the annotation columns this release's contracts add.
const enabled = process.env.MDP_RESTORE_ACCEPT === "1";
const run = promisify(execFile);
const repo = resolve(fileURLToPath(import.meta.url), "../../../../..");
function required(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name}`);
  return value;
}
describe.skipIf(!enabled)("the deploy's mart rebuild", () => {
  let server: ServerType | undefined;
  let url = "";
  let warehouse: postgres.Sql;
  let keys: postgres.Sql;
  const key = `mdp_${randomBytes(32).toString("base64url")}`;
  beforeAll(async () => {
    warehouse = postgres(required("MDP_READER_URL"), { max: 2, types: readerTypes });
    keys = postgres(required("MDP_API_KEY_READER_URL"), { max: 1 });
    await new Promise<void>((done) => {
      server = serve({ fetch: createApp(warehouse, keys).fetch, port: 0, hostname: "127.0.0.1" }, (info) => {
        url = `http://127.0.0.1:${info.port}`;
        done();
      });
    });
    // An operator key: no tenant, so it reads mart_playlist_events too.
    const hash = createHash("sha256").update(key).digest("hex");
    await database()`INSERT INTO control.api_key(key_hash,label,role) VALUES (${hash},'serving restore check','reader')`;
  });
  afterAll(async () => {
    server?.close();
    await warehouse.end();
    await keys.end();
    await database().end();
  });

  it("answers contract_pending for the previous release's marts, then serves them after the rebuild runs", async () => {
    const client = createDataClient(url, { "x-api-key": key });
    const admin = postgres(required("MDP_WAREHOUSE_ADMIN_URL"), { max: 1 });
    await admin.unsafe("ALTER TABLE marts.mart_chart_history DROP COLUMN resale_permitted, DROP COLUMN source_keys");
    await admin.unsafe(
      "ALTER TABLE marts.mart_playlist_events DROP COLUMN learning_eligible, DROP COLUMN resale_permitted, DROP COLUMN source_keys",
    );
    await admin.end();
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      for (const read of [() => client.mart_chart_history({ limit: 1 }), () => client.mart_playlist_events({ limit: 1 })])
        await expect(read()).rejects.toMatchObject({ code: "DATA", status: 503, data: { error_class: "contract_pending" } });
      const lines = logged.mock.calls.map((call) => String(call[0]));
      expect(lines.filter((line) => line.includes('"event":"contract_pending"'))).toHaveLength(2);
    } finally {
      logged.mockRestore();
    }
    const cycles = async () => (await database()`SELECT count(*)::int AS n FROM control.cycle`)[0]?.n;
    const before = await cycles();
    // The rebuild machine's command: the restore run, ungated, on the newest scheduled cycle.
    for (const cadence of ["weekly", "hourly"])
      await run("bash", [resolve(repo, "ops/run.sh"), cadence, "--target", "pg_local"], {
        cwd: repo,
        env: { ...process.env, MDP_RESTORE: "1", MDP_RUN_REASON_CATEGORY: "other", MDP_RUN_ID: "" },
        maxBuffer: 64 * 1024 * 1024,
      });
    expect(await cycles()).toBe(before);
    // mart_chart_history is built empty on this stack (restore-check.sh); mart_playlist_events holds rows.
    expect(Array.isArray((await client.mart_chart_history({ limit: 1 })).rows)).toBe(true);
    expect((await client.mart_playlist_events({ limit: 1 })).rows).toHaveLength(1);
  }, 1_800_000);
});
