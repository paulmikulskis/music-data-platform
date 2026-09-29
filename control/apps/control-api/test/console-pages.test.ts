import { describe, expect, it } from "vitest";
import { renderIndex, renderEntity } from "../src/explorer-page.js";
import { assembleCatalog } from "../src/explorer-data.js";
import { RecentRuns, RunReceipts } from "../src/run-components.js";
import { z } from "zod";
import { runDto } from "@mdp/contracts";
import { readFileSync } from "node:fs";

it("labels explorer rows and entity pages from the generated catalog", async () => {
  const catalog = assembleCatalog({
    relations: [
      {
        schema: "tenant_tenant_a_marts",
        name: "mart_chart_history",
        type: "table",
        columns: [],
        rows: 1,
        bytes: 10,
      },
    ],
    streamlines: [],
    lastRuns: [],
    models: [],
    sources: [],
    warehouseReachable: true,
    manifestReachable: false,
  });
  expect(String(await renderIndex(catalog))).toContain("tenant: tenant_a");
  expect(String(await renderEntity(catalog.entities[0]!, catalog))).toContain(
    "layer: silver",
  );
});
it("uses the same coverage meter in the recent run and receipt surfaces", async () => {
  const recorded = JSON.parse(
    readFileSync(
      new URL(
        "../../../packages/contracts/test/fixtures/_v1_runs_get.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  const source = Array.isArray(recorded.response)
    ? recorded.response[0]
    : recorded.response.runs?.[0];
  const run = runDto.parse({
    ...source,
    error_class: "forbidden_path",
    error_message: "Token endpoint refused",
  });
  const coverage = {
    [run.id]: {
      succeeded: 8,
      total: 10,
      failures: [{ id: "target", name: "Example", reason: "HTTP 404" }],
    },
  };
  expect(await RecentRuns({ runs: [run], coverage }).toString()).toContain(
    "8 / 10 targets succeeded",
  );
  const receipts = await RunReceipts({
    runs: [run],
    receipts: [[]],
    coverage,
  }).toString();
  expect(receipts).toContain("8 / 10 targets succeeded");
  expect(receipts).toContain("Next step:");
});
const base = process.env.MDP_CONSOLE_TEST_URL;
describe.skipIf(!base)("fixture console pages", () => {
  for (const path of [
    "/ops",
    "/functions",
    "/functions/billboard_hot100",
    "/explorer",
    "/workbench",
    "/status",
    "/screen/weekly",
  ]) {
    it(`renders the failure banner on ${path}`, async () => {
      const response = await fetch(`${base}${path}`);
      const html = await response.text();
      expect(response.status).toBe(200);
      expect(html).toContain("Platform needs attention");
      expect(html).toContain("/actions/retry");
    }, 30000);
  }
  it("renders run and target detail pages with their shared safety primitives", async () => {
    const runs = z
      .array(z.object({ id: z.string() }))
      .parse(await (await fetch(`${base}/api/runs?source_key=billboard_hot100`)).json());
    const targets = z
      .array(z.object({ id: z.string(), handle: z.string().nullable() }))
      .parse(await (await fetch(`${base}/api/targets`)).json());
    const run = await fetch(`${base}/runs/${runs[0]!.id}`),
      target = await fetch(
        `${base}/targets/${targets.find((t) => t.handle === "fixture_account_001")!.id}`,
      );
    expect(run.status).toBe(200);
    expect(target.status).toBe(200);
    const runHtml = await run.text(),
      targetHtml = await target.text();
    expect(runHtml).toContain("targets succeeded");
    expect(runHtml).toContain("rights:");
    expect(runHtml).toContain("Next step:");
    expect(targetHtml).toContain("Platform needs attention");
    expect(targetHtml).toContain("Last HTTP response: 404");
    expect(targetHtml).toContain("/actions/activate");
  }, 30000);
  it("shows mixed health, coverage, drift and refusals from runtime records", async () => {
    const html = await (await fetch(`${base}/functions/billboard_hot100`)).text();
    for (const text of [
      'id="target-strip"',
      'id="recent-runs"',
      'id="output-preview"',
      "data-lazy=",
      "/part/rows",
    ])
      expect(html).toContain(text);
    for (const text of [
      "Delivery health",
      "Run sample",
      "never probed",
      'class="label-chips"',
    ])
      expect(html).not.toContain(text);
  }, 30000);
});
it("labels the tenant warehouse copy from its declared model", async () => {
  const catalog = assembleCatalog({
    relations: [
      {
        schema: "tenant_tenant_a_staging",
        name: "stg_scoped_fixture",
        type: "table",
        columns: [],
        rows: 1,
        bytes: 10,
      },
    ],
    streamlines: [],
    lastRuns: [],
    models: [
      {
        name: "stg_scoped_fixture",
        schema: "tenant_tenant_a_staging",
        folder: "staging/tenant",
        materialized: "table",
        description: null,
        columns: [],
        tags: ["scope:tenant", "bronze"],
        meta: {},
        upstream: [],
        downstream: [],
      },
    ],
    sources: [],
    warehouseReachable: true,
    manifestReachable: true,
  });
  expect(
    String(
      await renderEntity(
        catalog.entities.find((e) => e.group === "warehouse")!,
        catalog,
      ),
    ),
  ).toContain("tenant: tenant_a");
});
it("keeps rejected row share beside independent target coverage in receipts", async () => {
  const { receiptSchema } = await import("@mdp/data-sdk");
  const recorded = JSON.parse(
    readFileSync(
      new URL(
        "../../../packages/contracts/test/fixtures/_v1_runs_get.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  const source = Array.isArray(recorded.response)
    ? recorded.response[0]
    : recorded.response.runs?.[0];
  const run = runDto.parse({
    ...source,
    status: "failed",
    coverage: "partial",
    rows_written: "1",
    rows_rejected: "9",
  });
  const receipt = receiptSchema.parse({
    run_id: run.id,
    status: "failed",
    coverage: "partial",
    allow_partial: true,
    rows_written: "1",
    rows_rejected: "9",
    dump_id: null,
    landed_seq: null,
    trace_url: "/traces/fixture",
    message: "Open rejected rows.",
    loads: [],
    row_coverage: "partial",
    row_acceptance: 0.1,
    row_rejection_share: 0.9,
    min_row_coverage: 0.5,
    row_coverage_met: false,
    targets_succeeded: 10,
    targets_total: 10,
    min_target_coverage: 0.9,
    target_coverage: 1,
    target_coverage_met: true,
  });
  const html = await RunReceipts({
    runs: [run],
    receipts: [[receipt]],
  }).toString();
  expect(html).toContain("90% error rejections");
  expect(html).toContain("Below floor");
  expect(html).toContain("Target coverage: 10/10");
  expect(html).toContain("/runbooks/partial-coverage");
});
