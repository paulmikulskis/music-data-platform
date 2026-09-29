import "server-only";
import { searchRow, searchQuery } from "../lib/search";
import { martCitationSql, directCitationSql } from "./citation-sql";
import { z } from "zod";
import {
  dataContract,
  metadata_mart_playlist_events,
  metadata_mart_shazam_chart_daily,
  metadata_mart_track_daily_streams,
  metadata_mart_top_movers_current,
  metadata_mart_song_day,
  metadata_mart_song_cluster_members,
  metadata_mart_readiness,
  metadata_mart_song_aliases,
  mart_playlist_events,
  mart_shazam_chart_daily,
  mart_track_daily_streams,
  mart_song_aliases,
  mart_song_cluster_members,
} from "@mdp/data-sdk";
import { createORPCClient } from "@orpc/client";
import { RPCLink } from "@orpc/client/fetch";
import { required, type Person } from "@mdp/showcase-auth";
import { rememberProofs, rememberHistory } from "./proof-store";
import { exampleSources } from "../lib/rights";
import { budget } from "./read-budget";
import { warehouse, controlClient } from "./clients";
import {
  mover,
  page,
  songDay,
  identity,
  rightsRow,
  readiness,
  arrivalSummary,
  arrival,
  earlySignal,
  songArtist,
  songPlace,
  playlistItemRow,
  chartEntryRow,
  artistSongRow,
  type PlaylistItemRow,
  type ChartEntryRow,
  type ArtistSongRow,
  type Identity,
  type Rights,
  type Mover,
  type ArrivalSummary,
  type Arrival,
  type EarlySignal,
  type SongArtist,
  type SongPlace,
} from "./models";
import type { Build, Provenance } from "../components/number";
// Named queries validate the consumed fields against generated mart contracts.
// Each key must be a data API procedure: scaffold a missing one with `pnpm --dir control mdp scaffold api <mart>`.
const schemas = {
  mart_playlist_events: page(mart_playlist_events),
  mart_shazam_chart_daily: page(mart_shazam_chart_daily),
  mart_track_daily_streams: page(mart_track_daily_streams),
  mart_top_movers_current: page(mover),
  mart_song_day: page(songDay),
  mart_song_cluster_members: page(mart_song_cluster_members),
  mart_readiness: page(readiness),
  mart_song_aliases: page(
    mart_song_aliases.pick({ alias_key: true, song_key: true }),
  ),
} satisfies Partial<Record<keyof typeof dataContract, z.ZodType>>;
const metadata = {
  mart_playlist_events: metadata_mart_playlist_events,
  mart_shazam_chart_daily: metadata_mart_shazam_chart_daily,
  mart_track_daily_streams: metadata_mart_track_daily_streams,
  mart_top_movers_current: metadata_mart_top_movers_current,
  mart_song_day: metadata_mart_song_day,
  mart_song_cluster_members: metadata_mart_song_cluster_members,
  mart_readiness: metadata_mart_readiness,
  mart_song_aliases: metadata_mart_song_aliases,
};
type Query = keyof typeof schemas;
// The marts the showcase reads through the data API.
export const dataApiReads = Object.keys(schemas);
type DirectResult<T> = {
  rows: T[];
  build: Build;
  queried_at: string;
  sql: string;
};
type MartResult<N extends Query> = z.output<(typeof schemas)[N]> & {
  queried_at: string;
  sql: string;
};
const readers: {
  [N in Query]: { parse(input: unknown): z.output<(typeof schemas)[N]> };
} = schemas;
const moversCache = budget.cache<MartResult<"mart_top_movers_current">>();
const songMovementCache = budget.cache<MartResult<"mart_top_movers_current">>();
const songCache = budget.cache<MartResult<"mart_song_day">>();
const aliasesCache = budget.cache<MartResult<"mart_song_aliases">>();
const groupCache = budget.cache<MartResult<"mart_song_cluster_members">>();
const readinessCache = budget.cache<MartResult<"mart_readiness">>();
const arrivalsCache = budget.cache<DirectResult<ArrivalSummary>>();
const signalCache = budget.cache<DirectResult<Arrival>>();
const earlyCache = budget.cache<DirectResult<EarlySignal>>();
const identitiesCache = budget.cache<DirectResult<Identity>>();
const rightsCache = budget.cache<DirectResult<Rights>>();
const statusCache =
  budget.cache<
    Awaited<ReturnType<ReturnType<typeof controlClient>["status"]>>
  >();
const receiptsCache = budget.cache<DirectResult<{ run_id: string | null }>>();
const recentCache = budget.cache<DirectResult<Mover>>();
const songsCache = budget.cache<DirectResult<{ songs: string }>>();
const artistsCache = budget.cache<DirectResult<SongArtist>>();
const placesCache = budget.cache<DirectResult<SongPlace>>();
export async function mart<N extends Query>(
  name: N,
  input: {
    limit: number;
    filters?: Record<string, unknown>;
    range?: Record<string, unknown>;
    cursor?: string;
  },
) {
  const client = createORPCClient<
    Record<Query, (input: unknown) => Promise<unknown>>
  >(
    new RPCLink({
      url: new URL("/rpc", required("MDP_DATA_API_URL")).toString(),
      headers: { "x-api-key": required("MDP_SHOWCASE_READER_KEY") },
      fetch: (request, init) =>
        fetch(request, {
          ...init,
          cache: "no-store",
          signal: AbortSignal.timeout(6500),
        }),
    }),
  );
  const result = readers[name].parse(await client[name](input));
  return {
    ...result,
    queried_at: new Date().toISOString(),
    sql: martCitationSql(metadata[name], input),
  };
}
export function citation(
  read: { build: Build; queried_at: string; sql?: string },
  query: string,
  sql = read.sql ?? "",
  observed_at?: string | null,
): Provenance {
  return {
    build: read.build,
    queried_at: read.queried_at,
    scope: read.build.scope,
    cadence:
      read.build.relation.startsWith("catalog.") ||
      read.build.relation.startsWith("reference.")
        ? undefined
        : "daily",
    query,
    sql,
    observed_at,
  };
}
export function movers(limit = 3) {
  return moversCache
    .read(`global:movers:${limit}`, "heavy", async () => {
      // Rising reads new songs only; catalog and established artists belong to Places.
      const result = await mart("mart_top_movers_current", {
        limit,
        filters: { movement_list: "new_entries" },
      });
      return { ...result, rows: await playlistCounts(result.rows) };
    })
    .then((result) => {
      rememberProofs(result.value.rows);
      return result;
    });
}
// Strict history keeps the bookmarked key; only movement follows its current group.
export function songMovement(key: string) {
  return songMovementCache
    .read(`global:song-movement:${key}`, "heavy", async () => {
      const members = await mart("mart_song_cluster_members", {
        limit: 1,
        filters: { song_key: key },
      });
      const representative = members.rows[0]?.representative_song_key ?? key;
      const result = await mart("mart_top_movers_current", {
        limit: 1,
        filters: { song_key: representative },
      });
      return { ...result, rows: await playlistCounts(result.rows) };
    })
    .then((result) => {
      rememberProofs(result.value.rows);
      return result;
    });
}
// A song page's 28 UTC days, ending today.
export function historyWindow(now = new Date()) {
  const end = new Date(now);
  end.setUTCHours(0, 0, 0, 0);
  end.setUTCDate(end.getUTCDate() + 1);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 28);
  return {
    from: start.toISOString().slice(0, 10),
    to: end.toISOString().slice(0, 10),
  };
}
export function songHistory(key: string) {
  return songCache
    .read(`global:song:${key}`, "heavy", async () =>
      mart("mart_song_day", {
        limit: 100,
        filters: { song_key: key },
        range: { day: historyWindow() },
      }),
    )
    .then((result) => {
      rememberHistory(key, result.value.build, result.value.rows);
      return result;
    });
}
// One row per family: its history and the first day it counts toward the two-source ranking.
export function movementReadiness() {
  return readinessCache.read("global:readiness", "heavy", () =>
    mart("mart_readiness", { limit: 7 }),
  );
}
// The other song keys in the key's movement group, through its representative. At most 20.
export function songGroup(key: string) {
  return groupCache.read(`global:group:${key}`, "heavy", async () => {
    const own = await mart("mart_song_cluster_members", {
      limit: 1,
      filters: { song_key: key },
    });
    const representative = own.rows[0]?.representative_song_key;
    if (!representative) return own;
    return mart("mart_song_cluster_members", {
      limit: 20,
      filters: { representative_song_key: representative },
    });
  });
}
export function alias(key: string) {
  return aliasesCache.read(`global:alias:${key}`, "heavy", () =>
    mart("mart_song_aliases", { limit: 1, filters: { alias_key: key } }),
  );
}
// Read data and its stamp under the same short transaction and relation lock.
async function direct<T>(
  schema: z.ZodType<T>,
  relation: string,
  sql: string,
  parameters: string[] = [],
  options?: {
    timeout: number;
    signal?: AbortSignal;
    trigramThreshold?: number;
  },
): Promise<DirectResult<T>> {
  const result = await warehouse().begin(
    "isolation level repeatable read read only",
    async (tx) => {
      options?.signal?.throwIfAborted();
      if (options)
        await tx`SELECT set_config('statement_timeout', ${String(options.timeout)}, true)`;
      if (options?.trigramThreshold !== undefined)
        await tx`SELECT set_config('pg_trgm.similarity_threshold', ${String(options.trigramThreshold)}, true)`;
      await tx.unsafe(`LOCK TABLE ${relation} IN ACCESS SHARE MODE`);
      const stamps = await tx<
        {
          cycle_id: string | null;
          close_no: string | null;
          built_at: string | null;
        }[]
      >`SELECT stamp->>'cycle_id' AS cycle_id, stamp->>'close_no' AS close_no, stamp->>'built_at' AS built_at FROM (SELECT catalog.snapshot_stamp(${relation}) AS stamp) s`;
      const stamp = stamps[0];
      options?.signal?.throwIfAborted();
      const query = tx.unsafe(sql, parameters);
      const cancel = () => query.cancel();
      options?.signal?.addEventListener("abort", cancel, { once: true });
      let rows;
      try {
        rows = await query;
        options?.signal?.throwIfAborted();
      } finally {
        options?.signal?.removeEventListener("abort", cancel);
      }
      const build: Build = {
        relation,
        scope: "global",
        tenant_slug: null,
        stamped: !!stamp?.built_at,
        cycle_id: stamp?.cycle_id ?? null,
        close_no: stamp?.close_no ?? null,
        built_at: stamp?.built_at
          ? new Date(stamp.built_at).toISOString()
          : null,
      };
      return {
        rows,
        queried_at: new Date().toISOString(),
        build,
        sql: directCitationSql(sql, parameters),
      };
    },
  );
  return { ...result, rows: z.array(schema).parse(result.rows) };
}
// Sorted, distinct song keys, so one group reads one cache entry.
const keyList = (keys: string | string[]) =>
  [...new Set(typeof keys === "string" ? [keys] : keys)].sort().slice(0, 20);
// The platform copies of one song key, or of every key in its group.
export function identities(keys: string | string[]) {
  const list = keyList(keys);
  return identitiesCache.read(
    `global:identity:${list.join(",")}`,
    "heavy",
    () =>
      direct(
        identity,
        "explore_intermediate.int_song_key__daily",
        "SELECT platform, platform_track_id, resolved, song_key FROM explore_intermediate.int_song_key__daily WHERE song_key IN (SELECT jsonb_array_elements_text($1::text::jsonb)) ORDER BY platform, platform_track_id LIMIT 30",
        [JSON.stringify(list)],
      ),
  );
}
// The rights register as viewers see it: internal example sources stay out of every count.
export const rightsSql = `SELECT count(*)::text AS annotated, count(*) FILTER (WHERE learning_eligible)::text AS learning, count(*) FILTER (WHERE resale_permitted)::text AS resale,
      coalesce(json_agg(json_build_object('source_key', source_key, 'learning', coalesce(learning_eligible, false), 'resale', coalesce(resale_permitted, false)) ORDER BY source_key), '[]'::json) AS sources
      FROM catalog.learning_rights WHERE source_key NOT IN (${[...exampleSources].map((key) => `'${key}'`).join(", ")})`;
export function rights() {
  return rightsCache.read("global:rights", "heavy", () =>
    direct(rightsRow, "catalog.learning_rights", rightsSql),
  );
}
export async function status(person: Person) {
  return statusCache.read(
    `person:${person.api_key_id}:status`,
    "light",
    () => controlClient(person).status({}),
    10000,
  );
}
export function playlistReceipt(
  snapshot: string,
  platform: string,
  playlist: string,
) {
  return receiptsCache.read(
    `proof:playlist:${platform}:${playlist}:${snapshot}`,
    "heavy",
    () =>
      direct(
        z.object({ run_id: z.string().nullable() }),
        "explore_staging.stg_playlist__snapshots",
        "SELECT _run_id::text AS run_id FROM explore_staging.stg_playlist__snapshots WHERE snapshot_id=$1 AND platform=$2 AND playlist_id=$3 LIMIT 1",
        [snapshot, platform, playlist],
      ),
  );
}
export const moverColumns =
  "movement_list, rank, day, song_key, title_text, artist_text, window_days, age_basis, momentum_score, score_parts, coverage, reason_rule, evidence, ranking_build, learning_eligible, resale_permitted, source_keys";
export function recentMovers() {
  return recentCache
    .read("global:movers:28-days", "heavy", async () => {
      const storedMover = mover.extend({
        momentum_score: z.preprocess(
          (value) => (value === null ? null : Number(value)),
          mover.shape.momentum_score,
        ),
      });
      const result = await direct(
        storedMover,
        "marts.mart_top_movers",
        `SELECT ${moverColumns} FROM (
      SELECT DISTINCT ON (song_key) ${moverColumns} FROM marts.mart_top_movers
      WHERE movement_list = 'new_entries'
        AND day >= (now() AT TIME ZONE 'UTC')::date - 27 AND day <= (now() AT TIME ZONE 'UTC')::date
      ORDER BY song_key, day DESC, rank
    ) recent ORDER BY day DESC, rank LIMIT 50`,
      );
      return { ...result, rows: await playlistCounts(result.rows) };
    })
    .then((result) => {
      rememberProofs(result.value.rows);
      return result;
    });
}

// Count the captured locators, rather than joining to a newer ranking after a rebuild.
export const playlistCountsSql = `SELECT row->>'song_key' AS song_key,row->>'ranking_build' AS ranking_build,
 (SELECT count(DISTINCT (e->'row_key'->>'platform',e->'row_key'->>'playlist_id'))::text
 FROM jsonb_array_elements(row->'evidence') e WHERE e->>'component'='playlist_adds'
 AND e->'row_key'->>'platform' IS NOT NULL AND e->'row_key'->>'playlist_id' IS NOT NULL) AS playlist_count
 FROM jsonb_array_elements($1::text::jsonb) row`;
async function playlistCounts(rows: Mover[]) {
  if (!rows.length) return rows;
  const counts = await warehouse().unsafe<
    { song_key: string; ranking_build: string; playlist_count: string }[]
  >(playlistCountsSql, [JSON.stringify(rows)]);
  return rows.map((row) => ({
    ...row,
    playlist_count:
      counts.find(
        (c) =>
          c.song_key === row.song_key && c.ranking_build === row.ranking_build,
      )?.playlist_count ?? null,
  }));
}
export const trackedSongsSql =
  "SELECT count(DISTINCT song_key)::text AS songs FROM explore_intermediate.int_song_key__daily";
export function trackedSongs() {
  return songsCache.read("global:tracked-songs", "heavy", () =>
    direct(
      z.object({ songs: z.string() }),
      "explore_intermediate.int_song_key__daily",
      trackedSongsSql,
    ),
  );
}

// Home counts new songs that entered tracked lists or charts. The lead song entered Shazam
// Discovery in the most countries, then at the best position, then by list rank.
export const arrivalsSql = `WITH songs AS (
  SELECT day, song_key, rank, title_text, artist_text, window_days, movement_list, age_basis, entered_lists, reason_rule, learning_eligible, resale_permitted, evidence, source_keys
  FROM marts.mart_arrivals_current WHERE movement_list = 'new_entries'
), charts AS (
  SELECT s.song_key, split_part(e->'row_key'->>'chart', ':', 3) AS country, min((e->'row_key'->>'position')::integer) AS position
  FROM songs s CROSS JOIN LATERAL jsonb_array_elements(coalesce(s.evidence, '[]')::jsonb) e
  WHERE e->>'relation' = 'mart_shazam_chart_daily' AND split_part(e->'row_key'->>'chart', ':', 2) = 'discovery'
  GROUP BY 1, 2
), discovery AS (
  SELECT song_key, count(*) AS countries, min(position) AS best_position, json_agg(country ORDER BY position, country) AS names
  FROM charts GROUP BY song_key
), lead AS (
  SELECT s.*, d.names, d.countries, d.best_position FROM songs s LEFT JOIN discovery d USING (song_key)
  ORDER BY d.countries DESC NULLS LAST, d.best_position NULLS LAST, s.rank LIMIT 1
)
SELECT (SELECT count(*) FROM songs)::text AS songs, (SELECT max(window_days) FROM songs) AS window_days,
  (SELECT coalesce(json_agg(DISTINCT k.value), '[]'::json) FROM songs CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(songs.source_keys, '[]')::jsonb) k) AS source_keys,
  (SELECT coalesce(json_agg(DISTINCT jsonb_build_object('relation', e->>'relation', 'row_key', jsonb_build_object('platform', e->'row_key'->>'platform'))), '[]'::json)
    FROM songs CROSS JOIN LATERAL jsonb_array_elements(coalesce(songs.evidence, '[]')::jsonb) e) AS observed,
  (SELECT json_build_object('day', day::text, 'song_key', song_key, 'title_text', title_text, 'artist_text', artist_text, 'window_days', window_days,
    'movement_list', movement_list, 'age_basis', age_basis, 'entered_lists', entered_lists::text, 'reason_rule', reason_rule,
    'learning_eligible', learning_eligible, 'resale_permitted', resale_permitted, 'evidence', evidence::json, 'discovery', coalesce(names, '[]'::json)) FROM lead) AS lead`;
export function arrivals() {
  return arrivalsCache.read("global:arrivals", "heavy", () =>
    direct(arrivalSummary, "marts.mart_arrivals_current", arrivalsSql),
  );
}
// Places's two lists rank new chart markets first. Each list keeps its own ranks.
// stage_measured says whether any arrival has an artist size measure yet.
// Use the ranking's first-market rule, including chart playlists and matched copies.
export const signalArrivalsSql = `WITH shown AS (
  SELECT * FROM marts.mart_arrivals_current
  WHERE movement_list IN ('established_entries', 'catalog_entries') AND rank <= 6
), presence AS (
  SELECT c.cluster_key AS song_key, f.day, t.market
  FROM explore_intermediate.int_song_followers__daily f
  JOIN explore_intermediate.int_song_cluster__daily c USING (song_key)
  JOIN reference.playlist_reach_tiers t
    ON CASE WHEN t.platform IN ('apple', 'apple_music') THEN 'apple' ELSE t.platform END = f.platform
    AND t.playlist_id = f.playlist_id
  WHERE c.cluster_key IN (SELECT song_key FROM shown)
    AND CASE WHEN f.owner_class IN ('editorial', 'chart') THEN coalesce(t.list_kind, f.owner_class) ELSE f.owner_class END = 'chart'
    AND length(t.market) = 2
  UNION
  SELECT song_key, chart_date, upper(country)
  FROM explore_intermediate.int_cluster_shazam__daily
  WHERE song_key IN (SELECT song_key FROM shown) AND length(country) = 2
), first_market AS (
  SELECT song_key, market, min(day) AS day FROM presence GROUP BY 1, 2
), new_markets AS (
  SELECT s.song_key, jsonb_agg(DISTINCT e.market ORDER BY e.market) AS markets
  FROM shown s
  JOIN explore_intermediate.int_cluster_entries__daily e ON e.song_key = s.song_key
  JOIN first_market f ON f.song_key = e.song_key AND f.market = e.market AND f.day = e.day
  JOIN explore_intermediate.int_song_windows__daily w ON w.day = s.day
    AND w.family = CASE WHEN e.platform = 'shazam' THEN 'shazam' ELSE 'playlists' END
  WHERE e.list_kind = 'chart' AND length(e.market) = 2
    AND e.day <= s.day AND e.day > s.day - w.window_days
  GROUP BY s.song_key
)
SELECT s.movement_list, s.rank, s.song_key, s.title_text, s.artist_text, s.window_days, s.entered_lists, s.entered_charts,
  s.market_count, s.chart_spread_gain, s.markets, s.age_basis, s.reason_rule, s.evidence, s.learning_eligible, s.resale_permitted, s.source_keys,
  CASE WHEN jsonb_array_length(coalesce(n.markets, '[]'::jsonb)) = s.chart_spread_gain
    THEN coalesce(n.markets, '[]'::jsonb) END AS new_markets,
  EXISTS (SELECT 1 FROM marts.mart_arrivals_current m WHERE coalesce(m.artist_stage_basis, 'none') <> 'none') AS stage_measured
  FROM shown s LEFT JOIN new_markets n USING (song_key)
  ORDER BY s.movement_list, s.rank`;
export function signalArrivals() {
  return signalCache.read("global:signal-arrivals", "heavy", () =>
    direct(arrival, "marts.mart_arrivals_current", signalArrivalsSql),
  );
}
// Rising's one-source songs, six per family. SQL counts the distinct playlists behind each add.
export const earlySignalsSql = `SELECT day::text, evidence, family, movement_list, rank, song_key, title_text, artist_text, window_days, component, value,
  age_basis, reason_rule, learning_eligible, resale_permitted, source_keys,
  (SELECT count(DISTINCT (e->'row_key'->>'platform', e->'row_key'->>'playlist_id'))
    FROM jsonb_array_elements(coalesce(evidence, '[]')::jsonb) e WHERE e->>'component' = 'playlist_adds'
    AND e->'row_key'->>'platform' IS NOT NULL AND e->'row_key'->>'playlist_id' IS NOT NULL)::text AS playlist_count
  FROM marts.mart_early_signals_current WHERE movement_list = 'new_entries' AND rank <= 6 AND song_key IS NOT NULL
  ORDER BY family, rank`;
export function earlySignals() {
  return earlyCache.read("global:early-signals", "heavy", () =>
    direct(earlySignal, "marts.mart_early_signals_current", earlySignalsSql),
  );
}

// A day with something real on a song's page: an add, a tracked list, a chart, plays or a
// Hot 100 week. mart_song_day also holds empty rows back to the day collection began.
const heardDay = `coalesce(d.editorial_adds > 0 OR d.algorithmic_adds > 0 OR d.list_count > 0
  OR d.shazam_charts > 0 OR d.stream_rate > 0 OR d.billboard_position IS NOT NULL, false)`;
// Each song's lead artist, preferring its Spotify copy. The Wikidata item counts only when
// exactly one MusicBrainz artist carries the platform id. First seen is the first day one of the
// artist's songs had something real on its page, one index probe per song on mart_song_day's grain
// index (song_key in C collation, then day).
// Where they show up keeps each copy's own platform sources (and Shazam for an Apple copy): the
// rest of a row's source keys are upstream of the whole table.
export const songArtistsSql = `WITH picked AS (
  SELECT DISTINCT ON (k.song_key) k.song_key, k.platform, k.primary_artist_id, k.primary_artist_key
  FROM explore_intermediate.int_song_key__daily k
  WHERE k.song_key IN (SELECT jsonb_array_elements_text($1::text::jsonb)) AND k.primary_artist_id IS NOT NULL
  ORDER BY k.song_key, (k.platform = 'spotify') DESC, k.platform, k.platform_track_id
), songs AS (
  SELECT s.primary_artist_key, s.song_key, s.platform, s.source_keys FROM explore_intermediate.int_song_key__daily s
  WHERE s.primary_artist_key IN (SELECT primary_artist_key FROM picked)
), seen AS (
  SELECT a.primary_artist_key, min(f.day)::text AS first_seen
  FROM (SELECT DISTINCT primary_artist_key, song_key FROM songs) a
  CROSS JOIN LATERAL (SELECT d.day FROM marts.mart_song_day d WHERE d.song_key = a.song_key COLLATE "C" AND ${heardDay}
    ORDER BY d.day LIMIT 1) f
  GROUP BY 1
), heard AS (
  SELECT s.primary_artist_key, json_agg(DISTINCT x.value) AS source_keys
  FROM songs s CROSS JOIN LATERAL jsonb_array_elements_text(coalesce(s.source_keys, '[]')::jsonb) x
  WHERE split_part(x.value, '_', 1) = CASE s.platform WHEN 'spotify' THEN 'sp' WHEN 'apple' THEN 'am' WHEN 'bandcamp' THEN 'bc' WHEN 'soundcloud' THEN 'sc' END
    OR (s.platform = 'apple' AND x.value = 'sz_chart')
  GROUP BY 1
)
SELECT p.song_key, p.platform, p.primary_artist_id AS artist_id, a.wikidata_qid, a.mb_artist_name, seen.first_seen,
  coalesce(heard.source_keys, '[]'::json) AS source_keys
FROM picked p
LEFT JOIN explore_intermediate.int_artist_identity a ON a.candidate_count = 1 AND a.platform_artist_id = p.primary_artist_id
  AND (CASE WHEN a.platform IN ('apple_music', 'apple') THEN 'apple' ELSE a.platform END) = p.platform
LEFT JOIN seen USING (primary_artist_key) LEFT JOIN heard USING (primary_artist_key)
ORDER BY p.song_key`;
export function songArtists(keys: string[]) {
  const unique = [...new Set(keys)].sort().slice(0, 60);
  return artistsCache.read(`global:artists:${unique.join(",")}`, "heavy", () =>
    direct(
      songArtist,
      "explore_intermediate.int_song_key__daily",
      songArtistsSql,
      [JSON.stringify(unique)],
    ),
  );
}
// The Shazam charts a song (or its matched copies) reached in the last 28 days, in the order it reached them.
export const songPlacesSql = `SELECT s.country, s.city, min(s.chart_date)::text AS first_day
  FROM marts.mart_shazam_chart_daily s
  WHERE s.apple_song_id IN (SELECT k.platform_track_id FROM explore_intermediate.int_song_key__daily k
    WHERE k.song_key IN (SELECT jsonb_array_elements_text($1::text::jsonb)) AND k.platform = 'apple')
    AND s.chart_date >= (now() AT TIME ZONE 'UTC')::date - 27
  GROUP BY 1, 2 ORDER BY min(s.chart_date), 1, 2 LIMIT 60`;
export function songPlaces(keys: string | string[]) {
  const list = keyList(keys);
  return placesCache.read(`global:places:${list.join(",")}`, "heavy", () =>
    direct(songPlace, "marts.mart_shazam_chart_daily", songPlacesSql, [
      JSON.stringify(list),
    ]),
  );
}

// The typed text as a LIKE pattern body: backslash, % and _ match only themselves.
const likeText = String.raw`replace(replace(replace(lower($1), '\', '\\'), '%', '\%'), '_', '\_')`;
// One GIN-indexed query for every content kind. Bound text never becomes SQL.
// A name that contains the text (whole, at its start, at a word start, anywhere) outranks a
// fuzzy match, and a fuzzy-only match must stay close to the best one, so "tokyo" finds Tokyo
// titles and not Tool. Typos still match by trigram ("my body isnt ready" finds fixture_artist).
// Songs with nothing on their page for the last 28 days (no adds, cities, plays or Hot 100 week)
// come after every result that has something to show.
export const librarySearchSql = `WITH scored AS (
  SELECT i.object_key, i.kind, i.display_text, i.context, i.aliases, i.last_seen, i.source_keys,
    i.learning_eligible, i.resale_permitted, similarity(lower(i.display_text), lower($1)) AS sim,
    CASE WHEN lower(i.display_text) = lower($1) THEN 1.0
      WHEN lower(i.display_text) LIKE ${likeText} || '%' THEN 0.6
      WHEN lower(i.display_text) LIKE '% ' || ${likeText} || '%' THEN 0.5
      WHEN lower(i.display_text) LIKE '%' || ${likeText} || '%' THEN 0.3
      ELSE 0 END AS contained
  FROM marts.mart_search_index i
  WHERE lower(i.display_text) % lower($1) OR lower(i.display_text) LIKE '%' || ${likeText} || '%'
  ORDER BY contained DESC, sim DESC, i.object_key
  LIMIT 60
), ranked AS (
  SELECT s.*, max(s.sim) OVER () AS best, bool_or(s.contained > 0) OVER () AS contains FROM scored s
)
SELECT object_key, kind, display_text, context::json AS context,
  aliases::json AS aliases, last_seen::text, source_keys::json AS source_keys,
  learning_eligible, resale_permitted
  FROM ranked r
  WHERE r.contained > 0 OR r.sim >= greatest(0.3, 0.6 * r.best, CASE WHEN r.contains THEN 0.45 ELSE 0 END)
  ORDER BY r.kind = 'song' AND NOT EXISTS (
      SELECT 1 FROM marts.mart_song_day d
      WHERE d.song_key = (r.context::json->>'key') COLLATE "C"
        AND d.day >= (now() AT TIME ZONE 'UTC')::date - 27
        AND (d.editorial_adds > 0 OR d.algorithmic_adds > 0 OR d.shazam_cities > 0
          OR d.stream_rate > 0 OR d.billboard_position IS NOT NULL)),
    r.sim + r.contained DESC, r.last_seen DESC, r.object_key
  LIMIT 20`;
// A Library playlist: its newest profile and the five newest songs that entered it.
// The owner's name is served only for platform-owned lists (editorial and chart).
export const playlistItemSql = `SELECT p.title, p.platform, p.owner_class,
  CASE WHEN p.owner_class IN ('editorial', 'chart') THEN p.owner_name END AS owner_name,
  p.followers::text AS followers, p.track_count_reported::text AS tracks, p.observed_at::text AS observed_at,
  coalesce((SELECT json_agg(json_build_object('song_key', n.song_key, 'title', n.title, 'day', n.day) ORDER BY n.observed_at DESC, n.position)
    FROM (SELECT e.observed_at, e.position, e.observed_at::date::text AS day, k.song_key, coalesce(k.title_text, 'Untitled song') AS title
      FROM marts.mart_playlist_events e
      JOIN LATERAL (SELECT k.song_key, k.title_text FROM explore_intermediate.int_song_key__daily k
        WHERE k.platform = CASE WHEN e.platform IN ('apple_music', 'apple') THEN 'apple' ELSE e.platform END
          AND k.platform_track_id = e.platform_track_id AND k.song_key IS NOT NULL
        ORDER BY k.song_key LIMIT 1) k ON true
      WHERE e.platform = $1 AND e.playlist_id = $2 AND e.event_type IN ('add', 'entered_head')
        AND NOT coalesce(e.is_baseline, false)
      ORDER BY e.observed_at DESC, e.position LIMIT 5) n), '[]'::json) AS songs
  FROM (SELECT * FROM marts.mart_playlist_profile WHERE platform = $1 AND playlist_id = $2
    ORDER BY observed_at DESC, stream, variant LIMIT 1) p`;
// A Library Shazam chart: its latest day's top five, with the chart's size that day.
export const shazamChartItemSql = `WITH top AS (
  SELECT c.chart_type, c.country, c.city, c.chart_date, c.position, c.title_text, c.artist_text, c.apple_song_id,
    count(*) OVER () AS entries
  FROM marts.mart_shazam_chart_daily c
  WHERE c.chart = $1 AND c.chart_date = (SELECT max(chart_date) FROM marts.mart_shazam_chart_daily WHERE chart = $1)
  ORDER BY c.position LIMIT 5)
SELECT t.chart_type, t.country, t.city, t.chart_date::text AS chart_date, t.entries::text AS entries, t.position,
  t.title_text AS title, t.artist_text AS artist, k.song_key
FROM top t LEFT JOIN LATERAL (SELECT k.song_key FROM explore_intermediate.int_song_key__daily k
  WHERE k.platform = 'apple' AND k.platform_track_id = t.apple_song_id AND k.song_key IS NOT NULL
  ORDER BY k.song_key LIMIT 1) k ON true
ORDER BY t.position`;
// A Library Billboard chart: its latest week's top five.
export const billboardItemSql = `SELECT 'hot-100' AS chart_type, NULL::text AS country, NULL::text AS city,
  h.chart_week::text AS chart_date, count(*) OVER ()::text AS entries, h.chart_position AS position,
  h.track_title AS title, h.artist_name AS artist, NULL::text AS song_key
FROM marts.mart_chart_history h
WHERE h.chart_name = $1 AND h.chart_week = (SELECT max(chart_week) FROM marts.mart_chart_history WHERE chart_name = $1)
ORDER BY h.chart_position LIMIT 5`;
// A Library artist: the songs they lead, one per matched song (the cluster the Library lists),
// songs with something on their page in the last 28 days first, then the latest and widest.
// First seen is the first day any copy of their songs had something real on its page.
// mart_song_day's grain index takes song_key in C collation.
export const artistItemSql = `WITH own AS (
  SELECT k.song_key, coalesce(c.cluster_key, k.song_key) AS group_key, k.title_text
  FROM explore_intermediate.int_song_key__daily k
  LEFT JOIN explore_intermediate.int_song_cluster__daily c ON c.song_key = k.song_key
  WHERE k.platform = $1 AND k.primary_artist_id = $2 AND k.song_key IS NOT NULL
), groups AS (
  SELECT group_key, coalesce(max(title_text) FILTER (WHERE song_key = group_key), min(title_text)) AS title
  FROM own GROUP BY group_key
), members AS (
  SELECT group_key, song_key FROM own
  UNION SELECT c.cluster_key, c.song_key FROM explore_intermediate.int_song_cluster__daily c
    WHERE c.cluster_key IN (SELECT group_key FROM groups)
), seen AS (
  SELECT g.group_key, coalesce(g.title, 'Untitled song') AS title, f.first_day, f.last_day, f.charts, f.plays,
    coalesce(f.shown, false) AS shown
  FROM groups g CROSS JOIN LATERAL (
    SELECT min(d.day) FILTER (WHERE h.heard) AS first_day, max(d.day) FILTER (WHERE h.heard) AS last_day,
      bool_or(h.heard AND d.day >= (now() AT TIME ZONE 'UTC')::date - 27) AS shown,
      max(d.shazam_charts) FILTER (WHERE d.day >= (now() AT TIME ZONE 'UTC')::date - 27) AS charts,
      max(d.stream_rate) FILTER (WHERE d.day >= (now() AT TIME ZONE 'UTC')::date - 27) AS plays
    FROM members m JOIN marts.mart_song_day d ON d.song_key = m.song_key COLLATE "C"
    CROSS JOIN LATERAL (SELECT ${heardDay} AS heard) h
    WHERE m.group_key = g.group_key) f
)
SELECT group_key AS song_key, title, first_day::text AS first_day, count(*) OVER ()::text AS songs,
  (min(first_day) OVER ())::text AS first_seen
FROM seen
ORDER BY shown DESC, last_day DESC NULLS LAST, charts DESC NULLS LAST, plays DESC NULLS LAST, title, group_key
LIMIT 5`;
const playlistCache = budget.cache<DirectResult<PlaylistItemRow>>();
const chartCache = budget.cache<DirectResult<ChartEntryRow>>();
const artistCache = budget.cache<DirectResult<ArtistSongRow>>();
export function playlistItem(platform: string, playlist: string) {
  return playlistCache.read(
    `global:library:playlist:${platform}:${playlist}`,
    "light",
    () =>
      direct(
        playlistItemRow,
        "marts.mart_playlist_profile",
        playlistItemSql,
        [platform, playlist],
        { timeout: 2000 },
      ),
  );
}
export function chartItem(chart: string) {
  const billboard = chart.startsWith("billboard:");
  return chartCache.read(`global:library:chart:${chart}`, "light", () =>
    billboard
      ? direct(
          chartEntryRow,
          "marts.mart_chart_history",
          billboardItemSql,
          [chart.slice("billboard:".length)],
          { timeout: 2000 },
        )
      : direct(
          chartEntryRow,
          "marts.mart_shazam_chart_daily",
          shazamChartItemSql,
          [chart],
          { timeout: 2000 },
        ),
  );
}
export function artistItem(platform: string, artist: string) {
  return artistCache.read(
    `global:library:artist:${platform}:${artist}`,
    "light",
    () =>
      direct(
        artistSongRow,
        "explore_intermediate.int_song_key__daily",
        artistItemSql,
        [platform, artist],
        { timeout: 2000 },
      ),
  );
}
// Proof summaries name the exact lists, charts and counters behind one fact.
// The newest title of each playlist a fact's evidence points at.
export const playlistTitlesSql = `SELECT DISTINCT ON (p.platform, p.playlist_id) p.platform, p.playlist_id, p.title
  FROM marts.mart_playlist_profile p
  JOIN json_to_recordset($1::text::json) AS x(platform text, playlist_id text)
    ON x.platform = p.platform AND x.playlist_id = p.playlist_id
  ORDER BY p.platform, p.playlist_id, p.observed_at DESC
  LIMIT 20`;
// Distinct lists across every matched copy, with the movement list classification.
const playlistEntriesSql = `SELECT DISTINCT e.observed_at::date AS day, e.platform, e.playlist_id
  FROM marts.mart_playlist_events e
  JOIN explore_intermediate.int_song_key__daily k ON k.song_key IN (SELECT jsonb_array_elements_text($1::text::jsonb))
    AND k.platform_track_id = e.platform_track_id
    AND k.platform = CASE WHEN e.platform IN ('apple_music', 'apple') THEN 'apple' ELSE e.platform END
  LEFT JOIN reference.playlist_reach_tiers t ON t.playlist_id = e.playlist_id
    AND CASE WHEN t.platform IN ('apple_music', 'apple') THEN 'apple' ELSE t.platform END = k.platform
  WHERE e.event_type IN ('add', 'entered_head') AND NOT coalesce(e.is_baseline, false)
    AND CASE WHEN e.owner_class IN ('editorial', 'chart') THEN coalesce(t.list_kind, e.owner_class) ELSE e.owner_class END
      IN ('editorial', 'new_music', 'dsp_algorithmic')`;
export const playlistDaysSql = `SELECT day::text, count(*)::integer AS playlists FROM (${playlistEntriesSql}) entries
  WHERE day >= $2::date AND day < $3::date GROUP BY day ORDER BY day`;
const playlistDaysCache =
  budget.cache<DirectResult<{ day: string; playlists: number }>>();
export function playlistDays(
  songs: string[],
  window: { from: string; to: string },
) {
  return playlistDaysCache.read(
    `global:playlist-days:${JSON.stringify(songs)}:${window.from}:${window.to}`,
    "heavy",
    () =>
      direct(
        z.object({ day: z.string(), playlists: z.number() }),
        "marts.mart_playlist_events",
        playlistDaysSql,
        [JSON.stringify(songs), window.from, window.to],
      ),
  );
}
export const dayListsSql = `SELECT n.platform, n.playlist_id, coalesce(p.title, 'Playlist') AS title
  FROM (${playlistEntriesSql}) n
  LEFT JOIN LATERAL (SELECT pp.title FROM marts.mart_playlist_profile pp
    WHERE pp.platform = n.platform AND pp.playlist_id = n.playlist_id ORDER BY pp.observed_at DESC LIMIT 1) p ON true
  WHERE n.day = $2::date ORDER BY title, n.platform, n.playlist_id LIMIT 100`;
// Cities: the city Shazam charts the song was on that day.
export const dayChartsSql = `SELECT chart, country, city, position FROM (
    SELECT DISTINCT ON (s.chart) s.chart, s.country, s.city, s.position
    FROM marts.mart_shazam_chart_daily s
    WHERE s.apple_song_id IN (SELECT k.platform_track_id FROM explore_intermediate.int_song_key__daily k
        WHERE k.song_key IN (SELECT jsonb_array_elements_text($1::text::jsonb)) AND k.platform = 'apple')
      AND s.chart_date = $2::date AND s.city IS NOT NULL AND s.city <> ''
    ORDER BY s.chart, s.position) c
  ORDER BY position, chart LIMIT 60`;
// Streams: each copy's play counter read that day, with the copy's own id to tell copies apart.
export const dayPlaysSql = `SELECT t.platform, t.platform_track_id, t.play_count::text AS play_count
  FROM marts.mart_track_daily_streams t
  JOIN explore_intermediate.int_song_key__daily k ON k.song_key IN (SELECT jsonb_array_elements_text($1::text::jsonb))
    AND k.platform_track_id = t.platform_track_id
    AND k.platform = CASE WHEN t.platform IN ('apple_music', 'apple') THEN 'apple' ELSE t.platform END
  WHERE t.day = $2::date AND t.play_count IS NOT NULL
  ORDER BY t.play_count DESC, t.platform LIMIT 5`;
const titleRow = z.object({
  platform: z.string(),
  playlist_id: z.string(),
  title: z.string().nullable(),
});
const chartRow = z.object({
  chart: z.string(),
  country: z.string().nullable(),
  city: z.string().nullable(),
  position: z.number().int(),
});
const playsRow = z.object({
  platform: z.string(),
  platform_track_id: z.string(),
  play_count: z.string(),
});
const titlesCache = budget.cache<DirectResult<z.infer<typeof titleRow>>>();
const dayChartsCache = budget.cache<DirectResult<z.infer<typeof chartRow>>>();
const dayPlaysCache = budget.cache<DirectResult<z.infer<typeof playsRow>>>();
export function playlistTitles(
  lists: { platform: string; playlist_id: string }[],
) {
  const unique = [
    ...new Map(
      lists.map((list) => [`${list.platform}:${list.playlist_id}`, list]),
    ).values(),
  ]
    .sort((a, b) =>
      `${a.platform}:${a.playlist_id}`.localeCompare(
        `${b.platform}:${b.playlist_id}`,
      ),
    )
    .slice(0, 20);
  return titlesCache.read(
    `global:proof-titles:${JSON.stringify(unique)}`,
    "light",
    () =>
      direct(
        titleRow,
        "marts.mart_playlist_profile",
        playlistTitlesSql,
        [JSON.stringify(unique)],
        { timeout: 2000 },
      ),
  );
}
// A lane's day reads take the song key, or every key of its matched song, as the page adds them.
export function dayLists(songs: string | string[], day: string) {
  const list = keyList(songs);
  return titlesCache.read(
    `global:proof-lists:${list.join(",")}:${day}`,
    "light",
    () =>
      direct(
        titleRow,
        "marts.mart_playlist_events",
        dayListsSql,
        [JSON.stringify(list), day],
        { timeout: 2000 },
      ),
  );
}
export function dayCharts(songs: string | string[], day: string) {
  const list = keyList(songs);
  return dayChartsCache.read(
    `global:proof-charts:${list.join(",")}:${day}`,
    "light",
    () =>
      direct(
        chartRow,
        "marts.mart_shazam_chart_daily",
        dayChartsSql,
        [JSON.stringify(list), day],
        { timeout: 2000 },
      ),
  );
}
export function dayPlays(songs: string | string[], day: string) {
  const list = keyList(songs);
  return dayPlaysCache.read(
    `global:proof-plays:${list.join(",")}:${day}`,
    "light",
    () =>
      direct(
        playsRow,
        "marts.mart_track_daily_streams",
        dayPlaysSql,
        [JSON.stringify(list), day],
        { timeout: 2000 },
      ),
  );
}
export function librarySearch(query: string, signal?: AbortSignal) {
  const q = searchQuery.parse(query);
  return budget.run("light", () =>
    direct(searchRow, "marts.mart_search_index", librarySearchSql, [q], {
      timeout: 1000,
      trigramThreshold: 0.3,
      signal,
    }),
  );
}
