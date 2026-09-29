import "server-only";
import { mart_song_day } from "@mdp/data-sdk";
import { scheduledCycleSql } from "@mdp/contracts/health-policy.generated";
import { randomUUID } from "node:crypto";
import type { Sql } from "postgres";
import { z } from "zod";
import { callAnchor, callSnapshot } from "../lib/calls";
import {
  draftCandidate,
  draftWeek,
  draftReadDay,
  draftWindowPassed,
} from "../lib/draft";
import { stamp } from "./call-reads";
import { freezeDraft, readDraft } from "./draft-store";
import { controlStore, warehouse } from "./clients";
import { budget } from "./read-budget";
import { consistentCallBuilds } from "./call-builds";

// Daily entries use their observation day. Weekly entries also need their whole interval within Saturday.
export const traySql = `SELECT a.song_key,a.artist_stage,a.age_class,a.discovery_entries::int,
 a.market_count::int,a.entered_lists::int,a.list_reach_tier,a.movement_list,a.rank::int,a.window_days,a.day::text,a.source_keys,
 (SELECT jsonb_agg(x ORDER BY x.platform,x.platform_track_id) FROM (
 SELECT DISTINCT k.platform,k.platform_track_id,k.song_key FROM explore_intermediate.int_song_key__daily k
 WHERE k.song_key IN (SELECT jsonb_array_elements_text(a.member_song_keys::jsonb)) OR k.song_key=a.song_key
 ORDER BY k.platform,k.platform_track_id LIMIT 21) x) AS anchors
 FROM marts.mart_arrivals_current a WHERE a.day=$1::date AND EXISTS (
 SELECT 1 FROM explore_intermediate.int_cluster_entries__daily e WHERE e.song_key=a.cluster_key AND e.day=$1::date AND (
 (e.platform='shazam' AND split_part(e.list_id,':',2)='discovery' AND e.event_type='chart_entry') OR
 (e.platform<>'shazam' AND e.list_kind IN ('editorial','new_music') AND e.event_type IN ('add','entered_head')
 AND (e.observed_at AT TIME ZONE 'UTC')::date=$1::date
 AND EXISTS (SELECT 1 FROM explore_intermediate.int_playlist__snapshots s
 WHERE s.platform=e.platform AND s.playlist_id=e.list_id AND s.snapshot_id=e.snapshot_id
 AND s.variant=e.locator::jsonb->'row_key'->>'variant' AND s.stream=e.locator::jsonb->'row_key'->>'stream'
 AND (s.cadence='daily' OR (e.locator::jsonb->'window'->>'start')::timestamptz >= $1::date AT TIME ZONE 'UTC')))))
 ORDER BY a.movement_list,a.rank,md5($1::text || ':' || a.song_key)`;
const rowSchema = draftCandidate.omit({ snapshot: true }).extend({
  anchors: z.array(callAnchor).min(1).max(21),
  window_days: z.number().nullable(),
  day: z.iso.date(),
  source_keys: z.preprocess(
    (v) => (typeof v === "string" ? JSON.parse(v) : v),
    z.array(z.string()),
  ),
});
export async function captureTray(db: Sql, week: string) {
  draftWeek.parse(week);
  return db.begin("isolation level repeatable read read only", async (tx) => {
    await tx`LOCK TABLE marts.mart_arrivals_current,explore_intermediate.int_cluster_entries__daily,explore_intermediate.int_song_key__daily,explore_intermediate.int_playlist__snapshots IN ACCESS SHARE MODE`;
    const builds = await Promise.all([
      stamp(tx, "marts.mart_arrivals_current"),
      stamp(tx, "explore_intermediate.int_cluster_entries__daily"),
      stamp(tx, "explore_intermediate.int_song_key__daily"),
      stamp(tx, "explore_intermediate.int_playlist__snapshots"),
    ]);
    if (!consistentCallBuilds(builds)) return null;
    const rows = z
      .array(rowSchema)
      .parse(await tx.unsafe(traySql, [draftReadDay(week)]));
    const candidates = rows.map((row) =>
      draftCandidate.parse({
        ...row,
        snapshot: callSnapshot.parse({
          v: 1,
          card: "arrival",
          song_key: row.song_key,
          anchors: row.anchors.slice(0, 20),
          anchors_truncated: row.anchors.length > 20,
          places_shown: null,
          builds,
          source_keys: row.source_keys,
          facts_day: row.day,
          close_no: builds[0]?.close_no,
          idempotency_key: randomUUID(),
          handle: "rules",
          exp: 0,
          draft_week: week,
          list: row.movement_list,
          window_days: row.window_days,
          entered_lists: String(row.entered_lists),
          discovery: [],
        }),
      }),
    );
    return { candidates, build: builds[0]! };
  });
}
export async function ensureDraft(week: string) {
  const existing = await budget.run("light", () =>
    readDraft(controlStore(), week),
  );
  if (existing) return existing;
  if (draftWindowPassed(week)) return null;
  if (budget.runnerBusy) return null;
  const tray = await budget.run("heavy", () => captureTray(warehouse(), week));
  if (!tray) return null;
  // A stamp belongs to Saturday's closed daily cycle, even when the tray is empty.
  const eligible = await budget.run("light", () =>
    controlStore().unsafe(
      `SELECT id FROM control.cycle
    WHERE id=$1 AND cadence='daily' AND scope='global' AND status='closed'
    AND (opened_at AT TIME ZONE 'UTC')::date=$2::date
    AND ${scheduledCycleSql()} AND close_no=$3`,
      [tray.build.cycle_id, draftReadDay(week), tray.build.close_no],
    ),
  );
  if (!eligible.length) return null;
  return budget.run("light", () =>
    freezeDraft(controlStore(), week, tray.candidates),
  );
}

// Display names stay live; the frozen tray and call snapshots contain no names.
export const draftNamesSql = `SELECT requested.song_key, latest.title_text, latest.artist_text
 FROM jsonb_array_elements_text($1::text::jsonb) requested(song_key)
 LEFT JOIN LATERAL (SELECT title_text,artist_text FROM marts.mart_song_day
 WHERE song_key=requested.song_key ORDER BY day DESC LIMIT 1) latest ON true
 ORDER BY requested.song_key`;
const draftName = mart_song_day.pick({
  song_key: true,
  title_text: true,
  artist_text: true,
});
export type DraftName = z.infer<typeof draftName>;
export async function readDraftNames(db: Sql, keys: string[]) {
  return z
    .array(draftName)
    .parse(
      await db.unsafe(draftNamesSql, [JSON.stringify([...new Set(keys)])]),
    );
}
