import { afterEach, expect, it, vi } from "vitest";
import {
  metadata_mart_song_day,
  metadata_mart_top_movers_current,
} from "@mdp/data-sdk";
import { build, days, movers as fixtures } from "./browser/fixtures";
vi.mock("server-only", () => ({}));
vi.mock("../server/clients", () => ({
  warehouse: () => ({ unsafe: async () => [] }),
}));
import { songHistory, movers, citation } from "../server/reads";
import { budget } from "../server/read-budget";
import { directCitationSql } from "../server/citation-sql";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
function serve(rows: unknown[], relation: string) {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-26T12:00:00Z"));
  vi.stubEnv("MDP_DATA_API_URL", "http://data.invalid");
  vi.stubEnv("MDP_SHOWCASE_READER_KEY", "fixture");
  budget.invalidate("global:");
  budget.setRunner("idle");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        json: { rows, next_cursor: null, build: build(relation), labels: {} },
      }),
    ),
  );
}
it("Home copies all selected mover columns and its list and limit", async () => {
  serve(fixtures, "mart_top_movers_current");
  const result = await movers(3);
  const sql = citation(result.value, "Movement").sql;
  for (const column of metadata_mart_top_movers_current.columns)
    expect(sql).toContain(`"${column}"`);
  expect(sql).toContain("\"movement_list\" = E'new_entries'");
  expect(sql).toContain(
    'ORDER BY "movement_list" COLLATE "C", "rank" LIMIT 3;',
  );
});
it("Song copies selected columns, its escaped song, half-open dates and limit from the read", async () => {
  serve(days, "mart_song_day");
  const result = await songHistory("apple:test'\\song");
  const sql = citation(result.value, "History").sql;
  for (const column of metadata_mart_song_day.columns)
    expect(sql).toContain(`"${column}"`);
  expect(sql).toContain("\"song_key\" = E'apple:test''\\\\song'");
  expect(sql).toContain("\"day\" >= E'2026-08-30'");
  expect(sql).toContain("\"day\" < E'2026-09-27'");
  expect(sql).toContain("LIMIT 100;");
  vi.advanceTimersByTime(86400000);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("offline");
    }),
  );
  expect((await songHistory("apple:test'\\song")).value.sql).toBe(sql);
});
it("renders direct read parameters without replacing text inside a value", () => {
  expect(
    directCitationSql("SELECT song_key WHERE song_key=$1 LIMIT 30", [
      "test'$2",
    ]),
  ).toBe("SELECT song_key WHERE song_key=E'test''$2' LIMIT 30");
});
