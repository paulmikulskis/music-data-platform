import { martBuild } from "@mdp/data-sdk";
import { expect, it, vi } from "vitest";
import postgres from "postgres";
vi.mock("server-only", () => ({}));
import {
  librarySearchSql,
  librarySearch,
  artistItemSql,
  songArtistsSql,
  playlistItem,
  chartItem,
  artistItem,
  playlistTitles,
  dayLists,
  dayCharts,
  dayPlays,
} from "../server/reads";
import { searchHref, searchQuery, searchResponse } from "../lib/search";
import { chartLabel, libraryItemPath } from "../lib/library";
import { searchProjection } from "../server/call-offers";
import { build } from "./browser/fixtures";
import inventory from "../../../../ops/showcase/queries.json";

it("refuses short queries and keeps search text out of SQL", () => {
  expect(searchQuery.safeParse(" a ").success).toBe(false);
  expect(searchQuery.safeParse("a".repeat(101)).success).toBe(false);
  expect(searchQuery.parse(" ab ")).toBe("ab");
  expect(librarySearchSql).toContain("lower(i.display_text) % lower($1)");
  // Typed % and _ match only themselves in the containment pattern.
  expect(librarySearchSql).toContain(
    String.raw`replace(replace(replace(lower($1), '\', '\\'), '%', '\%'), '_', '\_')`,
  );
  expect(librarySearchSql).toContain("LIMIT 20");
});
it("keeps the reviewed query inventory's Library search text in step with the code", () => {
  const query = inventory.find(
    (entry) => "id" in entry && entry.id === "library_search",
  );
  expect(query && "sql" in query ? query.sql : null).toBe(librarySearchSql);
});
it("lists an artist's matched songs once and dates them from real signals", () => {
  // A cluster's copies are one song, keyed like the Library's song result.
  expect(artistItemSql).toContain("coalesce(c.cluster_key, k.song_key) AS group_key");
  expect(artistItemSql).toContain("count(*) OVER ()::text AS songs");
  expect(artistItemSql.indexOf("count(*) OVER ()")).toBeLessThan(
    artistItemSql.lastIndexOf("LIMIT 5"),
  );
  // Empty rows back to the day collection began never date an artist.
  for (const sql of [artistItemSql, songArtistsSql])
    expect(sql).toContain("d.list_count > 0");
});
it("links only songs to pages and never to the operator console's Explorer", () => {
  const base = {
    aliases: [],
    last_seen: "2026-09-25 00:00:00",
    source_keys: [],
    learning_eligible: false,
    resale_permitted: false,
  };
  for (const [kind, context] of [
    [
      "playlist",
      { key: "spotify:list", platform: "spotify", playlist_id: "list" },
    ],
    [
      "chart",
      { key: "shazam:top-50:japan:tokyo", country: "JP", city: "tokyo" },
    ],
    ["artist", { key: "artist", platform: "spotify", artist_id: "artist" }],
    ["source", { key: "sz_chart" }],
  ] as const) {
    const row = {
      ...base,
      object_key: `${kind}:x`,
      kind,
      display_text: "x",
      context,
    };
    expect(searchHref(row)).toBeNull();
    expect(libraryItemPath(row)).toMatch(/^\/library\/item\?kind=/);
  }
  expect(
    chartLabel({
      ...base,
      object_key: "chart:x",
      kind: "chart",
      display_text: "tokyo Shazam top-50",
      context: {
        key: "shazam:top-50:japan:tokyo",
        country: "JP",
        city: "tokyo",
      },
    }),
  ).toBe("Tokyo · Shazam top 50");
});
it("freezes only the facts shown on a search result", () => {
  const row = {
    object_key: "song:apple:123",
    kind: "song" as const,
    display_text: "Test song",
    context: { key: "apple:123", subtitle: "Test artist" },
    aliases: ["spotify:copy"],
    last_seen: "2026-09-25 00:00:00",
    source_keys: ["sz_chart"],
    learning_eligible: false,
    resale_permitted: false,
  };
  expect(searchHref(row)).toBe("/s/song/apple%3A123");
  const projection = searchProjection(
    row,
    martBuild.parse(build("mart_search_index")),
  );
  expect(projection).toMatchObject({
    card: "search",
    places_shown: null,
    display_text: row.display_text,
    subtitle: "Test artist",
  });
  expect(projection).not.toHaveProperty("shazam_cities");
  expect(
    searchResponse.safeParse({
      rows: [row],
      offers: {},
      sources_unavailable: false,
      calls_unavailable: false,
    }).success,
  ).toBe(true);
});
it.skipIf(!process.env.MDP_SHOWCASE_ADAPTER_TEST_URL)(
  "executes indexed Library reads as showcase_wh with a one-second timeout",
  async () => {
    vi.stubEnv(
      "MDP_SHOWCASE_WH_URL",
      process.env.MDP_SHOWCASE_ADAPTER_TEST_URL!,
    );
    const db = postgres(process.env.MDP_SHOWCASE_ADAPTER_TEST_URL!, { max: 1 });
    try {
      const read = await librarySearch("fixture");
      expect(Array.isArray(read.rows)).toBe(true);
      expect(read.build.relation).toBe("marts.mart_search_index");
      const plans = await db.begin(async (tx) => {
        await tx`SET LOCAL enable_seqscan = off`;
        return tx.unsafe(`EXPLAIN ${librarySearchSql}`, ["fixture"]);
      });
      expect(JSON.stringify(plans)).toContain("Bitmap Index Scan");
      const abort = new AbortController();
      abort.abort();
      await expect(librarySearch("fixture", abort.signal)).rejects.toThrow();
      // Library sheets and proof summaries read their declared columns as showcase_wh.
      const day = new Date().toISOString().slice(0, 10);
      const reads = [
        await playlistItem("spotify", "fixture"),
        await chartItem("shazam:top-50:japan:tokyo"),
        await chartItem("billboard:hot-100"),
        await artistItem("spotify", "fixture"),
        await playlistTitles([{ platform: "spotify", playlist_id: "fixture" }]),
        await dayLists("fixture", day),
        await dayCharts("fixture", day),
        await dayPlays("fixture", day),
      ];
      for (const read of reads)
        expect(Array.isArray(read.value.rows)).toBe(true);
    } finally {
      await db.end();
      const { warehouse } = await import("../server/clients");
      await warehouse().end();
      vi.unstubAllEnvs();
    }
  },
);
