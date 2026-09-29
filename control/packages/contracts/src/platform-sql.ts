import { scheduledCycleSql } from "./health-policy.generated.js";

export const genuineSourceRun = "true";

// Both reads use the same warehouse and fixture boundary.
const productionEnvironment = `r.warehouse_id=w.id AND w.is_production
  AND r.kind IN ('invoke','dbt')
  AND coalesce(r.resolved_config->>'fixture','false')='false'
  AND coalesce(r.resolved_config->>'is_test','false')='false'
  AND NOT coalesce(r.resolved_config ? 'fixture_scenario',false)`;
// Headline ingestion counts production scheduled runs only.
export const productionRun = `${productionEnvironment}
  AND ${scheduledCycleSql("c.opened_by_dbt_run_id")}
  AND r.work_key !~ '^(test|fixture|manual|backfill|canary):'`;
const costValue =
  "CASE WHEN k.cost_microcents>0 THEN k.cost_microcents::numeric ELSE k.cost_cents::numeric*1000000 END";
const costLabel =
  "CASE WHEN bool_and(k.origin='estimate') THEN 'estimate' WHEN bool_and(k.origin<>'estimate') THEN 'reconciled' ELSE 'partly reconciled' END";

export const outputTarget =
  "target_table ~ '^raw\\.[a-z]' AND target_table NOT IN ('raw.cost_ledger','raw.cycles','raw.cycle_inputs','raw.cycle_attempts','raw.dump_stamps','raw.targets_current','raw.targets_history')";

export const ingestionSql = `WITH loaded AS MATERIALIZED (
  SELECT dump_id,warehouse_id,loaded_at,rows_inserted FROM control.load
  WHERE status='loaded' AND loaded_at >= $1::timestamptz AND loaded_at <= $2::timestamptz
  AND ${outputTarget}
) SELECT (l.loaded_at AT TIME ZONE 'UTC')::date::text AS day,s.source_key,sum(l.rows_inserted)::text AS rows_inserted
  FROM loaded l JOIN control.warehouse w ON w.id=l.warehouse_id
  JOIN LATERAL (SELECT run_id,streamline_id,kind FROM control.dump WHERE id=l.dump_id LIMIT 1) d ON true
  JOIN LATERAL (SELECT warehouse_id,kind,work_key,resolved_config,cycle_id,revision_id FROM control.run WHERE id=d.run_id LIMIT 1) r ON true
  JOIN LATERAL (SELECT opened_by_dbt_run_id FROM control.cycle WHERE id=r.cycle_id LIMIT 1) c ON true
  JOIN control.streamline s ON s.id=d.streamline_id
  WHERE ${productionRun} AND ${genuineSourceRun} AND d.kind='output'
  GROUP BY day,s.source_key ORDER BY day,s.source_key`;

export const ingestionEnvelopeSql = `WITH by_source AS (${ingestionSql}), by_day AS (
  SELECT day, sum(rows_inserted::numeric)::text AS rows_inserted FROM by_source GROUP BY day
) SELECT jsonb_build_object('rows', coalesce((SELECT jsonb_agg(b ORDER BY day,source_key) FROM by_source b),'[]'::jsonb),
 'summary', jsonb_build_object('rows_inserted',coalesce(sum(rows_inserted::numeric),0)::text,
 'sources_live',count(DISTINCT source_key) FILTER (WHERE rows_inserted::numeric>0)::text,
 'today_rows',coalesce(sum(rows_inserted::numeric) FILTER (WHERE day=($2::timestamptz AT TIME ZONE 'UTC')::date::text),0)::text,
 'today_sources',count(DISTINCT source_key) FILTER (WHERE rows_inserted::numeric>0 AND day=($2::timestamptz AT TIME ZONE 'UTC')::date::text)::text,
 'today_measured',count(*) FILTER (WHERE day=($2::timestamptz AT TIME ZONE 'UTC')::date::text)>0,
 'days',coalesce((SELECT jsonb_agg(d ORDER BY day) FROM by_day d),'[]'::jsonb)),
 'next_step','Open /runs to inspect load receipts.') AS payload FROM by_source`;

// Both totals share one statement snapshot, including a reconciliation committed during this read.
export const costSql = `SELECT count(*)::text AS current_rows, (k.occurred_at AT TIME ZONE 'UTC')::date::text AS day,
  coalesce(round(sum(${costValue})/1000000),0)::text AS cost_cents,
  CASE WHEN count(*)=0 THEN 'estimate' ELSE ${costLabel} END AS label
  FROM control.cost_ledger k WHERE k.is_current
  AND k.occurred_at >= $1::timestamptz AND k.occurred_at <= $2::timestamptz
  GROUP BY GROUPING SETS ((), ((k.occurred_at AT TIME ZONE 'UTC')::date)) ORDER BY day NULLS FIRST`;
// Saved catalog estimates have no row-level basis for excluding synthetic identities.
// Keep operator storage metadata, but never reuse those estimates as genuine row totals.
export const inventoryHistorySql =
  "SELECT day::text,layer,relations,NULL::text AS rows_est,bytes::text,captured_at,false AS complete FROM control.showcase_inventory WHERE warehouse=$1 AND day >= ($2::timestamptz AT TIME ZONE 'UTC')::date AND day <= ($3::timestamptz AT TIME ZONE 'UTC')::date ORDER BY day,layer";
export const firstInventorySql =
  "SELECT min(day)::text AS day FROM control.showcase_inventory WHERE warehouse=$1";

// Expand probe receipts once. Run ids stay text: older audits can contain non-UUID values.
export const probeExclusions = `probe_audits AS MATERIALIZED (
  SELECT after FROM control.audit_log WHERE action='streamlines.probe'
), probe_run_ids AS MATERIALIZED (
  SELECT after->'result'->>'run_id' AS run_id FROM probe_audits
  UNION
  SELECT remote->'result'->>'run_id' FROM probe_audits
  CROSS JOIN LATERAL jsonb_array_elements(coalesce(after->'remote','[]'::jsonb)) remote
), probe_work_keys AS MATERIALIZED (
  SELECT DISTINCT 'manual:'||(after->'input'->>'key') AS work_key FROM probe_audits
)`;

// A timeline includes real manual production work. Headline totals keep productionRun.
// Callers include probeExclusions so each equality becomes an anti join over the shared sets.
export const timelineRun = `${productionEnvironment} AND r.scope='global' AND (c.scope IS NULL OR c.scope='global')
  AND r.work_key !~ '^(test|fixture|sample|workbench|backfill|canary):'
  AND coalesce(c.opened_by_dbt_run_id,'') !~ '^(test|fixture|sample|workbench|backfill|canary):'
  AND coalesce(r.resolved_config->>'sample','false')='false'
  AND NOT EXISTS (SELECT 1 FROM probe_run_ids p WHERE p.run_id=r.id::text)
  AND NOT EXISTS (SELECT 1 FROM probe_work_keys p WHERE p.work_key=r.work_key)`;
