import { expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { functionPage } from "@mdp/contracts";
import { functionPart } from "../src/function-view.js";
import { rows } from "../src/db.js";
import postgres from "postgres";
vi.mock("../src/db.js", async () => ({
  ...(await vi.importActual("../src/db.js")),
  rows: vi.fn(),
}));

const client = vi.hoisted(() => vi.fn());
vi.mock("@orpc/server", async () => ({
  ...(await vi.importActual("@orpc/server")),
  createRouterClient: client,
}));

it.each([false, true])(
  "shows receipt coverage without an output preview (admin=%s)",
  async (admin) => {
    const recorded = JSON.parse(
      readFileSync(
        new URL(
          "../../../packages/contracts/test/fixtures/_v1_functions_source_key__get.json",
          import.meta.url,
        ),
        "utf8",
      ),
    ).response;
    const run = {
      ...recorded.last_runs[0],
      status: "succeeded",
      coverage: "full",
      rows_written: "2",
      rows_rejected: "9",
    };
    const receipt = {
      ...recorded.receipts[0][0],
      status: "succeeded",
      coverage: "full",
      rows_written: "2",
      rows_rejected: "9",
      rows_excluded: "9",
      row_exclusions: { "unsupported_item:musicVideo": 9 },
      row_coverage: "full",
      row_acceptance: 1,
      row_rejection_share: 0,
      min_row_coverage: 0.5,
      row_coverage_met: true,
      targets_succeeded: 1,
      targets_total: 1,
      min_target_coverage: 0.9,
      target_coverage: 1,
      target_coverage_met: true,
    };
    const page = functionPage.parse({
      ...recorded,
      last_runs: [run],
      receipts: [[receipt]],
      output_preview: [],
      fingerprint_history: [],
      rejected_sample: [],
    });
    const stream = {
      ...page.manifest,
      id: run.streamline_id,
      layer: "bronze",
      cadence_tag: "hourly",
      enabled: true,
      batch_size: 10,
      max_concurrency: 1,
      parked_inputs: 0,
      acknowledged_fingerprints: [],
    };
    client.mockReturnValue({
      functions: { page: async () => page },
      streamlines: { get: async () => stream },
      runs: {
        get: async () => ({
          run: page.last_runs[0],
          receipts: page.receipts[0],
        }),
      },
    });
    const context = {
      identity: {
        actor: "fixture",
        admin,
        staff: !admin,
        tenant_id: null,
        tenant_slug: null,
      },
      db: postgres("postgresql://fixture:fixture@127.0.0.1:1/fixture"),
    };
    vi.mocked(rows)
      .mockResolvedValueOnce(page.last_runs)
      .mockResolvedValueOnce([]);
    const html = String(await functionPart(context, "fixture_accounts", "receipts"));
    expect(html).toContain('id="latest-receipts"');
    expect(html).toContain("Row coverage: full");
    expect(html).toContain("0% error rejections");
    expect(html).toContain("Expected exclusions: 9");
    expect(html).toContain("unsupported_item:musicVideo: 9");
    expect(html).toContain("Target coverage: 1/1");
    expect(html).toContain("/runbooks/partial-coverage");
  },
);
