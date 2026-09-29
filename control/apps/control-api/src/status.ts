import { alertGroupDto, cadenceStatusDto, heartbeatStatusDto, healthSummaryDto, sendsStatusDto, versionDto, type PlatformStatus } from "@mdp/contracts";
import { rows, one, type DB } from "./db.js";
import { emailClasses } from "./email.js";
import { scheduledCycleSql } from "./health-policy.generated.js";
import { coreLauncherStatus } from "./core-launcher.js";

// The one policy for API, CLI and console. Strictly greater than each age boundary.
export const statusRules = {
  broken: "A critical alert, an unreachable data API, a closed cycle older than two intervals, or an open cycle older than one interval.",
  attention: "Otherwise: any alert; a missing close, overdue close or open cycle older than one interval; failed, pending, skipped or abandoned email; unknown or mismatched builds.",
  healthy: "Otherwise: all cadences have a close within one interval, no outstanding signals, and API builds match. Young open cycles are normal.",
};
export function verdictFor(status: Pick<PlatformStatus, "cadences" | "alerts" | "sends" | "versions">) {
  const broken: string[] = [], attention: string[] = [];
  for (const row of status.cadences) {
    const label = `${row.cadence} ${row.scope}`;
    if (!row.last_closed) attention.push(`${label}: no closed cycle`);
    else if (row.last_closed.age_seconds > row.interval_seconds * 2) broken.push(`${label}: close older than two intervals`);
    else if (row.overdue) attention.push(`${label}: close overdue`);
    for (const cycle of row.open) {
      if (cycle.age_seconds > row.interval_seconds) broken.push(`${label}: cycle ${cycle.id} open over one interval`);
    }
  }
  for (const alert of status.alerts)
    (alert.severity === "critical" ? broken : attention).push(`${alert.count} ${alert.severity} ${alert.class} alerts`);
  for (const [state, count] of Object.entries(status.sends))
    if (Number(count)) attention.push(`${count} ${state} alert emails (24 h)`);
  if (status.versions.state === "unavailable") broken.push("Data API version unavailable");
  else if (status.versions.state !== "match") attention.push(`API builds ${status.versions.state}`);
  return { verdict: broken.length ? "broken" as const : attention.length ? "attention" as const : "healthy" as const,
    reasons: [...broken, ...attention] };
}
async function versions(): Promise<PlatformStatus["versions"]> {
  const control_sha = process.env.MDP_BUILD_SHA || null;
  if (!process.env.MDP_DATA_API_URL) return { control_sha, data_sha: null, state: "unknown" };
  try {
    const response = await fetch(new URL("/version", process.env.MDP_DATA_API_URL), { signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error("Version unavailable");
    const { git_sha: data_sha } = versionDto.parse(await response.json());
    return { control_sha, data_sha, state: !control_sha || !data_sha ? "unknown" : control_sha === data_sha ? "match" : "mismatch" };
  } catch { return { control_sha, data_sha: null, state: "unavailable" }; }
}
export async function cadenceStatus(db: DB, checked_at: string) {
  return rows(db, cadenceStatusDto, `WITH pairs AS (
      SELECT cadence, 'global'::text AS scope FROM (VALUES ('hourly'),('daily'),('weekly')) v(cadence)
      UNION SELECT cadence,scope FROM control.dbt_job WHERE runner=(SELECT runner FROM control.runner_mode WHERE id)
      UNION SELECT cadence,scope FROM control.cycle WHERE ${scheduledCycleSql()}
    ), active AS (
      SELECT p.*, CASE cadence WHEN 'hourly' THEN 3600 WHEN 'daily' THEN 86400 ELSE 604800 END AS interval_seconds
      FROM pairs p WHERE cadence IN ('hourly','daily','weekly') AND
        (scope='global' OR EXISTS (SELECT 1 FROM control.tenant t WHERE scope='tenant:'||t.id::text AND t.status='active'))
    ), cycles AS (
      SELECT c.*, jsonb_build_object('id',id,'close_no',close_no::text,'closed_at',closed_at,'opened_at',opened_at,
        'age_seconds',greatest(0,extract(epoch FROM ($1::timestamptz-coalesce(closed_at,opened_at)))),
        'git_sha',git_sha,'image_digest',image_digest) AS detail FROM control.cycle c WHERE status IN ('open','closed') AND ${scheduledCycleSql('c.opened_by_dbt_run_id')}
    ) SELECT p.*, coalesce(extract(epoch FROM ($1::timestamptz-last.closed_at)) > interval_seconds,false) AS overdue,
      last.detail AS last_closed, coalesce(open.details,'[]'::jsonb) AS open
    FROM active p LEFT JOIN LATERAL (
      SELECT detail,closed_at FROM cycles c WHERE c.cadence=p.cadence AND c.scope=p.scope AND status='closed'
      ORDER BY closed_at DESC NULLS LAST,close_no DESC NULLS LAST,id LIMIT 1
    ) last ON true LEFT JOIN LATERAL (
      SELECT jsonb_agg(detail ORDER BY opened_at,id) AS details FROM cycles c
      WHERE c.cadence=p.cadence AND c.scope=p.scope AND status='open'
    ) open ON true ORDER BY CASE p.cadence WHEN 'hourly' THEN 1 WHEN 'daily' THEN 2 ELSE 3 END,p.scope`, [checked_at]);
}
export async function healthSummary(db: DB) {
  const checked_at = new Date().toISOString();
  const cadences = await cadenceStatus(db, checked_at);
  const overdue = [...new Set(cadences.filter(row =>
    !row.last_closed || row.overdue || row.open.some(cycle => cycle.age_seconds > row.interval_seconds),
  ).map(row => row.cadence))];
  return healthSummaryDto.parse({
    checked_at,
    ok: overdue.length === 0,
    overdue,
    next_step: "Open /ops and follow the runner recovery guide.",
  });
}
export async function platformStatus(db: DB): Promise<PlatformStatus> {
  const checked_at = new Date().toISOString();
  const [cadences, alerts, sends, builds, heartbeats] = await Promise.all([
    cadenceStatus(db, checked_at),
    rows(db, alertGroupDto, `SELECT a.class,a.severity,count(*)::text AS count,
      coalesce(jsonb_agg(DISTINCT '/runbooks/'||r.slug) FILTER (WHERE r.slug IS NOT NULL),'[]'::jsonb) AS runbook_urls,
      bool_or(r.slug IS NULL) AS no_guide FROM control.alert a LEFT JOIN control.runbook r
      ON r.slug=coalesce(a.runbook_slug,replace(a.class,'_','-'))
      WHERE a.resolved_at IS NULL AND a.acknowledged_by IS NULL GROUP BY a.class,a.severity ORDER BY a.severity DESC,a.class`),
    // Failures are attempts (so retry storms remain visible); pending is distinct eligible alerts
    // opened or touched in the window, with no delivery or terminal give-up at any time.
    one(db, sendsStatusDto, `SELECT
      (SELECT count(*)::text FROM control.audit_log WHERE action='email.failed' AND at >= $1::timestamptz-interval '24 hours') AS failed,
      (SELECT count(*)::text FROM control.audit_log WHERE action='email.skipped' AND at >= $1::timestamptz-interval '24 hours') AS skipped,
      (SELECT count(*)::text FROM control.audit_log WHERE action='email.gave_up' AND at >= $1::timestamptz-interval '24 hours') AS gave_up,
      (SELECT count(*)::text FROM control.alert a WHERE resolved_at IS NULL AND acknowledged_by IS NULL
        AND (severity='critical' OR (severity='warning' AND (class=ANY($2::text[]) OR class LIKE '%\\_unavailable')))
        AND (opened_at >= $1::timestamptz-interval '24 hours' OR EXISTS
          (SELECT 1 FROM control.audit_log l WHERE l.subject=a.id::text AND l.action LIKE 'email.%' AND l.at >= $1::timestamptz-interval '24 hours'))
        AND NOT EXISTS (SELECT 1 FROM control.audit_log l WHERE l.subject=a.id::text AND l.action IN ('email.sent','email.gave_up'))) AS pending`, [checked_at, emailClasses]),
    versions(),
    rows(db, heartbeatStatusDto, `SELECT action,at,
      greatest(0,extract(epoch FROM ($1::timestamptz-at)))::double precision AS age_seconds,
      "after"->>'cadence' AS cadence,("after"->>'configured')::boolean AS configured
      FROM control.audit_log WHERE action IN ('heartbeat.sent','heartbeat.skipped','heartbeat.failed')
      ORDER BY at DESC,id DESC LIMIT 1`, [checked_at]),
  ]);
  const signals = { cadences, alerts, sends, versions: builds };
  return { checked_at, heartbeat: heartbeats[0] ?? null, retry_launcher: coreLauncherStatus(), ...signals, ...verdictFor(signals), rules: statusRules };
}
