import { scheduledCycleSql } from "./health-policy.generated.js";
import { z } from "zod";
import { rows, type DB } from "./db.js";

// Only attempted, terminal cycles count. Non-due weekly members are absent from this history.
const histories = `WITH cycles AS (
  SELECT r.streamline_id,c.id,c.opened_at,max(r.id::text) AS run_id,
    max(coalesce((r.resolved_config->'target_coverage'->>'stale_target_cycles')::int,2)) AS threshold,
    bool_or(EXISTS (SELECT 1 FROM control.dead_letter d WHERE d.run_id=r.id AND d.target_id=$1
      AND d.reason LIKE 'stale_target:%')) AS stale
  FROM control.run r JOIN control.cycle c ON c.id=r.cycle_id
  JOIN control.batch b ON b.run_id=r.id JOIN control.target t ON t.id=$1
  WHERE t.id=ANY(b.target_ids) AND c.opened_at >= t.activated_at
    AND r.status IN ('succeeded','partial','failed')
    AND NOT coalesce(b.cursor_checkpoint->'completed_targets','[]'::jsonb) ? ('skipped:'||t.id::text)
    AND ${scheduledCycleSql('c.opened_by_dbt_run_id')}
  GROUP BY r.streamline_id,c.id,c.opened_at
), health AS (
  SELECT c.*,
    EXISTS (SELECT 1 FROM control.call_ledger f JOIN control.run fr ON fr.id=f.run_id
      WHERE fr.cycle_id=c.id AND fr.streamline_id=c.streamline_id AND f.target_id=$1 AND f.http_status IN (404,410))
    AND NOT EXISTS (
      SELECT 1 FROM control.call_ledger f JOIN control.run fr ON fr.id=f.run_id
      WHERE fr.cycle_id=c.id AND fr.streamline_id=c.streamline_id AND f.target_id=$1 AND f.http_status IN (404,410)
        AND NOT EXISTS (
          SELECT 1 FROM control.call_ledger p JOIN control.run pr ON pr.id=p.run_id
          JOIN control.batch pb ON pb.run_id=pr.id AND p.target_id=ANY(pb.target_ids)
          WHERE pr.cycle_id=c.id AND pr.streamline_id=c.streamline_id AND pr.status IN ('succeeded','partial','failed')
            AND p.vendor=f.vendor AND p.target_id<>$1 AND p.http_status BETWEEN 200 AND 299
            AND pb.cursor_checkpoint->'completed_targets' ? p.target_id::text
            AND NOT pb.cursor_checkpoint->'completed_targets' ? ('stale_target:'||p.target_id::text)
            AND NOT pb.cursor_checkpoint->'completed_targets' ? ('rejected:'||p.target_id::text)
            AND NOT pb.cursor_checkpoint->'completed_targets' ? ('skipped:'||p.target_id::text)
        )
    ) AS healthy FROM cycles c
), ordered AS (
  SELECT *,row_number() OVER (PARTITION BY streamline_id ORDER BY opened_at DESC,id DESC) AS position,
    sum(CASE WHEN stale THEN 0 ELSE 1 END) OVER
    (PARTITION BY streamline_id ORDER BY opened_at DESC,id DESC) AS breaks FROM health
) SELECT streamline_id::text AS source,max(threshold)::int AS threshold,count(*)::int AS streak,
    bool_and(healthy) AS healthy,(array_agg(id::text ORDER BY opened_at DESC,id DESC))[1] AS cycle_id,
    (array_agg(run_id ORDER BY opened_at DESC,id DESC))[1] AS run_id
  FROM ordered WHERE breaks=0 AND stale AND position<=threshold GROUP BY streamline_id`;

export async function parkStaleTargets(db: DB) {
  const parked: string[] = [];
  const warnings: {run_id:string;source:string;cycle_id:string;reason:string}[] = [];
  const lock = await rows(db,z.object({locked:z.boolean()}),"SELECT pg_try_advisory_xact_lock(hashtext('target-lifecycle')) AS locked");
  if (!lock[0]?.locked) return {parked,warnings};
  const candidates = await rows(db, z.object({ id: z.uuid(), target_set_id:z.uuid() }),
    `SELECT DISTINCT t.id,t.target_set_id FROM control.target t JOIN control.dead_letter d ON d.target_id=t.id
     WHERE t.activated_at IS NOT NULL AND t.deactivated_at IS NULL AND d.reason LIKE 'stale_target:%' ORDER BY t.id`);
  for (const { id, target_set_id } of candidates) {
    const history = await rows(db,z.object({source:z.string(),threshold:z.number(),streak:z.number(),healthy:z.boolean(),cycle_id:z.string(),run_id:z.string()}),histories,[id]);
    const failure = history.find(h=>h.streak>=h.threshold);
    if (!failure) continue;
    if (!failure.healthy) {
      if (!warnings.some(w=>w.source===failure.source && w.cycle_id===failure.cycle_id))
        warnings.push({...failure,reason:"No healthy peer on the same host; targets stay active"});
      continue;
    }
    const [capacity] = await rows(db,z.object({eligible:z.number(),parked:z.number()}),`
      SELECT (SELECT count(DISTINCT t.id)::int FROM control.run r JOIN control.batch b ON b.run_id=r.id
        JOIN control.target t ON t.id=ANY(b.target_ids) WHERE r.cycle_id=$1 AND t.target_set_id=$2
        AND NOT coalesce(b.cursor_checkpoint->'completed_targets','[]'::jsonb) ? ('skipped:'||t.id::text)) AS eligible,
      (SELECT count(*)::int FROM control.audit_log WHERE action='targets.parkStale'
        AND after->>'cycle_id'=$1::text AND after->>'target_set_id'=$2::text) AS parked`,[failure.cycle_id,target_set_id]);
    // Allow one target or ten percent per cycle, including earlier recovery passes.
    if (!capacity || capacity.parked >= Math.max(1, Math.floor(capacity.eligible * .1))) {
      if (!warnings.some(w=>w.source===failure.source && w.cycle_id===failure.cycle_id))
        warnings.push({...failure,reason:"Parking limit reached. Review the remaining targets before deactivating them."});
      continue;
    }
    const changed = await rows(db,z.object({id:z.uuid()}),"UPDATE control.target SET deactivated_at=now(),updated_at=now() WHERE id=$1 AND deactivated_at IS NULL RETURNING id",[id]);
    if (!changed.length) continue;
    await db.unsafe(`INSERT INTO control.audit_log(actor,action,subject,after)
      VALUES ('target-lifecycle','targets.parkStale',$1,$2::text::jsonb)`,
      [id, JSON.stringify({ reason: "stale_target", cycles: failure.streak, threshold: failure.threshold, cycle_id:failure.cycle_id,target_set_id })]);
    parked.push(id);
  }
  return { parked, warnings };
}
