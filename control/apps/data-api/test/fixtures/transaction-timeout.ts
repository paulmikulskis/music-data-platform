// A separate process with strict rejection handling proves late driver failures are caught.
import assert from "node:assert/strict";
import postgres from "postgres";
import { z } from "zod";
import { readMart, type ReadContext } from "../../src/read.js";
import { installRejectionHandler, requestCorrelation } from "../../src/process-errors.js";

if (process.argv[2] === "backstop") {
  const remove = installRejectionHandler();
  requestCorrelation.run("fixture-correlation", () => { void Promise.reject(new Error("private fixture body")); });
  await new Promise(resolve => setTimeout(resolve, 20));
  remove();
  console.log("PASS process remains alive after a logged rejection");
} else {
  const admin = postgres(process.env.MDP_WAREHOUSE_TEST_URL!, { max: 1, onnotice: () => {} });
  const reader = postgres(process.env.MDP_READER_URL!, { max: 1, connect_timeout: 2, backoff: () => 0 });
  const row = z.object({ id: z.string() });
  const metadata = { name: "mart_transaction_deadline_fixture", schema: "marts", tenant_scoped: false,
    tenant_readable: true, columns: ["id"], grain: ["id"], grain_types: ["text"], time_columns: [] };
  const read = (pause?: ReadContext["pause"]) => readMart({ warehouse: reader, keys: reader,
    request: new Request("http://127.0.0.1"), ...(pause ? { pause } : {}) }, metadata, row, x => row.parse(x), { limit: 1 });
  process.env.MDP_AUTH_MODE = "dev";
  process.env.CLERK_SECRET_KEY = "";
  try {
    assert.equal((await reader`SHOW transaction_timeout`)[0]?.transaction_timeout, "6s");
    assert.equal((await reader`SHOW statement_timeout`)[0]?.statement_timeout, "5s");
    await admin`CREATE SCHEMA IF NOT EXISTS marts`;
    await admin`CREATE TABLE marts.mart_transaction_deadline_fixture (id text)`;
    await admin`INSERT INTO marts.mart_transaction_deadline_fixture VALUES ('ready')`;
    await admin`GRANT SELECT ON marts.mart_transaction_deadline_fixture TO reader_wh`;
    const results = [];
    for (const mode of ["active", "idle", "late"] as const) {
      // The late case delays only this fixture session's server limit to prove that a
      // rejection after the 6.5-second client response stays attached to the read path.
      if (mode === "late") await reader`SET transaction_timeout='7s'`;
      const previous = (await reader`SELECT pg_backend_pid() AS pid`)[0]!.pid;
      const started = performance.now();
      await assert.rejects(read(async tx => {
        const locks = await admin`SELECT count(*)::int AS n FROM pg_locks
          WHERE pid=${previous} AND relation='marts.mart_transaction_deadline_fixture'::regclass AND granted`;
        assert.ok(locks[0]!.n > 0, "the read holds a mart lock before timing out");
        if (mode === "idle") return await new Promise<void>(() => {});
        // Keep production's five-second statement limit. Disable it in this transaction
        // only so pg_sleep reaches the server's six-second transaction limit.
        await tx`SET LOCAL statement_timeout=0`;
        await tx`SELECT pg_sleep(10)`;
      }), (error: unknown) => {
        assert.equal((error as { data?: { error_class?: string } }).data?.error_class, "read_timeout");
        return true;
      });
      const elapsed = Math.round(performance.now() - started);
      assert.ok(elapsed >= (mode === "late" ? 6400 : 5500) && elapsed < 7500, `timeout takes ${elapsed} ms`);
      if (mode === "late") await new Promise(resolve => setTimeout(resolve, 1000));
      assert.equal((await admin`SELECT count(*)::int AS n FROM pg_locks WHERE pid=${previous}`)[0]!.n, 0);
      assert.equal((await admin`SELECT count(*)::int AS n FROM pg_stat_activity WHERE pid=${previous}`)[0]!.n, 0);
      assert.deepEqual((await read()).rows, [{ id: "ready" }]);
      assert.notEqual((await reader`SELECT pg_backend_pid() AS pid`)[0]!.pid, previous);
      assert.equal((await reader`SHOW transaction_timeout`)[0]?.transaction_timeout, "6s");
      results.push({ mode, elapsed_ms: elapsed, error_class: "read_timeout", fresh_connection: true, locks: 0 });
    }
    console.log(JSON.stringify({ status: "passed", results }));
  } finally {
    await reader.end({ timeout: 0 });
    await admin`DROP TABLE IF EXISTS marts.mart_transaction_deadline_fixture CASCADE`;
    await admin.end();
  }
}
