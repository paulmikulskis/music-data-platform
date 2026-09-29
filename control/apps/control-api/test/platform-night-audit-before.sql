-- Expanded night query at f9789330. Run platform-night-budget.test.ts to compare.
WITH selected AS MATERIALIZED (
 SELECT r.*,s.source_key,true AS genuine,
 c.opened_by_dbt_run_id
 FROM control.run r JOIN control.warehouse w ON w.id=r.warehouse_id
 LEFT JOIN control.cycle c ON c.id=r.cycle_id
 LEFT JOIN control.streamline s ON s.id=r.streamline_id
 WHERE r.warehouse_id=w.id AND w.is_production
  AND r.kind IN ('invoke','dbt')
  AND coalesce(r.resolved_config->>'fixture','false')='false'
  AND coalesce(r.resolved_config->>'is_test','false')='false'
  AND NOT coalesce(r.resolved_config ? 'fixture_scenario',false) AND r.scope='global' AND (c.scope IS NULL OR c.scope='global')
  AND r.work_key !~ '^(test|fixture|sample|workbench|backfill|canary):'
  AND coalesce(c.opened_by_dbt_run_id,'') !~ '^(test|fixture|sample|workbench|backfill|canary):'
  AND coalesce(r.resolved_config->>'sample','false')='false'
  AND NOT EXISTS (SELECT 1 FROM control.audit_log sample WHERE sample.action='streamlines.probe'
    AND (sample.after->'result'->>'run_id'=r.id::text
      OR r.work_key='manual:'||(sample.after->'input'->>'key')
      OR EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(sample.after->'remote','[]'::jsonb)) remote
        WHERE remote->'result'->>'run_id'=r.id::text))) AND w.id=$3::uuid
 AND true
 AND (
   ((r.status='queued' OR NOT EXISTS (SELECT 1 FROM control.run_attempt a WHERE a.run_id=r.id))
     AND r.created_at >= $1::timestamptz AND r.created_at < $2::timestamptz)
   OR EXISTS (SELECT 1 FROM control.run_attempt a WHERE a.run_id=r.id
     AND a.started_at < $2::timestamptz AND (a.ended_at IS NULL OR a.ended_at > $1::timestamptz)
     AND coalesce(a.dbt_run_id,'') !~ '^(sample|test|fixture|workbench|canary|backfill):'))
), members AS MATERIALIZED (
 SELECT r.id AS run_id,m.target_id,m.resource_kind FROM selected r
 JOIN control.target_export_member m ON m.revision_id=r.revision_id
 WHERE NOT false
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
 WHERE NOT false
), batch_stats AS MATERIALIZED (
 SELECT run_id,
 count(DISTINCT target_id) FILTER (WHERE eligible)::int AS eligible,
 count(DISTINCT target_id) FILTER (WHERE succeeded)::int AS succeeded,
 count(DISTINCT target_id) FILTER (WHERE skipped)::int AS skipped,
 max(updated_at) AS evidence_at
 FROM batch_targets GROUP BY run_id
), coverage AS (
 SELECT r.id,
 CASE WHEN r.revision_id IS NOT NULL THEN coalesce(m.frozen_membership,0) END AS frozen_membership,
 CASE WHEN r.resolved_config ? 'target_coverage' AND r.revision_id IS NOT NULL THEN coalesce(b.eligible,0) END AS eligible,
 CASE WHEN r.resolved_config ? 'target_coverage' AND r.revision_id IS NOT NULL THEN coalesce(b.succeeded,0) END AS succeeded,
 CASE WHEN r.resolved_config ? 'target_coverage' AND r.revision_id IS NOT NULL THEN coalesce(b.skipped,0) END AS skipped,
 m.unit,b.evidence_at,
 r.created_at >= $1::timestamptz AND coalesce(b.evidence_at < $2::timestamptz,true) AS within_window
 FROM selected r LEFT JOIN member_stats m ON m.run_id=r.id
 LEFT JOIN batch_stats b ON b.run_id=r.id
), targets AS (
 SELECT id,within_window,jsonb_build_object('frozen_membership',frozen_membership,'eligible',eligible,
 'succeeded',succeeded,'skipped',skipped,'unit',unit,'evidence_at',to_char(evidence_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) AS payload FROM coverage
), all_attempts AS MATERIALIZED (
 SELECT a.*,count(*) OVER (PARTITION BY a.run_id) AS total FROM control.run_attempt a
 JOIN selected r ON r.id=a.run_id
), attempts AS MATERIALIZED (
 SELECT a.run_id,jsonb_agg(jsonb_build_object('attempt_no',a.attempt_no,'started_at',to_char(a.started_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
 'ended_at',to_char(a.ended_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'status',a.status,'trigger',CASE
 WHEN a.dbt_run_id LIKE 'manual:%' OR r.work_key LIKE 'manual:%' THEN 'manual'
 WHEN ca.runner IS NOT NULL AND ca.reason_category='other' AND a.dbt_run_id<>r.opened_by_dbt_run_id THEN 'retry/restore'
 WHEN ca.runner IS NOT NULL AND ca.reason_category='scheduled' AND a.dbt_run_id=r.opened_by_dbt_run_id
 AND left(c.opened_by_dbt_run_id, 7) <> 'manual:' AND left(c.opened_by_dbt_run_id, 9) <> 'backfill:' AND left(c.opened_by_dbt_run_id, 7) <> 'canary:' THEN 'scheduled'
  ELSE 'unknown' END,
 'trigger_evidence',jsonb_build_object('dbt_run_id',a.dbt_run_id,'reason_category',ca.reason_category,'runner',ca.runner),
 'targets',CASE WHEN a.total=1 THEN t.payload END)
 ORDER BY a.attempt_no) AS payload
 FROM selected r JOIN all_attempts a ON a.run_id=r.id
 LEFT JOIN control.cycle c ON c.id=r.cycle_id
 LEFT JOIN control.cycle_attempt ca ON ca.dbt_run_id=a.dbt_run_id AND ca.cycle_id=r.cycle_id
 JOIN targets t ON t.id=r.id
 WHERE a.started_at < $2::timestamptz AND (a.ended_at IS NULL OR a.ended_at > $1::timestamptz)
 AND coalesce(a.dbt_run_id,'') !~ '^(sample|test|fixture|workbench|canary|backfill):'
 GROUP BY a.run_id
), outputs AS MATERIALIZED (
 SELECT r.id,CASE WHEN r.genuine THEN count(d.id)::text END AS dump_count,
 CASE WHEN r.genuine THEN coalesce(sum(l.rows_landed),0)::text END AS rows_landed
 FROM selected r LEFT JOIN control.dump d ON d.run_id=r.id AND d.kind='output'
 LEFT JOIN LATERAL (SELECT sum(rows_inserted) AS rows_landed FROM control.load
   WHERE dump_id=d.id AND warehouse_id=r.warehouse_id AND status='loaded' AND target_table ~ '^raw\.[a-z]' AND target_table NOT IN ('raw.cost_ledger','raw.cycles','raw.cycle_inputs','raw.cycle_attempts','raw.dump_stamps','raw.targets_current','raw.targets_history')) l ON true
 GROUP BY r.id,r.genuine
), reader_batches AS MATERIALIZED (
 SELECT source_key,count(DISTINCT target_id) FILTER (WHERE succeeded)::int AS succeeded,
 max(updated_at) AS evidence_at
 FROM batch_targets WHERE source_key IS NOT NULL GROUP BY source_key
), reader_coverage AS (
 SELECT r.source_key,bool_and(t.within_window AND (t.payload->>'succeeded') IS NOT NULL) AS known,
 CASE WHEN count(DISTINCT t.payload->>'unit')=1 THEN min(t.payload->>'unit') END AS unit
 FROM selected r JOIN targets t ON t.id=r.id
 WHERE r.source_key IS NOT NULL GROUP BY r.source_key
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
 'runs',coalesce((SELECT jsonb_agg(jsonb_build_object('run_id',r.id,'source_key',r.source_key,'kind',r.kind,
 'admitted_at',to_char(r.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
 'cycle_id',r.cycle_id,'scope',r.scope,'warehouse_id',r.warehouse_id,'status',r.status,
 'attempts',coalesce(a.payload,'[]'::jsonb),'outputs',jsonb_build_object('dump_count',o.dump_count,'rows_landed',o.rows_landed),
 'targets',t.payload) ORDER BY r.created_at,r.id) FROM selected r JOIN targets t ON t.id=r.id
 JOIN outputs o ON o.id=r.id LEFT JOIN attempts a ON a.run_id=r.id),'[]'::jsonb),
 'coverage',coalesce((SELECT jsonb_agg(jsonb_build_object('source_key',source_key,'succeeded',CASE WHEN known THEN succeeded END,
 'unit',unit,'evidence_at',to_char(evidence_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) ORDER BY source_key) FROM reader_success),'[]'::jsonb),
 'closes',coalesce((SELECT jsonb_agg(jsonb_build_object('cycle_id',c.id,'cadence',c.cadence,'close_no',c.close_no::text,
 'closed_at',to_char(c.closed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),'status',c.status) ORDER BY c.closed_at) FROM control.cycle c
 WHERE c.scope='global' AND c.closed_at >= $1::timestamptz AND c.closed_at < $2::timestamptz
 AND EXISTS (SELECT 1 FROM control.run r JOIN control.warehouse w ON w.id=r.warehouse_id
   LEFT JOIN control.streamline s ON s.id=r.streamline_id
   WHERE r.cycle_id=c.id AND r.warehouse_id=w.id AND w.is_production
  AND r.kind IN ('invoke','dbt')
  AND coalesce(r.resolved_config->>'fixture','false')='false'
  AND coalesce(r.resolved_config->>'is_test','false')='false'
  AND NOT coalesce(r.resolved_config ? 'fixture_scenario',false) AND r.scope='global' AND (c.scope IS NULL OR c.scope='global')
  AND r.work_key !~ '^(test|fixture|sample|workbench|backfill|canary):'
  AND coalesce(c.opened_by_dbt_run_id,'') !~ '^(test|fixture|sample|workbench|backfill|canary):'
  AND coalesce(r.resolved_config->>'sample','false')='false'
  AND NOT EXISTS (SELECT 1 FROM control.audit_log sample WHERE sample.action='streamlines.probe'
    AND (sample.after->'result'->>'run_id'=r.id::text
      OR r.work_key='manual:'||(sample.after->'input'->>'key')
      OR EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(sample.after->'remote','[]'::jsonb)) remote
        WHERE remote->'result'->>'run_id'=r.id::text))) AND true AND w.id=$3::uuid)),'[]'::jsonb),
 'alerts',coalesce((SELECT jsonb_agg(jsonb_build_object('run_id',run_id,'attempt_no',attempt_no,'opened_at',to_char(opened_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
 'class',class,'resolved_at',to_char(resolved_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')) ORDER BY opened_at,id) FROM related_alerts),'[]'::jsonb)) AS payload;
