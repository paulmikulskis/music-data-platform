import { expect, it, vi } from "vitest";
import postgres from "postgres";
vi.mock("server-only", () => ({}));
import {
  shazamCitiesSql,
  teamQuestions,
  teamResult,
} from "../lib/team-questions";
import inventory from "../../../../ops/showcase/queries.json";

it("keeps the one starter question fixed, bounded and in the reviewed inventory", () => {
  expect(teamQuestions).toHaveLength(1);
  expect(shazamCitiesSql).toContain("LIMIT 5");
  expect(shazamCitiesSql).not.toMatch(/\$\d/);
  expect(shazamCitiesSql).toContain("marts.mart_shazam_chart_daily");
  expect(shazamCitiesSql).toContain("explore_intermediate.int_song_key__daily");
  const entry = inventory.find(
    (item) => "id" in item && item.id === "team_shazam_cities",
  );
  expect(entry && "sql" in entry ? entry.sql : null).toBe(shazamCitiesSql);
});
it("accepts at most five reviewed rows and no extra fields", () => {
  const row = {
    title: "Night tide",
    artist: "Test recording",
    city: "london",
    country: "united-kingdom",
    first_day: "2026-09-26",
    song_key: null,
  };
  const value = { id: "shazam-cities", queried_at: "2026-09-27T00:00:00Z" };
  expect(teamResult.safeParse({ ...value, rows: [row] }).success).toBe(true);
  expect(
    teamResult.safeParse({ ...value, rows: Array(6).fill(row) }).success,
  ).toBe(false);
  expect(
    teamResult.safeParse({
      ...value,
      rows: [{ ...row, apple_song_id: "creator-sentinel" }],
    }).success,
  ).toBe(false);
});
// The question reads built marts, so it runs where check-adapters.sh builds them.
it.skipIf(!process.env.MDP_SHOWCASE_ADAPTER_TEST_URL)(
  "runs the question as showcase_wh with reviewed columns only",
  async () => {
    const db = postgres(process.env.MDP_SHOWCASE_ADAPTER_TEST_URL!);
    try {
      const rows = await db.unsafe(shazamCitiesSql);
      expect(rows.length).toBeLessThanOrEqual(5);
      for (const row of rows)
        expect(Object.keys(row).sort()).toEqual(
          ["artist_text", "city", "country", "first_day", "song_key", "title_text"],
        );
    } finally {
      await db.end();
    }
  },
);
it.skipIf(!process.env.MDP_SHOWCASE_ADAPTER_TEST_URL)(
  "reads through the budget as showcase_wh and projects each row",
  async () => {
    vi.stubEnv(
      "MDP_SHOWCASE_WH_URL",
      process.env.MDP_SHOWCASE_ADAPTER_TEST_URL!,
    );
    const { budget } = await import("../server/read-budget");
    budget.setRunner("idle");
    try {
      const { teamQuestion } = await import("../server/team-questions");
      const result = await teamQuestion("shazam-cities");
      expect(result.id).toBe("shazam-cities");
      expect(result.rows.length).toBeLessThanOrEqual(5);
      for (const row of result.rows)
        expect(Object.keys(row).sort()).toEqual(
          ["artist", "city", "country", "first_day", "song_key", "title"],
        );
      await expect(teamQuestion("other")).rejects.toThrow("Unknown question");
    } finally {
      const { warehouse } = await import("../server/clients");
      await warehouse().end();
      vi.unstubAllEnvs();
    }
  },
);
it("hands the same SQL to the Workbench through a signed link and never runs it there", async () => {
  vi.stubEnv("MDP_SHOWCASE_SESSION_SECRET", "s".repeat(32));
  vi.doMock("../server/session", () => ({
    session: async () => ({ handle: "fixture", person: { handle: "fixture" } }),
  }));
  vi.doMock("../server/team-questions", () => ({
    teamQuestion: async (id: string) => ({
      id,
      queried_at: "2026-09-27T00:00:00Z",
      rows: [],
    }),
  }));
  vi.resetModules();
  try {
    const { GET } = await import("../app/s/team-question/route");
    const response = await GET(
      new Request("http://viewer.invalid/s/team-question?id=shazam-cities"),
    );
    expect(response.status).toBe(200);
    const body = await response.json();
    const link = new URL(body.workbench, "http://viewer.invalid");
    expect(link.pathname).toBe("/workbench");
    expect(link.searchParams.get("intent")).toBe("query");
    expect(link.searchParams.get("sql")).toBe(shazamCitiesSql);
    expect(link.searchParams.get("showcase_proof")).toMatch(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
    expect(
      (await GET(new Request("http://viewer.invalid/s/team-question?id=other"))).status,
    ).toBe(404);
  } finally {
    vi.doUnmock("../server/session");
    vi.doUnmock("../server/team-questions");
    vi.resetModules();
    vi.unstubAllEnvs();
  }
});
