import { execFileSync } from "node:child_process";
import { strict as assert } from "node:assert";
import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";
import { z } from "zod";
import { functionPage } from "../packages/contracts/src/index.js";
import {
  createDataClient,
  mart_chart_history,
} from "../packages/data-sdk/src/index.js";
import { seedVisualBudgets } from "../apps/control-api/scripts/seed-visual-budgets.js";
import { database } from "../apps/control-api/src/db.js";
const base = process.env.MDP_CONTROL_API_URL ?? "http://127.0.0.1:8090";
const transcript: string[] = [];
process.on("exit", code => {
  writeFileSync("../ops/evidence/control/fixes-curl-walk.txt", transcript.join("\n") + `\nOPS_WALK ${code === 0 ? "PASS" : "FAIL"}\n`);
});
function curl(path: string, fields?: Record<string, string>) {
  const args = [
    "--silent",
    "--show-error",
    "--fail-with-body",
    "--location",
    `${base}${path}`,
  ];
  if (fields)
    args.push(
      ...Object.entries(fields).flatMap(([k, v]) => [
        "--data-urlencode",
        `${k}=${v}`,
      ]),
    );
  // Populated fixture pages include receipt diagnostics and can exceed 1 MiB.
  const body = execFileSync("curl", args, { encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
  transcript.push(
    `curl ${fields ? "-X POST " : ""}${base}${path}${fields ? " [form fields: " + Object.keys(fields).join(", ") + "]" : ""}`,
  );
  if (fields) {
    const result = body.match(
      /<summary>Raw result<\/summary><pre>(.*?)<\/pre>/s,
    )?.[1];
    assert(result, "Operation result is rendered");
    transcript.push(
      result
        .replaceAll("&quot;", '"')
        .replaceAll("&amp;", "&")
        .replaceAll("&lt;", "<")
        .replaceAll("&gt;", ">")
        .replace(/("(?:cap_cents|ceiling_cents|cost_cents)"\s*:\s*)"?\d+"?/g, '$1"[synthetic amount omitted]"'),
    );
  } else transcript.push(`HTTP 200; ${body.length} bytes; page rendered`);
  return body;
}
const parseApi = async (path: string) => {
  const response = await fetch(base + path, {
    headers: { "x-mdp-dev-user": "dev-user" },
  });
  assert(response.ok);
  return response.json();
};
const sets = z
  .array(
    z.object({
      id: z.string(),
      kind: z.string(),
      tenant_id: z.string().nullable(),
    }),
  )
  .parse(await parseApi("/api/targets/sets"));
let set = sets.find((s) => s.kind === "account" && s.tenant_id === null);
if (!set) {
  curl("/actions/create-set", { kind: "account", name: "Fixture accounts" });
  set = z
    .array(
      z.object({
        id: z.string(),
        kind: z.string(),
        tenant_id: z.string().nullable(),
      }),
    )
    .parse(await parseApi("/api/targets/sets"))
    .find((s) => s.kind === "account" && s.tenant_id === null);
}
assert(set);
const key = `control-${randomUUID()}`;
const csv = `platform,handle,platform_account_id\nfixture,${key},${key}`;
curl("/ops");
curl("/actions/import", { target_set_id: set.id, csv, dry_run: "true" });
let targets = z
  .array(z.object({ id: z.string(), handle: z.string().nullable() }))
  .parse(await parseApi("/api/targets"));
assert(!targets.some((t) => t.handle === key), "dry run leaves no target");
curl("/actions/import", { target_set_id: set.id, csv, dry_run: "false" });
targets = z
  .array(z.object({ id: z.string(), handle: z.string().nullable() }))
  .parse(await parseApi("/api/targets"));
const target = targets.find((t) => t.handle === key);
assert(target);
curl("/actions/resolve", { id: target.id, platform_account_id: key });
curl("/actions/activate", { id: target.id, active: "true" });
const db = database();
const period = `control-acceptance-${randomUUID()}`;
curl("/actions/create-budget", { scope: "global", scope_id: "", period, cap_cents: "100", soft_pct: "80", hard_action: "warn", ceiling_cents: "200" });
const budgets = z.array(z.object({id:z.string(),period:z.string()})).parse(await parseApi("/api/budgets"));
const budgetId = z.string().parse(budgets.find(b => b.period === period)?.id);
curl("/actions/budget", { id: budgetId, cap_cents: "150" });
transcript.push("BUDGET_FLOW PASS create → raise within ceiling");
const fixture = await fetch(`${process.env.MDP_SERVICE_URL}/v1/_fixture/plan`, {
  method: "POST",
  headers: {
    authorization: `Bearer ${process.env.MDP_SERVICE_TOKEN}`,
    "content-type": "application/json",
  },
  body: JSON.stringify({ source_key: "fixture_accounts", pages: 100 }),
});
assert(fixture.ok);
curl("/functions/fixture_accounts");
const html = curl("/actions/run", {
  source_key: "fixture_accounts",
  key,
  scope: "global",
  back: "/functions/fixture_accounts",
});
const rawResult = (body: string): unknown => {
  const raw = body.match(/<summary>Raw result<\/summary><pre>(.*?)<\/pre>/s)?.[1];
  assert(raw, "Audited result is rendered");
  return JSON.parse(raw.replaceAll("&quot;", '"').replaceAll("&amp;", "&").replaceAll("&lt;", "<").replaceAll("&gt;", ">"));
};
const runId = z.object({result:z.object({run_id:z.uuid()})}).parse(rawResult(html)).result.run_id;
let done = false;
for (let i = 0; i < 100; i++) {
  const result = z
    .object({
      run: z.object({ status: z.string(), rows_written: z.string() }),
      receipts: z.array(z.object({ trace_url: z.string() })),
    })
    .parse(await parseApi(`/api/runs/${runId}`));
  if (result.run.status === "succeeded") {
    assert(BigInt(result.run.rows_written) > 0);
    assert(result.receipts[0]?.trace_url);
    transcript.push(
      JSON.stringify({
        run_id: runId,
        ...result.run,
        receipt_count: result.receipts.length,
        trace: result.receipts[0]?.trace_url,
      }),
    );
    done = true;
    break;
  }
  assert(
    !["failed", "partial", "superseded"].includes(result.run.status),
    JSON.stringify(result),
  );
  await new Promise((r) => setTimeout(r, 300));
}
assert(done, "manual run reaches succeeded");
const page = curl("/functions/fixture_accounts");
assert(
  page.includes("Output preview") &&
    page.includes(key) &&
    page.includes("/traces/"),
);
const trace = page.match(/href="(\/traces\/[^"]+)"/)?.[1];
assert(trace);
curl(trace);
const audit =
  await db`SELECT action FROM control.audit_log WHERE after->>'state'='succeeded' ORDER BY at DESC LIMIT 100`;
for (const expected of [
  "targets.importTargets",
  "targets.resolve",
  "targets.bulkActivate",
  "streamlines.runNow",
  "budgets.create",
  "budgets.raise",
])
  assert(
    audit.some((a) => a.action === expected),
    `audit contains ${expected}`,
  );
// Keep a stable, varied local fixture instead of accumulating global duplicates.
if(new URL(process.env.MDP_CONTROL_RT_URL || "").hostname === "127.0.0.1" && new URL(process.env.MDP_CONTROL_RT_URL || "").port === "5433") await seedVisualBudgets();
curl("/screen/weekly");
const client = createDataClient(
  process.env.MDP_DATA_API_URL ?? "http://127.0.0.1:8091",
  { "x-mdp-dev-user": "dev-user" },
);
const result = await client.mart_chart_history({limit: 2});
assert(result.rows.length > 0);
assert(result.next_cursor);
const next = await client.mart_chart_history({limit: 2, cursor: result.next_cursor});
assert(next.rows.length > 0);
assert(JSON.stringify(next.rows[0]) !== JSON.stringify(result.rows[0]));
for (const row of result.rows) mart_chart_history.parse(row);
transcript.push(`data-api mart_chart_history: ${result.rows.length} typed rows; cursor page 2 distinct`);
// Exercise the real preview API, links and current-page export.
const firstPreview = functionPage.parse(await parseApi("/api/functions/fixture_accounts")).output_preview[0];
assert(firstPreview?.next_cursor, "Seeded preview has a second page");
assert(firstPreview.columns.some(c => c.name === "snapshot_at" && c.type === "timestamp with time zone"));
const previewQuery = new URLSearchParams({preview_table:firstPreview.table,preview_cursor:firstPreview.next_cursor});
const nextPreview = functionPage.parse(await parseApi(`/api/functions/fixture_accounts?${previewQuery}`)).output_preview.find(p => p.table === firstPreview.table);
assert(nextPreview && nextPreview.rows.length > 0);
const firstRows = new Set(firstPreview.rows.map(row => JSON.stringify(row)));
assert(nextPreview.rows.every(row => !firstRows.has(JSON.stringify(row))), "Preview page two is distinct");
const nextHtml = curl(`/functions/fixture_accounts?${previewQuery}`);
assert(nextHtml.includes("Latest rows") && nextHtml.includes("Next page · up to 100 rows"));
const exported = curl(`/functions/fixture_accounts/preview.csv?${new URLSearchParams({table:firstPreview.table,preview_cursor:firstPreview.next_cursor})}`);
assert(exported.includes('"snapshot_at"') && exported.includes(String(nextPreview.rows[0]?.handle)));
const invalidPreview = await fetch(base + "/api/functions/fixture_accounts?" + new URLSearchParams({preview_table:firstPreview.table,preview_cursor:firstPreview.next_cursor+"x"}));
assert.equal(invalidPreview.status,400);
transcript.push(`PREVIEW_CURSOR PASS distinct pages; catalog types; page-two CSV; invalid cursor refused; rows=${firstPreview.rows.length}/${nextPreview.rows.length}`);

// Reach a diagnosed rejection using the same form exposed to the engineer.
const scenarioHtml = curl("/actions/run", {source_key:"fixture_accounts",key:`control-rejection-${randomUUID()}`,scope:"global",fixture_scenario:"not_found",back:"/functions/fixture_accounts"});
const scenarioRun = z.object({result:z.object({run_id:z.uuid()})}).parse(rawResult(scenarioHtml)).result.run_id;
let rejected = false;
for(let i=0;i<100;i++) {
  const state = z.object({run:z.object({status:z.string(),rows_rejected:z.string()}),repairs_pending:z.number()}).parse(await parseApi(`/api/runs/${scenarioRun}`));
  if(["partial","failed","succeeded"].includes(state.run.status) && state.repairs_pending === 0) {
    assert.equal(state.run.status,"partial");
    assert(BigInt(state.run.rows_rejected)>0);
    const diagnosed = functionPage.parse(await parseApi("/api/functions/fixture_accounts"));
    assert(diagnosed.rejected_sample.some(row=>row._run_id === scenarioRun));
    const diagnosedHtml = curl("/functions/fixture_accounts");
    assert(diagnosedHtml.includes("invalid_record") && diagnosedHtml.includes(`/runs/${scenarioRun}`));
    transcript.push(`FIXTURE_REJECTION PASS scenario=not_found status=partial rejected=${state.run.rows_rejected}; producing-run link and reason rendered`);
    rejected=true;break;
  }
  await new Promise(resolve=>setTimeout(resolve,300));
}
assert(rejected,"Page-triggered fixture reaches partial with rejected records");
await db.end();
writeFileSync(
  "../ops/evidence/control/ops-walk.txt",
  transcript.join("\n") + "\n",
);
console.log(transcript.join("\n"));
console.log(
  "OPS_WALK PASS import dry-run → resolve → activate → run → trace + output → budget; typed data rows + cursor; preview pages + CSV; fixture rejection",
);
