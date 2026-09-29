import { describe, it, expect, vi, afterEach } from "vitest";
import { randomUUID } from "node:crypto";
import postgres from "postgres";
import { z } from "zod";
import { readMart } from "../src/read.js";
const enabled = process.env.MDP_CONTROL_INTEGRATION === "1";
afterEach(() => vi.unstubAllEnvs());
describe.skipIf(!enabled)("reader_wh tenant isolation and cursor scope", () => {
  it("injects session tenant and refuses cursor reuse across tenant or filters", async () => {
    const adminUrl = process.env.MDP_WAREHOUSE_TEST_URL;
    const readerUrl = process.env.MDP_READER_URL;
    if (!adminUrl || !readerUrl)
      throw new Error("Set warehouse test owner and reader URLs");
    const owner = postgres(adminUrl);
    const reader = postgres(readerUrl);
    const suffix = randomUUID().replaceAll("-", "");
    const slugA = `controla-${suffix}`;
    const slugB = `controlb_${suffix}`;
    const schemaA = `tenant_${slugA}_marts`;
    const schemaB = `tenant_${slugB}_marts`;
    const row = z.object({ id: z.string(), tenant_id: z.string() });
    const a = randomUUID(),
      b = randomUUID();
    // Development identity carries the tenant; a request can never name one.
    const as = (tenant: string, slug: string) => {
      vi.stubEnv("CLERK_SECRET_KEY", "");
      vi.stubEnv("MDP_AUTH_MODE", "dev");
      vi.stubEnv("MDP_DEV_TENANT_ID", tenant);
      vi.stubEnv("MDP_DEV_TENANT_SLUG", slug);
      return { request: new Request("http://data.local/"), warehouse: reader, keys: reader };
    };
    try {
      for (const schema of [schemaA, schemaB]) {
        await owner.unsafe(`CREATE SCHEMA "${schema}"`);
        await owner.unsafe(
          `CREATE TABLE "${schema}".mart_test(id text,tenant_id text)`,
        );
        await owner.unsafe(`GRANT USAGE ON SCHEMA "${schema}" TO reader_wh`);
        await owner.unsafe(
          `GRANT SELECT ON "${schema}".mart_test TO reader_wh`,
        );
      }
      await owner.unsafe(
        `INSERT INTO "${schemaA}".mart_test VALUES ('1',$1),('2',$1),('foreign',$2)`,
        [a, b],
      );
      await owner.unsafe(
        `INSERT INTO "${schemaB}".mart_test VALUES ('3',$1),('4',$1)`,
        [b],
      );
      const metadata = {
        name: "mart_test",
        schema: "marts",
        tenant_scoped: true,
        tenant_readable: true,
        columns: ["id", "tenant_id"],
        grain: ["id"],
        grain_types: ["text"],
        time_columns: [],
      };
      const first = await readMart(
        as(a, slugA),
        metadata,
        row,
        (v) => row.parse(v),
        { limit: 1 },
      );
      expect(first.rows).toEqual([{ id: "1", tenant_id: a }]);
      expect(first.next_cursor).not.toBeNull();
      const cursor = first.next_cursor ?? "";
      const second = await readMart(
        as(a, slugA),
        metadata,
        row,
        (v) => row.parse(v),
        { limit: 1, cursor },
      );
      expect(second.rows).toEqual([{ id: "2", tenant_id: a }]);
      await expect(
        readMart(as(b, slugB), metadata, row, (v) => row.parse(v), {
          limit: 1,
          cursor,
        }),
      ).rejects.toThrow("another query");
      await expect(
        readMart(as(a, slugA), metadata, row, (v) => row.parse(v), {
          limit: 1,
          filters: { tenant_id: b },
        }),
      ).rejects.toMatchObject({ data: { error_class: "invalid_filter" } });
      await expect(
        readMart(as(a, slugA), metadata, row, (v) => row.parse(v), {
          limit: 1,
          cursor,
          filters: { id: "1" },
        }),
      ).rejects.toThrow("another query");
    } finally {
      for (const schema of [schemaA, schemaB])
        await owner.unsafe(`DROP SCHEMA IF EXISTS "${schema}" CASCADE`);
      await owner.end();
      await reader.end();
    }
  });
});
