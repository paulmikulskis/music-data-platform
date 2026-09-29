import { z } from "zod";
import { rows, type DB } from "./db.js";
import { service } from "./service.js";

export const probeResult = z.object({ id: z.string(), status: z.string(), http_status: z.number().nullable(), source_key: z.string().nullable().default(null) });
export async function probeTargets(db: DB, ids: string[]) {
  const targets = await rows(db, z.record(z.string(), z.unknown()),
    `SELECT t.*,s.kind,s.tenant_id,p.resource_kind,p.canonical_key,coalesce(p.params_json,'{}'::jsonb) AS params_json
     FROM control.target t JOIN control.target_set s ON s.id=t.target_set_id
     LEFT JOIN control.target_spec p ON p.target_id=t.id WHERE t.id=ANY($1::uuid[]) ORDER BY t.id`, [ids]);
  const results = [];
  for (const target of targets) {
    try {
      const result = await service("/v1/targets/probe", z.object({ results: z.array(probeResult) }), { targets: [target] });
      results.push(...result.results);
    } catch {
      console.warn("Target probe unavailable; target state is unchanged");
      results.push({ id: String(target.id), status: "probe_unavailable", http_status: null, source_key: null });
    }
  }
  return { results };
}
export async function probeActivation(db: DB, ids: string[]) {
  // Activation is used by scheduled promoters. Record a pending check without any HTTP.
  // The background worker and the explicit probe command provide the network evidence.
  await db.unsafe(`INSERT INTO control.audit_log(actor,action,subject,after)
    SELECT 'target-probe','targets.probePending',id::text,'{"status":"not_probed","warning":"Activation proceeds; probe is advisory"}'::jsonb
    FROM unnest($1::uuid[]) id`, [ids]);
  console.warn("Target activation: not probed; advisory check pending");
  await db.unsafe("UPDATE control.alert SET resolved_at=now() WHERE class='stale_target' AND subject_type='target' AND subject_id=ANY($1::text[]) AND resolved_at IS NULL", [ids]);
  return { results: ids.map(id=>({id,status:"not_probed",http_status:null,source_key:null})) };
}

export async function probeImport<T extends {dry_run:boolean;rows:Record<string,unknown>[]}>(db: DB, result:T): Promise<T> {
  if (result.dry_run) return result;
  const ids = result.rows.flatMap(row=>typeof row.id === "string" ? [row.id] : []);
  const checked = await probeActivation(db, ids);
  return { ...result, rows: result.rows.map(row=>({...row,probe:checked.results.find(r=>r.id===row.id)?.status ?? "skipped"})) };
}

export async function pendingProbeTargets(db: DB) {
  return rows(db, z.object({id:z.uuid(),seed:z.boolean()}),
    `SELECT t.id,coalesce(t.activated_at IS NULL AND s.promotion_reason='seed',false) AS seed
     FROM control.target t LEFT JOIN control.target_spec s ON s.target_id=t.id
     WHERE t.resolution_status='resolved' AND t.deactivated_at IS NULL AND
       ((t.activated_at IS NULL AND s.promotion_reason='seed' AND NOT EXISTS (
         SELECT 1 FROM control.audit_log checked WHERE checked.action='targets.probeChecked' AND checked.subject=t.id::text
         AND checked.after->>'status' NOT IN ('probe_unavailable','skipped'))) OR EXISTS (
         SELECT 1 FROM control.audit_log pending WHERE pending.action='targets.probePending' AND pending.subject=t.id::text
         AND NOT EXISTS (SELECT 1 FROM control.audit_log checked WHERE checked.action='targets.probeChecked'
           AND checked.subject=t.id::text AND checked.at>=pending.at AND checked.after->>'status' NOT IN ('probe_unavailable','skipped'))))
     ORDER BY (SELECT max(at) FROM control.audit_log a WHERE a.action='targets.probeChecked' AND a.subject=t.id::text) NULLS FIRST,t.id
     LIMIT 20`);
}
