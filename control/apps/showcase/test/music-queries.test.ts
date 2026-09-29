import { expect, it, vi } from "vitest";
import postgres from "postgres";
vi.mock("server-only", () => ({}));
import { playlistCountsSql } from "../server/reads";
import { mover } from "../server/models";
import { movers } from "./browser/fixtures";
it.skipIf(!process.env.MDP_SHOWCASE_QUERY_TEST_URL)(
  "counts distinct captured playlist IDs, not weighted scores",
  async () => {
    const db = postgres(process.env.MDP_SHOWCASE_QUERY_TEST_URL!);
    try {
      const song = mover.parse(movers[0]);
      song.evidence.push(song.evidence[0]);
      const result = await db.unsafe(playlistCountsSql, [
        JSON.stringify([song]),
      ]);
      expect(result[0].playlist_count).toBe("1");
    } finally {
      await db.end();
    }
  },
);
it.skipIf(!process.env.MDP_SHOWCASE_ADAPTER_TEST_URL)(
  "runs every direct room adapter through showcase_wh and its generated projections",
  async () => {
    vi.stubEnv(
      "MDP_SHOWCASE_WH_URL",
      process.env.MDP_SHOWCASE_ADAPTER_TEST_URL!,
    );
    const { budget } = await import("../server/read-budget");
    const reads = await import("../server/reads");
    budget.setRunner("idle");
    try {
      const results = await Promise.all([
        reads.identities("fixture"),
        reads.trackedSongs(),
        reads.rights(),
        reads.playlistReceipt("fixture", "spotify", "fixture"),
        reads.recentMovers(),
        reads.arrivals(),
        reads.signalArrivals(),
        reads.earlySignals(),
        reads.songArtists(["fixture"]),
        reads.songPlaces("fixture"),
        reads.playlistDays(["fixture"], {
          from: "2026-09-01",
          to: "2026-09-28",
        }),
        reads.dayLists(["fixture"], "2026-09-27"),
      ]);
      for (const result of results) {
        expect(result.value.queried_at).toMatch(/^20/);
        expect(Array.isArray(result.value.rows)).toBe(true);
      }
      const db = (await import("../server/clients")).warehouse();
      const [role] =
        await db`SELECT current_user AS name,current_setting('default_transaction_read_only') AS readonly`;
      expect(role).toEqual({ name: "showcase_wh", readonly: "on" });
      await expect(db`SELECT id FROM control.api_key`).rejects.toThrow();
    } finally {
      const { warehouse } = await import("../server/clients");
      await warehouse().end();
      vi.unstubAllEnvs();
    }
  },
);
it.skipIf(!process.env.MDP_SHOWCASE_ADAPTER_TEST_URL)(
  "runs the bounded calls adapters with showcase_wh",
  async () => {
    const db = postgres(process.env.MDP_SHOWCASE_ADAPTER_TEST_URL!);
    try {
      const { anchorsSql, observeCalls } = await import("../server/call-reads");
      expect(
        await db.unsafe(anchorsSql, [JSON.stringify(["fixture"])]),
      ).toEqual([]);
      const result = await observeCalls(db, []);
      expect(result.rows).toEqual([]);
      expect(result.builds).toHaveLength(4);
    } finally {
      await db.end();
    }
  },
);

it.skipIf(!process.env.MDP_SHOWCASE_ADAPTER_TEST_URL)(
  "runs the Friday tray through showcase_wh",
  async () => {
    const db = postgres(process.env.MDP_SHOWCASE_ADAPTER_TEST_URL!);
    try {
      const { traySql, readDraftNames } = await import("../server/draft-reads");
      expect(await db.unsafe(traySql, ["2026-09-25"])).toEqual([]);
      expect(await readDraftNames(db, ["missing-song"])).toEqual([
        { song_key: "missing-song", title_text: null, artist_text: null },
      ]);
    } finally {
      await db.end();
    }
  },
);
