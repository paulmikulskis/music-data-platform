import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import postgres from "postgres";
import { z } from "zod";
import { readMart } from "../src/read.js";

const url = process.env.MDP_WAREHOUSE_TEST_URL;
const names = [
  "mart_top_movers",
  "mart_top_movers_current",
  "mart_early_signals_current",
  "mart_arrivals_current",
];
describe.skipIf(!url)("movement member filters", () => {
  const db = postgres(url!, { onnotice: () => {} });
  const row = z.object({ song_key: z.string(), member_song_keys: z.string() });
  beforeAll(async () => {
    vi.stubEnv("MDP_AUTH_MODE", "dev");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    await db`CREATE SCHEMA IF NOT EXISTS marts`;
    for (const name of names) {
      await db.unsafe(
        `CREATE TABLE marts.${name} (song_key text, member_song_keys text)`,
      );
    }
  });
  afterAll(async () => {
    for (const name of names)
      await db.unsafe(`DROP TABLE IF EXISTS marts.${name} CASCADE`);
    await db.end();
    vi.unstubAllEnvs();
  });
  it("finds the current score for both valid strict keys before and after the representative changes", async () => {
    for (const representative of ["a", "b"]) {
      for (const name of names) {
        await db.unsafe(`TRUNCATE marts.${name}`);
        await db.unsafe(`INSERT INTO marts.${name} VALUES ($1, $2)`, [
          representative,
          '["a","b"]',
        ]);
        for (const key of ["a", "b", "absent"]) {
          const page = await readMart(
            {
              request: new Request("http://data.local"),
              warehouse: db,
              keys: db,
            },
            {
              name,
              schema: "marts",
              tenant_scoped: false,
              tenant_readable: true,
              columns: ["song_key", "member_song_keys"],
              grain: ["song_key"],
              grain_types: ["text"],
              time_columns: [],
            },
            row,
            (value) => row.parse(value),
            { limit: 1, filters: { song_key: key } },
          );
          expect(page.rows.map((entry) => entry.song_key)).toEqual(
            key === "absent" ? [] : [representative],
          );
        }
      }
    }
  });
});
