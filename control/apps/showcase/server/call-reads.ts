import "server-only";
import { createHash } from "node:crypto";
import { z } from "zod";
import type { Sql, TransactionSql } from "postgres";
import { martBuild } from "@mdp/data-sdk";
import { callAnchor, callObservation, type SavedCall } from "../lib/calls";
import { warehouse, controlStore } from "./clients";
import { budget } from "./read-budget";
import { readCalls } from "./call-store";

const identityRelation = "explore_intermediate.int_song_key__daily";
const placesRelation = "marts.mart_shazam_chart_daily";
const clusterRelation = "explore_intermediate.int_song_cluster__daily";
export const anchorsSql = `SELECT requested.song_key AS requested_key, k.platform, k.platform_track_id, k.song_key, k.source_keys
  FROM jsonb_array_elements_text($1::text::jsonb) requested(song_key)
  CROSS JOIN LATERAL (SELECT platform,platform_track_id,song_key,source_keys FROM explore_intermediate.int_song_key__daily
    WHERE song_key=requested.song_key ORDER BY platform,platform_track_id LIMIT 21) k`;
const anchorRow = callAnchor.extend({
  requested_key: z.string(),
  source_keys: z.preprocess(
    (v) => (typeof v === "string" ? JSON.parse(v) : v),
    z.array(z.string()),
  ),
});
// Apple ids give a call its Shazam places. Three paths reach them, in one batch:
// the frozen Apple anchors, the Apple copies of each anchor's current song key, and the
// Apple copies of the other song keys in that key's provisional group (at most 20 keys and
// 20 of their Apple ids a call).
// A group-only id is a matched copy; "match pending" means no path reached an Apple id.
export const currentAnchorsSql = `WITH anchors AS (
 SELECT c->>'id' AS call_id,a->>'platform' AS platform,a->>'platform_track_id' AS platform_track_id,a->>'song_key' AS frozen_key
 FROM jsonb_array_elements($1::text::jsonb) c CROSS JOIN LATERAL jsonb_array_elements(c->'facts'->'anchors') a
), matched AS (
 SELECT a.*,k.song_key AS current_key FROM anchors a LEFT JOIN explore_intermediate.int_song_key__daily k USING(platform,platform_track_id)
), strict_apple AS (
 SELECT call_id,platform_track_id AS apple_id FROM anchors WHERE platform='apple'
 UNION SELECT m.call_id,k.platform_track_id FROM matched m JOIN explore_intermediate.int_song_key__daily k ON k.song_key=m.current_key AND k.platform='apple'
), members AS (
 SELECT DISTINCT m.call_id,member.song_key FROM matched m
 JOIN explore_intermediate.int_song_cluster__daily own ON own.song_key=m.current_key
 JOIN explore_intermediate.int_song_cluster__daily member ON member.cluster_key=own.cluster_key AND member.song_key<>own.song_key
), grouped AS (
 SELECT call_id,song_key FROM (SELECT *,row_number() OVER (PARTITION BY call_id ORDER BY song_key) AS n FROM members) ranked WHERE n<=20
), grouped_apple AS (
 SELECT call_id,apple_id FROM (SELECT *,row_number() OVER (PARTITION BY call_id ORDER BY apple_id) AS n FROM (
  SELECT g.call_id,k.platform_track_id AS apple_id FROM grouped g JOIN explore_intermediate.int_song_key__daily k ON k.song_key=g.song_key AND k.platform='apple'
  EXCEPT SELECT call_id,apple_id FROM strict_apple) copies) ranked WHERE n<=20
), apple AS (
 SELECT call_id,apple_id FROM strict_apple UNION SELECT call_id,apple_id FROM grouped_apple
)
SELECT c->>'id' AS call_id,
 (SELECT title_text FROM marts.mart_song_day WHERE song_key=c->>'song_key' ORDER BY day DESC LIMIT 1) AS title,
 (SELECT artist_text FROM marts.mart_song_day WHERE song_key=c->>'song_key' ORDER BY day DESC LIMIT 1) AS artist,
 coalesce((SELECT bool_or(current_key IS DISTINCT FROM frozen_key) FROM matched WHERE call_id=c->>'id'),false) AS changed,
 coalesce((SELECT jsonb_agg(apple_id ORDER BY apple_id) FROM apple WHERE call_id=c->>'id'),'[]') AS apple_ids,
 coalesce((SELECT jsonb_agg(apple_id ORDER BY apple_id) FROM grouped_apple WHERE call_id=c->>'id'),'[]') AS grouped_ids
FROM jsonb_array_elements($1::text::jsonb) c`;
export const callPlacesSql = `WITH calls AS (
 SELECT c->>'id' AS id,(c->>'submitted_at')::timestamptz AT TIME ZONE 'UTC' AS submitted,
 c->'facts'->'places_shown' AS shown,c->'apple_ids' AS apple_ids,coalesce(c->'grouped_ids','[]') AS grouped_ids FROM jsonb_array_elements($1::text::jsonb) c
), apple AS MATERIALIZED (
 SELECT c.id,jsonb_array_elements_text(c.apple_ids) AS apple_song_id FROM calls c
), prior_places AS MATERIALIZED (
 SELECT DISTINCT c.id,s.country,s.city
 FROM calls c JOIN apple a ON a.id=c.id JOIN marts.mart_shazam_chart_daily s ON s.apple_song_id=a.apple_song_id
 WHERE jsonb_typeof(c.shown) IS DISTINCT FROM 'array'
   AND s.chart_date BETWEEN c.submitted::date - 28 AND c.submitted::date
), places AS (
 SELECT DISTINCT ON(c.id,s.country,s.city) c.id,s.country,s.city,s.chart_date,s.chart,s.chart_type,s.position,s.source_keys,
 c.grouped_ids ? s.apple_song_id AS matched_copy
 FROM calls c JOIN apple a ON a.id=c.id JOIN marts.mart_shazam_chart_daily s ON s.apple_song_id=a.apple_song_id
 WHERE s.chart_date > c.submitted::date AND s.chart_date <= c.submitted::date + 28
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(c.shown)='array' THEN c.shown ELSE '[]' END) p
   WHERE s.country IS NOT DISTINCT FROM p->>'country' AND s.city IS NOT DISTINCT FROM p->>'city')
 AND (jsonb_typeof(c.shown) = 'array' OR NOT EXISTS (
   SELECT 1 FROM prior_places prior WHERE prior.id=c.id
     AND prior.country IS NOT DISTINCT FROM s.country AND prior.city IS NOT DISTINCT FROM s.city))
 ORDER BY c.id,s.country,s.city,s.chart_date,(c.grouped_ids ? s.apple_song_id),s.chart,s.position
), ranked AS (
 SELECT *,row_number() OVER (PARTITION BY id ORDER BY chart_date,country,city) AS ordinal FROM places
)
SELECT c.id, count(p.id)::text AS total, count(p.id) FILTER (WHERE p.matched_copy)::text AS copies,
 coalesce(jsonb_agg(jsonb_build_object('country',p.country,'city',p.city,'chart_date',p.chart_date::text,'chart',p.chart,'chart_type',p.chart_type,'position',p.position,'matched_copy',p.matched_copy)
 ORDER BY p.chart_date,p.country,p.city) FILTER (WHERE p.ordinal<=60),'[]') AS places,
 coalesce((SELECT jsonb_agg(DISTINCT k.value) FROM places x CROSS JOIN LATERAL jsonb_array_elements_text(x.source_keys::jsonb) k WHERE x.id=c.id),'[]') AS source_keys
FROM calls c LEFT JOIN ranked p ON p.id=c.id GROUP BY c.id ORDER BY c.id`;
export async function stamp(tx: TransactionSql, relation: string) {
  const [row] =
    await tx`SELECT s->>'cycle_id' AS cycle_id,s->>'close_no' AS close_no,s->>'built_at' AS built_at FROM (SELECT catalog.snapshot_stamp(${relation}) AS s) stamp`;
  const value = z
    .object({
      cycle_id: z.string().nullable().optional(),
      close_no: z.string().nullable().optional(),
      built_at: z.string().nullable().optional(),
    })
    .nullable()
    .parse(row ?? null);
  return martBuild.parse({
    relation,
    scope: "global",
    tenant_slug: null,
    stamped: !!value?.built_at,
    cycle_id: value?.cycle_id ?? null,
    close_no: value?.close_no == null ? null : String(value.close_no),
    built_at: value?.built_at ? new Date(value.built_at).toISOString() : null,
  });
}
const anchorsCache = budget.cache<{
  rows: z.infer<typeof anchorRow>[];
  build: z.infer<typeof martBuild>;
}>();
export function callAnchors(keys: string[]) {
  const unique = [...new Set(keys)].sort().slice(0, 50);
  return anchorsCache.read(
    `calls:anchors:${JSON.stringify(unique)}`,
    "heavy",
    () =>
      warehouse().begin(
        "isolation level repeatable read read only",
        async (tx) => {
          await tx.unsafe(
            `LOCK TABLE ${identityRelation} IN ACCESS SHARE MODE`,
          );
          const build = await stamp(tx, identityRelation);
          const rows = z
            .array(anchorRow)
            .parse(await tx.unsafe(anchorsSql, [JSON.stringify(unique)]));
          return { rows, build };
        },
      ),
  );
}
const matchedRow = z.object({
  call_id: z.uuid(),
  changed: z.boolean(),
  title: z.string().nullable(),
  artist: z.string().nullable(),
  apple_ids: z.array(z.string()),
  grouped_ids: z.array(z.string()),
});
const observedRow = z.object({
  id: z.uuid(),
  total: z.string(),
  copies: z.string(),
  places: z.array(callObservation.omit({ locator: true })),
  source_keys: z.array(z.string()),
});
export async function observeCalls(db: Sql, calls: SavedCall[]) {
  if (calls.length > 50 || calls.some((c) => c.facts.anchors.length > 20))
    throw new Error("Pick page is too large. Open an earlier week.");
  return db.begin("isolation level repeatable read read only", async (tx) => {
    await tx.unsafe(
      `LOCK TABLE ${identityRelation}, ${placesRelation}, marts.mart_song_day, ${clusterRelation} IN ACCESS SHARE MODE`,
    );
    const builds = [
      await stamp(tx, identityRelation),
      await stamp(tx, placesRelation),
      await stamp(tx, "marts.mart_song_day"),
      await stamp(tx, clusterRelation),
    ];
    const matched = z
      .array(matchedRow)
      .parse(await tx.unsafe(currentAnchorsSql, [JSON.stringify(calls)]));
    const inputs = calls.map((call) => ({
      ...call,
      apple_ids: matched.find((m) => m.call_id === call.id)?.apple_ids ?? [],
      grouped_ids:
        matched.find((m) => m.call_id === call.id)?.grouped_ids ?? [],
    }));
    const rows = z
      .array(observedRow)
      .parse(await tx.unsafe(callPlacesSql, [JSON.stringify(inputs)]));
    return {
      builds,
      queried_at: new Date().toISOString(),
      rows: rows.map((row) => ({
        ...row,
        title: matched.find((m) => m.call_id === row.id)?.title ?? null,
        artist: matched.find((m) => m.call_id === row.id)?.artist ?? null,
        changed: matched.find((m) => m.call_id === row.id)?.changed ?? false,
        matched: !!matched.find((m) => m.call_id === row.id)?.apple_ids.length,
        places: row.places.map((place) => ({
          ...place,
          locator: {
            component: "shazam_places",
            relation: placesRelation,
            row_key: {
              chart: place.chart,
              chart_date: place.chart_date,
              position: place.position,
            },
            window: { days: 28 },
            input_build: builds[1],
          },
        })),
      })),
    };
  });
}
export type CallRead = Awaited<ReturnType<typeof observeCalls>>;
const boardCache = budget.cache<CallRead>();
const immutable = new Map<string, CallRead>();
export async function callsBoard(week: string) {
  const calls = await budget.run("light", () =>
    readCalls(controlStore(), week),
  );
  if (!calls.length) return { calls, observations: null };
  const ids = createHash("sha256")
    .update(JSON.stringify(calls.map((c) => c.id)))
    .digest("hex");
  const observations = await boardCache
    .read(
      `calls:board:${week}`,
      "heavy",
      async () => {
        const result = await observeCalls(warehouse(), calls);
        const key = `${ids}:${JSON.stringify(result.builds)}`;
        const old = immutable.get(key);
        if (old) return old;
        if (immutable.size >= 64) {
          const oldest = immutable.keys().next().value;
          if (oldest) immutable.delete(oldest);
        }
        immutable.set(key, result);
        return result;
      },
      0,
    )
    .catch(() => null);
  return { calls, observations };
}
