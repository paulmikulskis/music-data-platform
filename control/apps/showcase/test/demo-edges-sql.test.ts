import { expect, it, vi } from "vitest";
import postgres from "postgres";
vi.mock("server-only", () => ({}));
import {
  playlistDaysSql,
  dayListsSql,
  signalArrivalsSql,
} from "../server/reads";
const url = process.env.MDP_DEMO_EDGES_TEST_URL;
it.skipIf(!url)(
  "counts distinct daily lists across copies and separates the three new markets",
  async () => {
    expect(new URL(url!).hostname).toBe("127.0.0.1");
    const db = postgres(url!);
    try {
      await db.begin(async (tx) => {
        await tx.unsafe(`
        CREATE TEMP TABLE edge_keys (song_key text, platform text, platform_track_id text);
        INSERT INTO edge_keys VALUES ('song','apple','a'),('copy','spotify','b');
        CREATE TEMP TABLE edge_events (platform text, playlist_id text, platform_track_id text, observed_at timestamptz, event_type text, owner_class text, is_baseline boolean);
        INSERT INTO edge_events VALUES
          ('apple_music','new','a','2026-09-25','add','editorial',false),
          ('spotify','editor','b','2026-09-26','add','editorial',false),
          ('spotify','editor','b','2026-09-26','entered_head','editorial',false),
          ('spotify','top','b','2026-09-27','add','editorial',false),
          ('spotify','baseline','b','2026-09-27','add','editorial',true);
        CREATE TEMP TABLE edge_tiers (platform text, playlist_id text, list_kind text, market text);
        INSERT INTO edge_tiers VALUES ('apple','new','new_music','US'),('spotify','top','chart','AU');
        CREATE TEMP TABLE edge_profiles (platform text, playlist_id text, title text, observed_at timestamptz);
        INSERT INTO edge_profiles VALUES ('apple_music','new','New music','2026-09-27'),('spotify','editor','Editors','2026-09-27');
        CREATE TEMP TABLE edge_groups (song_key text, cluster_key text);
        INSERT INTO edge_groups VALUES ('song','song'),('copy','song');
        CREATE TEMP TABLE edge_followers (song_key text, day date, platform text, playlist_id text, owner_class text);
        INSERT INTO edge_followers VALUES ('copy','2026-09-20','spotify','top','editorial');
        CREATE TEMP TABLE edge_shazam (song_key text, chart_date date, country text);
        INSERT INTO edge_shazam VALUES ('song','2026-09-25','CA'),('song','2026-09-25','GB'),('song','2026-09-25','US'),('song','2026-09-26','AU');
        CREATE TEMP TABLE edge_entries (song_key text, day date, market text, platform text, list_kind text);
        INSERT INTO edge_entries VALUES ('song','2026-09-25','CA','shazam','chart'),('song','2026-09-25','GB','shazam','chart'),('song','2026-09-25','US','shazam','chart'),('song','2026-09-26','AU','shazam','chart'),('song','2026-09-25','US','shazam','chart');
        CREATE TEMP TABLE edge_windows (day date, family text, window_days integer);
        INSERT INTO edge_windows VALUES ('2026-09-27','shazam',4),('2026-09-27','playlists',2);
        CREATE TEMP TABLE edge_arrivals (movement_list text, rank bigint, song_key text, title_text text, artist_text text, window_days integer, entered_lists bigint, entered_charts bigint, market_count bigint, chart_spread_gain bigint, markets text, age_basis text, reason_rule text, evidence text, learning_eligible boolean, resale_permitted boolean, source_keys text, artist_stage_basis text, day date);
        INSERT INTO edge_arrivals VALUES ('catalog_entries',1,'song','Song','Artist',4,0,4,4,3,'["AU","CA","GB","US"]','none','', '[]',false,false,'[]','none','2026-09-27');
      `);
        const names: Record<string, string> = {
          "marts.mart_playlist_events": "edge_events",
          "marts.mart_playlist_profile": "edge_profiles",
          "explore_intermediate.int_song_key__daily": "edge_keys",
          "reference.playlist_reach_tiers": "edge_tiers",
          "explore_intermediate.int_song_cluster__daily": "edge_groups",
          "explore_intermediate.int_song_followers__daily": "edge_followers",
          "explore_intermediate.int_cluster_shazam__daily": "edge_shazam",
          "explore_intermediate.int_cluster_entries__daily": "edge_entries",
          "explore_intermediate.int_song_windows__daily": "edge_windows",
          "marts.mart_arrivals_current": "edge_arrivals",
        };
        const local = (sql: string) =>
          Object.entries(names).reduce(
            (text, [from, to]) => text.replaceAll(from, to),
            sql,
          );
        expect(
          await tx.unsafe(local(playlistDaysSql), [
            '["song","copy"]',
            "2026-09-24",
            "2026-09-28",
          ]),
        ).toEqual([
          { day: "2026-09-25", playlists: 1 },
          { day: "2026-09-26", playlists: 1 },
        ]);
        expect(
          await tx.unsafe(local(dayListsSql), [
            '["song","copy"]',
            "2026-09-27",
          ]),
        ).toEqual([]);
        const [markets] = await tx.unsafe(local(signalArrivalsSql));
        expect(markets.new_markets).toEqual(["CA", "GB", "US"]);
        expect(markets.new_markets.length).toBe(
          Number(markets.chart_spread_gain),
        );
        // A shorter Shazam window drops old arrivals, even though the card's overall window stays four days.
        await tx`UPDATE edge_windows SET window_days=1 WHERE family='shazam'`;
        expect(
          (await tx.unsafe(local(signalArrivalsSql)))[0].new_markets,
        ).toBeNull();
      });
    } finally {
      await db.end();
    }
  },
);
