import { z } from "zod";
import {
  errorHint,
  nightWindow,
  platformNight,
  relationCount,
  relationCounts,
  type relationCountsInput,
} from "@mdp/contracts";
import {
  genuineSourceRun,
  outputTarget,
  probeExclusions,
  timelineRun,
} from "@mdp/contracts/platform-sql";
import { readRunnerState } from "@mdp/contracts/runner-state";
import { scheduledCycleSql } from "@mdp/contracts/health-policy.generated";
import { one, rows, type DB } from "./db.js";

// Aggregate each ledger independently. A dump, load or retry cannot multiply another ledger.
// Materialize shared totals so a low row estimate cannot repeat an aggregate in a nested loop.
const syntheticMember = "false";
const visibleMembership = "true";
export const nightSql = `WITH ${probeExclusions}, selected AS MATERIALIZED (
 SELECT r.*,s.source_key,(${genuineSourceRun}) AS genuine,
 c.opened_by_dbt_run_id
 FROM control.run r JOIN control.warehouse w ON w.id=r.warehouse_id
 LEFT JOIN control.cycle c ON c.id=r.cycle_id
 LEFT JOIN control.streamline s ON s.id=r.streamline_id
 WHERE ${timelineRun} AND w.id=$3::uuid
 AND (
   ((r.status='queued' OR NOT EXISTS (SELECT 1 FROM control.run_attempt a WHERE a.run_id=r.id))
     AND r.created_at >= $1::timestamptz AND r.created_at < $2::timestamptz)
   OR EXISTS (SELECT 1 FROM control.run_attempt a WHERE a.run_id=r.id
     AND a.started_at < $2::timestamptz AND (a.ended_at IS NULL OR a.ended_at > $1::timestamptz)
     AND coalesce(a.dbt_run_id,'') !~ '^(sample|test|fixture|workbench|canary|backfill):'))
), members AS MATERIALIZED (
 SELECT r.id AS run_id,m.target_id,m.resource_kind FROM selected r
 JOIN control.target_export_member m ON m.revision_id=r.revision_id
 WHERE NOT ${syntheticMember}
), member_stats AS MATERIALIZED (
 SELECT run_id,count(*)::int AS frozen_membership,
 CASE WHEN count(DISTINCT resource_kind)=1 THEN min(resource_kind) END AS unit
 FROM members GROUP BY run_id
), batch_targets AS MATERIALIZED (
 SELECT r.id AS run_id,r.source_key,b.updated_at,t.target_id,
 NOT markers ? ('skipped:'||t.target_id) AS eligible,
 markers ? t.target_id::text AND NOT markers ? ('skipped:'||t.target_id)
   AND NOT markers ? ('stale_target:'||t.target_id) AND NOT markers ? ('rejected:'||t.target_id) AS succeeded,
 markers ? ('skipped:'||t.target_id) AS skipped
 FROM selected r JOIN control.batch b ON b.run_id=r.id
 CROSS JOIN LATERAL unnest(b.target_ids) t(target_id)
 CROSS JOIN LATERAL (SELECT coalesce(b.cursor_checkpoint->'completed_targets','[]'::jsonb) AS markers) checkpoint
 JOIN control.target_export_member m ON m.revision_id=r.revision_id AND m.target_id=t.target_id
 WHERE NOT ${syntheticMember}
), batch_stats AS MATERIALIZED (
 SELECT run_id,
 count(DISTINCT target_id) FILTER (WHERE eligible)::int AS eligible,
 count(DISTINCT target_id) FILTER (WHERE succeeded)::int AS succeeded,
 count(DISTINCT target_id) FILTER (WHERE skipped)::int AS skipped,
 max(updated_at) AS evidence_at
 FROM batch_targets GROUP BY run_id
), coverage AS (
 SELECT r.*,
 CASE WHEN r.revision_id IS NOT NULL THEN coalesce(m.frozen_membership,0) END AS frozen_membership,
 CASE WHEN r.resolved_config ? 'target_coverage' AND r.revision_id IS NOT NULL THEN coalesce(b.eligible,0) END AS eligible,
 CASE WHEN r.resolved_config ? 'target_coverage' AND r.revision_id IS NOT NULL THEN coalesce(b.succeeded,0) END AS succeeded,
 CASE WHEN r.resolved_config ? 'target_coverage' AND r.revision_id IS NOT NULL THEN coalesce(b.skipped,0) END AS skipped,
 m.unit,b.evidence_at,
 r.created_at >= $1::timestamptz AND coalesce(b.evidence_at < $2::timestamptz,true) AS within_window
 FROM selected r LEFT JOIN member_stats m ON m.run_id=r.id
 LEFT JOIN batch_stats b ON b.run_id=r.id
), targets AS (
 SELECT coverage.*,jsonb_build_object('frozen_membership',frozen_membership,'eligible',eligible,
 'succeeded',succeeded,'skipped',skipped,'unit',unit,'evidence_at',to_char(evidence_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) AS payload FROM coverage
), run_payloads AS (
 SELECT r.id,r.created_at,jsonb_build_object('run_id',r.id,'source_key',r.source_key,'kind',r.kind,
 'admitted_at',to_char(r.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
 'cycle_id',r.cycle_id,'scope',r.scope,'warehouse_id',r.warehouse_id,'status',r.status,
 'attempts',coalesce(a.payload,'[]'::jsonb),'outputs',jsonb_build_object('dump_count',o.dump_count,'rows_landed',o.rows_landed),
 'targets',r.payload) AS payload
 FROM targets r
 -- Each lookup reads one run through the existing run_id indexes. Carrying the run fields
 -- with targets avoids joining materialized one-row estimates once per run.
 LEFT JOIN LATERAL (
   SELECT jsonb_agg(jsonb_build_object('attempt_no',a.attempt_no,
   'started_at',to_char(a.started_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
   'ended_at',to_char(a.ended_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'status',a.status,'trigger',CASE
   WHEN a.dbt_run_id LIKE 'manual:%' OR r.work_key LIKE 'manual:%' THEN 'manual'
   WHEN ca.runner IS NOT NULL AND ca.reason_category='other' AND a.dbt_run_id<>r.opened_by_dbt_run_id THEN 'retry/restore'
   WHEN ca.runner IS NOT NULL AND ca.reason_category='scheduled' AND a.dbt_run_id=r.opened_by_dbt_run_id
   AND ${scheduledCycleSql()} THEN 'scheduled'
   ELSE 'unknown' END,
   'trigger_evidence',jsonb_build_object('dbt_run_id',a.dbt_run_id,'reason_category',ca.reason_category,'runner',ca.runner),
   'targets',CASE WHEN a.total=1 THEN r.payload END) ORDER BY a.attempt_no) AS payload
   FROM (SELECT a.*,count(*) OVER () AS total FROM control.run_attempt a WHERE a.run_id=r.id) a
   LEFT JOIN control.cycle_attempt ca ON ca.dbt_run_id=a.dbt_run_id AND ca.cycle_id=r.cycle_id
   WHERE a.started_at < $2::timestamptz AND (a.ended_at IS NULL OR a.ended_at > $1::timestamptz)
   AND coalesce(a.dbt_run_id,'') !~ '^(sample|test|fixture|workbench|canary|backfill):'
 ) a ON true
 LEFT JOIN LATERAL (
   SELECT CASE WHEN r.genuine THEN count(d.id)::text END AS dump_count,
   CASE WHEN r.genuine THEN coalesce(sum(l.rows_landed),0)::text END AS rows_landed
   FROM control.dump d
   LEFT JOIN LATERAL (SELECT sum(rows_inserted) AS rows_landed FROM control.load
     WHERE dump_id=d.id AND warehouse_id=r.warehouse_id AND status='loaded' AND ${outputTarget}) l ON true
   WHERE d.run_id=r.id AND d.kind='output'
 ) o ON true
), reader_batches AS MATERIALIZED (
 SELECT source_key,count(DISTINCT target_id) FILTER (WHERE succeeded)::int AS succeeded,
 max(updated_at) AS evidence_at
 FROM batch_targets WHERE source_key IS NOT NULL GROUP BY source_key
), reader_coverage AS (
 SELECT t.source_key,bool_and(t.within_window AND (t.payload->>'succeeded') IS NOT NULL) AS known,
 CASE WHEN count(DISTINCT t.payload->>'unit')=1 THEN min(t.payload->>'unit') END AS unit
 FROM targets t
 WHERE t.source_key IS NOT NULL GROUP BY t.source_key
), reader_success AS (
 SELECT r.source_key,coalesce(b.succeeded,0) AS succeeded,r.known,r.unit,b.evidence_at
 FROM reader_coverage r LEFT JOIN reader_batches b ON b.source_key=r.source_key
), related_alerts AS (
 SELECT DISTINCT a.id,a.run_id,a.attempt_no,a.opened_at,a.class,a.resolved_at
 FROM control.alert a JOIN selected r ON (a.run_id=r.id OR
   (a.class='cadence_failed' AND a.subject_type='cycle' AND a.subject_id=r.cycle_id::text AND a.attempt_no IS NULL))
 WHERE (a.attempt_no IS NULL OR EXISTS (SELECT 1 FROM control.run_attempt ra WHERE ra.run_id=r.id
   AND ra.attempt_no=a.attempt_no AND ra.started_at < $2::timestamptz AND (ra.ended_at IS NULL OR ra.ended_at > $1::timestamptz)))
 AND a.opened_at < $2::timestamptz AND (a.resolved_at IS NULL OR a.resolved_at >= $1::timestamptz)
)
SELECT jsonb_build_object(
 'runs',coalesce((SELECT jsonb_agg(payload ORDER BY created_at,id) FROM run_payloads),'[]'::jsonb),
 'coverage',coalesce((SELECT jsonb_agg(jsonb_build_object('source_key',source_key,'succeeded',CASE WHEN known THEN succeeded END,
 'unit',unit,'evidence_at',to_char(evidence_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) ORDER BY source_key) FROM reader_success),'[]'::jsonb),
 'closes',coalesce((SELECT jsonb_agg(jsonb_build_object('cycle_id',c.id,'cadence',c.cadence,'close_no',c.close_no::text,
 'closed_at',to_char(c.closed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'status',c.status) ORDER BY c.closed_at) FROM control.cycle c
 WHERE c.scope='global' AND c.closed_at >= $1::timestamptz AND c.closed_at < $2::timestamptz
 -- OFFSET keeps this existence check tied to one cycle and its run_cycle_idx lookup.
 AND EXISTS (SELECT 1 FROM control.run r JOIN control.warehouse w ON w.id=r.warehouse_id
   LEFT JOIN control.streamline s ON s.id=r.streamline_id
   WHERE r.cycle_id=c.id AND ${timelineRun} AND ${visibleMembership} AND w.id=$3::uuid OFFSET 0)),'[]'::jsonb),
 'alerts',coalesce((SELECT jsonb_agg(jsonb_build_object('run_id',run_id,'attempt_no',attempt_no,'opened_at',to_char(opened_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
 'class',class,'resolved_at',to_char(resolved_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) ORDER BY opened_at,id) FROM related_alerts),'[]'::jsonb)) AS payload`;

const nightData = platformNight
  .pick({ runs: true, coverage: true, closes: true })
  .extend({
    alerts: platformNight.shape.alerts.element
      .omit({ summary: true, next_step: true })
      .array(),
  });
// The pool identity prevents one database's cached fixture or stale pin reaching another.
const caches = new WeakMap<
  DB,
  Map<
    string,
    { expires: number; value: Promise<z.infer<typeof platformNight>> }
  >
>();
export async function readNight(
  db: DB,
  input: z.infer<typeof nightWindow>,
  cacheOwner = db,
) {
  const window = nightWindow.parse(input);
  const production = await one(
    db,
    z.object({ id: z.uuid() }),
    "SELECT id FROM control.warehouse WHERE is_production",
  );
  let cache = caches.get(cacheOwner);
  if (!cache) {
    cache = new Map();
    caches.set(cacheOwner, cache);
  }
  const key = JSON.stringify([production.id, window.since, window.until]);
  const old = cache.get(key);
  if (old && old.expires > Date.now()) return old.value;
  const value = (async () => {
    const data = await one(db, z.object({ payload: nightData }), nightSql, [
      window.since,
      window.until,
      production.id,
    ]);
    const runner = await readRunnerState(db);
    return platformNight.parse({
      ...data.payload,
      window: { ...window, queried_at: new Date().toISOString() },
      warehouse_id: production.id,
      alerts: data.payload.alerts.map((alert) => ({
        ...alert,
        ...errorHint(alert.class),
      })),
      runner,
      next_due: runner.next_scheduled_at,
      next_step: "Open /runs to inspect a reading.",
    });
  })();
  if (cache.size >= 128) cache.delete(cache.keys().next().value ?? "");
  cache.set(key, { expires: Date.now() + 60000, value });
  try {
    return await value;
  } catch (error) {
    cache.delete(key);
    throw error;
  }
}

export async function readRelationCounts(
  db: DB,
  input: z.infer<typeof relationCountsInput>,
) {
  const captured = await rows(
    db,
    relationCount,
    `WITH requested AS (SELECT * FROM jsonb_to_recordset($1::text::jsonb)
      AS key(relation text,build_key text,input_hash text))
    SELECT DISTINCT c.warehouse_id,c.relation,c.build_key,c.captured_at,c.row_count::text,c.latest_at,c.basis,c.input_hash
    FROM requested q CROSS JOIN LATERAL (
      SELECT * FROM control.showcase_relation_count c
      WHERE c.warehouse_id=(SELECT id FROM control.warehouse WHERE is_production) AND c.relation=q.relation
      AND (c.build_key=q.build_key OR c.input_hash=q.input_hash)
      ORDER BY (c.build_key IS NOT DISTINCT FROM q.build_key) DESC,c.captured_at DESC LIMIT 2
    ) c`,
    [JSON.stringify(input.keys)],
  );
  return relationCounts.parse({
    queried_at: new Date().toISOString(),
    counts: input.keys.map((key) => {
      const sameBuild = captured.find(
        (row) =>
          row.relation === key.relation && row.build_key === key.build_key,
      );
      const capture =
        sameBuild?.input_hash === key.input_hash ? sameBuild : null;
      const last_good =
        captured
          .filter(
            (row) =>
              row.relation === key.relation &&
              row.input_hash === key.input_hash,
          )
          .sort((a, b) => b.captured_at.localeCompare(a.captured_at))[0] ??
        null;
      return {
        key,
        capture,
        last_good,
        denominator: capture?.row_count ?? null,
        suppression:
          key.build_key === null
            ? "unstamped"
            : capture
              ? "none"
              : sameBuild
                ? "different_input"
                : "not_captured",
      };
    }),
    next_step: "Open source details to inspect the capture date.",
  });
}
