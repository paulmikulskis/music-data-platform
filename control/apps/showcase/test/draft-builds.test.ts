import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";
import postgres from "postgres";
vi.mock("server-only", () => ({}));
import { captureTray } from "../server/draft-reads";

const url = process.env.MDP_CALL_TEST_URL;
const database = `draft_builds_${randomUUID().replaceAll("-", "")}`;
let db: postgres.Sql | undefined;
let admin: postgres.Sql | undefined;
const cycle = randomUUID();
const relations = [
  "marts.mart_arrivals_current",
  "explore_intermediate.int_cluster_entries__daily",
  "explore_intermediate.int_song_key__daily",
  "explore_intermediate.int_playlist__snapshots",
];
beforeAll(async () => {
  if (!url) return;
  const connection = new URL(url);
  expect(["localhost", "127.0.0.1"]).toContain(connection.hostname);
  connection.pathname = "/postgres";
  admin = postgres(connection.toString(), { onnotice: () => {} });
  await admin.unsafe(`CREATE DATABASE ${database}`);
  connection.pathname = `/${database}`;
  db = postgres(connection.toString(), { onnotice: () => {} });
  await db.unsafe(`
    CREATE SCHEMA marts;
    CREATE SCHEMA explore_intermediate;
    CREATE SCHEMA catalog;
    CREATE TABLE marts._build (relation text PRIMARY KEY, cycle_id text, close_no bigint, built_at timestamptz);
    CREATE FUNCTION catalog.snapshot_stamp(wanted text) RETURNS jsonb LANGUAGE sql AS $$
      SELECT to_jsonb(b) FROM marts._build b WHERE relation=wanted
    $$;
    CREATE TABLE marts.mart_arrivals_current (
      song_key text, artist_stage text, age_class text, discovery_entries int,
      market_count int, entered_lists int, list_reach_tier int, movement_list text,
      rank int, window_days int, day date, source_keys text, member_song_keys text, cluster_key text
    );
    CREATE TABLE explore_intermediate.int_song_key__daily (platform text, platform_track_id text, song_key text);
    CREATE TABLE explore_intermediate.int_cluster_entries__daily (
      song_key text, day date, platform text, list_id text, list_kind text,
      event_type text, observed_at timestamptz, snapshot_id text, locator jsonb
    );
    CREATE TABLE explore_intermediate.int_playlist__snapshots (
      platform text, playlist_id text, snapshot_id text, variant text, stream text, cadence text
    );
    INSERT INTO marts.mart_arrivals_current VALUES
      ('fixture','emerging','new',1,1,1,1,'new_entries',1,3,'2026-09-26','["sz_chart"]','["fixture"]','fixture');
    INSERT INTO explore_intermediate.int_song_key__daily VALUES ('apple','fixture','fixture');
    INSERT INTO explore_intermediate.int_cluster_entries__daily
      (song_key,day,platform,list_id,event_type) VALUES ('fixture','2026-09-26','shazam','shazam:discovery:US','chart_entry');
  `);
});
beforeEach(async () => {
  if (!db) return;
  await db`TRUNCATE marts._build`;
  for (const relation of relations) {
    await db`INSERT INTO marts._build VALUES (${relation},${cycle},68,'2026-09-26T03:00:00Z')`;
  }
});
afterAll(async () => {
  await db?.end();
  if (admin) {
    await admin.unsafe(`DROP DATABASE ${database} WITH (FORCE)`);
    await admin.end();
  }
});

it.skipIf(!url)("captures candidates when all four builds match", async () => {
  if (!db) return;
  const tray = await captureTray(db, "2026-09-25");
  expect(tray?.candidates).toHaveLength(1);
  expect(tray?.candidates[0]?.snapshot.builds).toHaveLength(4);
  expect(tray?.build).toMatchObject({ cycle_id: cycle, close_no: "68" });
});

for (const relation of relations) {
  it.skipIf(!url).each(["cycle", "close", "stamp"] as const)(
    `refuses a mismatched %s on ${relation}`,
    async (part) => {
      if (!db) return;
      if (part === "cycle") {
        await db`UPDATE marts._build SET cycle_id=${randomUUID()} WHERE relation=${relation}`;
      } else if (part === "close") {
        await db`UPDATE marts._build SET close_no=69 WHERE relation=${relation}`;
      } else {
        await db`DELETE FROM marts._build WHERE relation=${relation}`;
      }
      expect(await captureTray(db, "2026-09-25")).toBeNull();
    },
  );
}
