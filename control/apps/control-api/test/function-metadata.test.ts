import { readFileSync } from "node:fs";
import { afterAll, afterEach, expect, it, vi } from "vitest";
import { createRouterClient } from "@orpc/server";
import { functionPage } from "@mdp/contracts";
import postgres from "postgres";
import { z } from "zod";
import { router } from "../src/router.js";

const recorded = z
  .object({ response: functionPage })
  .parse(
    JSON.parse(
      readFileSync(
        new URL(
          "../../../packages/contracts/test/fixtures/_v1_functions_source_key__get.json",
          import.meta.url,
        ),
        "utf8",
      ),
    ),
  ).response;
const db = postgres("postgresql://fixture:fixture@127.0.0.1:1/fixture");
afterAll(async () => db.end());
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it.each([true, false])(
  "requests metadata upstream before reading the response (admin=%s)",
  async (admin) => {
    let receiptBuilds = 0;
    const fetcher = vi.fn<typeof fetch>(async (url) => {
      const query = new URL(String(url)).searchParams;
      if (query.get("metadata_only") === "true") {
        return Response.json({
          ...recorded,
          receipts: [],
          output_preview: [],
          rejected_sample: [],
        });
      }
      receiptBuilds++;
      return Response.json(recorded);
    });
    vi.stubEnv("MDP_SERVICE_URL", "http://service.fixture");
    vi.stubEnv("MDP_SERVICE_TOKEN", "fixture");
    vi.stubGlobal("fetch", fetcher);
    const client = createRouterClient(router, {
      context: {
        db,
        identity: {
          actor: "fixture",
          admin,
          staff: !admin,
          tenant_id: null,
          tenant_slug: null,
        },
      },
    });
    const metadata = await client.functions.page({
      source_key: "fixture_accounts",
      metadata_only: admin,
    });
    expect(metadata.last_runs.length).toBeGreaterThan(0);
    expect(metadata.receipts).toEqual([]);
    expect(receiptBuilds).toBe(0);
    expect(String(fetcher.mock.calls[0]![0])).toBe(
      "http://service.fixture/v1/functions/fixture_accounts?metadata_only=true",
    );
    if (admin) {
      const full = await client.functions.page({
        source_key: "fixture_accounts",
        preview_table: "raw.account_snapshots",
      });
      expect(full.receipts[0]!.length).toBeGreaterThan(0);
      expect(receiptBuilds).toBe(1);
      expect(String(fetcher.mock.calls[1]![0])).toContain(
        "preview_table=raw.account_snapshots",
      );
    }
  },
);
