import { z } from "zod";
import {
  cadenceOverdueSql,
  defaultCoverageFloor,
  scheduledCycleSql,
} from "./health-policy.generated.js";
import { errorHint, jsonRow, sourceReaders } from "@mdp/contracts";
import { rows, type DB } from "./db.js";
import { healthState } from "./console-semantics.js";
import type { Coverage, Failure, Health, Labels } from "./console-semantics.js";

// An open cycle can fail without a critical alert. A completed cycle clears this signal.
export async function consoleFailures(db: DB): Promise<Failure[]> {
  const [cycles, alerts, overdue] = await Promise.all([
    rows(
      db,
      jsonRow,
      `SELECT c.id,c.cadence,c.scope,c.opened_at,r.id AS run_id,s.source_key,r.error_class,r.error_message,
      e.attrs->>'model' AS model FROM control.cycle c
      LEFT JOIN LATERAL (SELECT * FROM control.run WHERE cycle_id=c.id AND (status='failed' OR (coverage='partial' AND resolved_config->'target_coverage' IS NULL AND NOT coalesce((SELECT allow_partial FROM control.streamline WHERE id=streamline_id),true))) ORDER BY updated_at DESC LIMIT 1) r ON true
      LEFT JOIN control.streamline s ON s.id=r.streamline_id
      LEFT JOIN LATERAL (SELECT attrs FROM control.run_event WHERE run_id=r.id AND (attrs ? 'model') ORDER BY at DESC LIMIT 1) e ON true
      WHERE c.status='open' AND ${scheduledCycleSql("c.opened_by_dbt_run_id")} AND (r.id IS NOT NULL OR c.opened_at < now()-${cadenceOverdueSql("c.cadence")})
      ORDER BY c.opened_at`,
    ),
    rows(
      db,
      jsonRow,
      `SELECT a.*,coalesce(r.cycle_id,c.id) AS cycle_id,r.error_message,r.error_class,s.source_key,
      cur.id AS current_cycle_id,cur.status::text AS current_status,cur.closed_at AS current_closed_at
      FROM control.alert a LEFT JOIN control.run r ON r.id=a.run_id LEFT JOIN control.streamline s ON s.id=r.streamline_id
      LEFT JOIN control.cycle c ON a.subject_type='cycle' AND c.id::text=a.subject_id
      LEFT JOIN control.cycle own ON own.id=coalesce(r.cycle_id,c.id)
      LEFT JOIN LATERAL (SELECT id,status,closed_at FROM control.cycle WHERE cadence=own.cadence AND scope=own.scope AND status<>'superseded'
        AND ${scheduledCycleSql()} ORDER BY opened_at DESC LIMIT 1) cur ON true
      WHERE a.resolved_at IS NULL AND a.severity='critical' ORDER BY a.opened_at DESC`,
    ),
    rows(
      db,
      jsonRow,
      `SELECT last.* FROM (SELECT DISTINCT ON (cadence,scope) id,cadence,scope,closed_at FROM control.cycle WHERE status='closed' AND ${scheduledCycleSql()} ORDER BY cadence,scope,closed_at DESC) last
      WHERE closed_at < now()-${cadenceOverdueSql("cadence")}
      AND NOT EXISTS (SELECT 1 FROM control.cycle c WHERE c.cadence=last.cadence AND c.scope=last.scope AND c.status='open' AND ${scheduledCycleSql("c.opened_by_dbt_run_id")})
      AND (scope='global' OR EXISTS (SELECT 1 FROM control.tenant t WHERE scope='tenant:'||t.id::text AND t.status='active'))`,
    ),
  ]);
  return [
    ...cycles.map((c) => ({
      source_key: String(c.source_key ?? "platform"),
      error_class: String(c.error_class ?? "cadence_failed"),
      at: String(c.opened_at ?? ""),
      title: `${c.cadence} · ${c.scope} · ${c.run_id ? "failed" : "overdue"} cycle`,
      cycle_id: String(c.id),
      ...(c.run_id ? { run_id: String(c.run_id) } : {}),
      model: String(
        c.model ??
          (c.source_key
            ? `bronze_invoke__${c.source_key}`
            : "Model not reported"),
      ),
      message:
        c.error_class === "partial_not_allowed" ||
        c.error_class === "stale_target"
          ? "Some targets failed. Check the receipt against its coverage floor."
          : String(c.error_message ?? "This cycle is overdue."),
      runbook: "/runbooks/partial-coverage",
    })),
    ...alerts
      .filter((a) => !cycles.some((c) => c.id === a.cycle_id))
      .map((a) => ({
        source_key: String(a.source_key ?? "platform"),
        error_class: String(a.error_class ?? a.class),
        at: String(a.opened_at ?? ""),
        title: String(a.class).replaceAll("_", " "),
        message: String(
          a.error_message ??
            "A critical alert is open. Review its cause before retrying.",
        ),
        ...retryOrNote(a),
        ...(a.run_id ? { run_id: String(a.run_id) } : {}),
        runbook: a.runbook_slug
          ? `/runbooks/${a.runbook_slug}`
          : "/runbooks/dbt-failure",
      })),
    ...overdue.map((c) => ({
      title: `${c.cadence} · ${c.scope} · overdue`,
      cycle_id: String(c.id),
      message: "The next cycle is overdue.",
      retryable: false,
      runbook: "/runbooks/dbt-failure",
    })),
  ];
}

// Retry is offered only while the cycle it would rebuild is open. A closed current cycle has
// nothing left to retry: its next recorded build resolves the alert.
export function retryOrNote(
  a: Record<string, unknown>,
): Pick<Failure, "cycle_id" | "note"> {
  if (!a.cycle_id || !a.current_cycle_id) return {};
  if (a.current_status === "open")
    return { cycle_id: String(a.current_cycle_id) };
  if (a.current_status !== "closed") return {};
  const at =
    a.current_closed_at instanceof Date
      ? a.current_closed_at.toISOString()
      : String(a.current_closed_at ?? "");
  return {
    note: `Cycle ${String(a.current_cycle_id).slice(0, 8)} closed ${at.slice(0, 16).replace("T", " ")} UTC. The next successful build resolves this alert.`,
  };
}

const readerPlatforms = new Map(
  sourceReaders.map((reader) => [reader.source_key, reader.platforms]),
);

// Coverage and the viewer count the same run members. Checkpoints also exclude members
// skipped for cadence or other reader-specific reasons within a declared platform.
function eligibleRunTarget(row: Record<string, unknown>): boolean {
  const completed = Array.isArray(row.completed) ? row.completed : [];
  const platforms = readerPlatforms.get(String(row.source_key));
  return (
    !completed.includes(`skipped:${row.id}`) &&
    (!platforms?.length || platforms.includes(String(row.platform)))
  );
}

// Durable checkpoints count targets, never HTTP attempts or rows. Failed markers subtract from
// completion; a successful retry's checkpoint takes precedence over an old dead letter.
export async function runCoverage(
  db: DB,
  ids: string[],
): Promise<Record<string, Coverage>> {
  if (!ids.length) return {};
  const data = await rows(
    db,
    jsonRow,
    `SELECT b.run_id,t.id,t.handle,t.platform_account_id,t.platform,s.source_key,b.status,
    coalesce(b.cursor_checkpoint->'completed_targets','[]') AS completed,
    coalesce(d.reason,CASE WHEN cardinality(b.target_ids)=1 AND counts.yielded=0 AND counts.rejected>0 THEN 'Rejected: no records passed validation' END) AS reason, (r.resolved_config->'target_coverage'->>'min_target_coverage')::float AS floor
    FROM control.batch b CROSS JOIN LATERAL unnest(b.target_ids) member(id)
    JOIN control.target t ON t.id=member.id JOIN control.run r ON r.id=b.run_id
    LEFT JOIN control.streamline s ON s.id=r.streamline_id
    LEFT JOIN LATERAL (SELECT reason FROM control.dead_letter WHERE run_id=b.run_id AND target_id=t.id ORDER BY first_seen_at DESC LIMIT 1) d ON true
    LEFT JOIN LATERAL (SELECT sum((attrs->>'yielded')::int) AS yielded,sum((attrs->>'rejected')::int-coalesce((SELECT sum(value::int) FROM jsonb_each_text(coalesce(attrs->'exclusions','{}'::jsonb))),0)) AS rejected FROM control.run_event WHERE run_id=b.run_id AND event_type='page_published' AND attrs->>'batch_id'=b.id::text) counts ON true
    WHERE b.run_id=ANY($1::uuid[])`,
    [ids],
  );
  const result: Record<string, Coverage> = {};
  const seen = new Set<string>();
  for (const row of data) {
    if (!eligibleRunTarget(row)) continue;
    const id = String(row.id),
      run = String(row.run_id);
    if (seen.has(`${run}:${id}`)) continue;
    seen.add(`${run}:${id}`);
    const completed = Array.isArray(row.completed) ? row.completed : [];
    const c = (result[run] ??= {
      succeeded: 0,
      total: 0,
      failures: [],
      ...(typeof row.floor === "number" ? { floor: row.floor } : {}),
    });
    c.total++;
    const failed =
      completed.includes(`stale_target:${id}`) ||
      completed.includes(`rejected:${id}`);
    if (
      completed.includes(id) &&
      !failed &&
      !String(row.reason ?? "").startsWith("Rejected:")
    )
      c.succeeded++;
    else if (failed || row.reason || row.status === "failed")
      c.failures.push({
        id,
        name: String(row.handle ?? row.platform_account_id ?? id),
        reason: String(
          row.reason ?? (failed ? "Rejected target" : "Target did not finish"),
        ),
      });
  }
  for (const coverage of Object.values(result))
    coverage.floor ??= defaultCoverageFloor(coverage.total);
  return result;
}
export async function targetHealth(
  db: DB,
  ids: string[],
  runId?: string,
): Promise<Record<string, Health>> {
  if (!ids.length) return {};
  const data = await rows(
    db,
    jsonRow,
    `WITH calls AS MATERIALIZED (
      SELECT target_id,run_id,http_status,created_at,id,block_signature FROM control.call_ledger WHERE target_id=ANY($1::uuid[]) AND ($2::uuid IS NULL OR run_id=$2)
    ), latest AS (
      SELECT DISTINCT ON (target_id) * FROM calls ORDER BY target_id,created_at DESC,id
    ), recovered AS (
      SELECT target_id,max(created_at) AS at FROM calls WHERE http_status NOT IN (404,410) GROUP BY target_id
    ), missing AS (
      SELECT c.target_id,count(DISTINCT run_id)::int AS n FROM calls c LEFT JOIN recovered r USING(target_id)
      WHERE http_status IN (404,410) AND created_at>coalesce(r.at,'epoch') GROUP BY c.target_id
    ), errors AS (
      SELECT DISTINCT ON (target_id) target_id,reason,first_seen_at FROM control.dead_letter
      WHERE target_id=ANY($1::uuid[]) AND ($2::uuid IS NULL OR run_id=$2) ORDER BY target_id,first_seen_at DESC
    ) SELECT t.id,t.handle,t.platform_account_id,t.activated_at,t.deactivated_at,t.resolution_status,
      CASE WHEN t.deactivated_at IS NOT NULL AND EXISTS (SELECT 1 FROM control.audit_log a WHERE a.action='targets.parkStale' AND a.subject=t.id::text AND a.at >= t.activated_at) THEN t.deactivated_at END AS parked_at,c.http_status,greatest(c.created_at,d.first_seen_at) AS at,
      CASE WHEN d.first_seen_at>coalesce(c.created_at,'epoch') THEN d.reason
        WHEN c.http_status=304 AND c.block_signature IS NULL THEN 'unchanged'
        WHEN c.http_status BETWEEN 200 AND 299 AND c.block_signature IS NULL THEN 'succeeded'
        ELSE 'HTTP '||coalesce(c.http_status::text,'unknown') END AS result,
      coalesce(m.n,0) AS missing_streak,
      (SELECT a.run_id::text FROM control.alert a WHERE a.subject_type='target' AND a.subject_id=t.id::text
        AND a.class='target_zero_yield' AND a.resolved_at IS NULL AND ($2::uuid IS NULL OR a.run_id=$2) ORDER BY a.opened_at DESC LIMIT 1) AS zero_yield_run
    FROM control.target t LEFT JOIN latest c ON c.target_id=t.id LEFT JOIN errors d ON d.target_id=t.id
    LEFT JOIN missing m ON m.target_id=t.id WHERE t.id=ANY($1::uuid[])`,
    [ids, runId ?? null],
  );
  return Object.fromEntries(
    data.map((r) => [
      String(r.id),
      {
        id: String(r.id),
        name: String(r.handle ?? r.platform_account_id ?? r.id),
        active: !!r.activated_at && !r.deactivated_at,
        resolved: r.resolution_status === "resolved",
        parked: !!r.parked_at,
        ...(r.at ? { at: String(r.at), result: String(r.result) } : {}),
        ...(typeof r.http_status === "number"
          ? { http_status: r.http_status }
          : {}),
        missing_streak: Number(r.missing_streak),
        ...(r.zero_yield_run
          ? { zero_yield_run: String(r.zero_yield_run) }
          : {}),
      },
    ]),
  );
}
export async function sourceLabels(db: DB, key: string): Promise<Labels> {
  const [row] = await rows(
    db,
    jsonRow,
    "SELECT category,rights_status,learning_eligible,resale_permitted FROM control.rights_source WHERE source_key=$1",
    [key],
  );
  if (!row) return {};
  return z
    .object({
      category: z.string(),
      rights_status: z.string(),
      learning_eligible: z.boolean(),
      resale_permitted: z.boolean(),
    })
    .parse(row);
}

export type TargetState =
  "read" | "unchanged" | "failed" | "gone" | "parked" | "not-read";
export type FunctionHealth = {
  counts: Record<TargetState, number>;
  read: number;
  total: number;
  same_platform: number;
  platform: string;
  set_name: string;
  rows: {
    id: string;
    name: string;
    platform: string;
    state: TargetState;
    at?: string;
    reason: string;
  }[];
};
export async function functionTargetHealth(
  db: DB,
  streamlineId: string,
  runId?: string,
): Promise<FunctionHealth> {
  const candidates = await rows(
    db,
    jsonRow,
    `WITH latest AS (
    SELECT id FROM control.run WHERE streamline_id=$1 AND status<>'superseded'
      AND ($2::uuid IS NULL OR id=$2) ORDER BY created_at DESC LIMIT 1
  ), population AS (
    SELECT b.run_id,unnest(b.target_ids) AS id,
      coalesce(b.cursor_checkpoint->'completed_targets','[]') AS completed
    FROM control.batch b JOIN latest r ON r.id=b.run_id
  ) SELECT t.id,p.run_id,p.completed,s.source_key,t.platform,coalesce(t.display_name,t.handle,t.platform_account_id,t.id::text) AS name,
    ts.name AS set_name,(SELECT count(*) FROM control.target other
      WHERE other.target_set_id=t.target_set_id AND other.platform=t.platform)::int AS same_platform
    FROM population p JOIN control.run r ON r.id=p.run_id
    JOIN control.streamline s ON s.id=r.streamline_id
    JOIN control.target t ON t.id=p.id JOIN control.target_set ts ON ts.id=t.target_set_id`,
    [streamlineId, runId ?? null],
  );
  const members = [
    ...new Map(
      candidates.filter(eligibleRunTarget).map((t) => [String(t.id), t]),
    ).values(),
  ];
  const health = await targetHealth(
    db,
    members.map((t) => String(t.id)),
    members[0] ? String(members[0].run_id) : undefined,
  );
  const counts: FunctionHealth["counts"] = {
    read: 0,
    unchanged: 0,
    failed: 0,
    gone: 0,
    parked: 0,
    "not-read": 0,
  };
  const stateNames: Record<ReturnType<typeof healthState>, TargetState> = {
    healthy: "read",
    unchanged: "unchanged",
    stale: "failed",
    dead: "gone",
    parked: "parked",
    "not read yet": "not-read",
  };
  const result = members.map((t) => {
    const h = health[String(t.id)];
    const state = h?.at ? stateNames[healthState(h)] : "not-read";
    counts[state]++;
    return {
      id: String(t.id),
      name: String(t.name),
      platform: String(t.platform),
      state,
      ...(h?.at ? { at: h.at } : {}),
      reason: h?.result ?? "No request recorded",
    };
  });
  const order: Record<TargetState, number> = {
    failed: 0,
    gone: 1,
    parked: 2,
    "not-read": 3,
    unchanged: 4,
    read: 5,
  };
  result.sort(
    (a, b) => order[a.state] - order[b.state] || a.name.localeCompare(b.name),
  );
  return {
    counts,
    read: counts.read + counts.unchanged,
    total: result.length,
    rows: result,
    same_platform: Number(members[0]?.same_platform ?? 0),
    platform: String(members[0]?.platform ?? ""),
    set_name: String(members[0]?.set_name ?? "target set"),
  };
}
export function rejectedByReason(records: Record<string, unknown>[]) {
  const groups = new Map<
    string,
    { code: string; count: number; sample: Record<string, unknown> }
  >();
  for (const record of records) {
    const code = String(record.reason ?? "validation_error").split(":")[0]!;
    const group = groups.get(code);
    if (group) group.count++;
    else groups.set(code, { code, count: 1, sample: record });
  }
  return [...groups.values()];
}
export function groupFailures(failures: Failure[]) {
  const groups = new Map<
    string,
    {
      source_key: string;
      error_class: string;
      title: string;
      message: string;
      summary: string;
      next_step: string;
      runbook: string;
      count: number;
      run_ids: string[];
      cycle_ids: string[];
      first_at: string;
      last_at: string;
      failures: Failure[];
    }
  >();
  for (const failure of failures) {
    const match = /(?:plpy\.Error: )?([a-z_]+): (.+?)(?: CONTEXT:|$)/.exec(
      failure.message,
    );
    const code = match?.[1] ?? failure.error_class ?? "cadence_failed";
    const source =
      failure.source_key ??
      failure.model?.replace(
        /^(?:bronze|silver|gold|universal)_invoke__/,
        "",
      ) ??
      "platform";
    const key = `${source}:${code}`;
    const hint = errorHint(code);
    let group = groups.get(key);
    if (!group) {
      const label = code.replaceAll("_", " ");
      group = {
        source_key: source,
        error_class: code,
        title: `${source} · ${label[0]!.toUpperCase()}${label.slice(1)}`,
        message: failure.message,
        summary: hint.summary,
        next_step: hint.next_step,
        runbook: `/runbooks/${hint.runbook ?? "cadence-failed"}`,
        count: 0,
        run_ids: [],
        cycle_ids: [],
        first_at: failure.at ?? "",
        last_at: failure.at ?? "",
        failures: [],
      };
      groups.set(key, group);
    }
    group.count++;
    group.failures.push(failure);
    if (failure.run_id && !group.run_ids.includes(failure.run_id))
      group.run_ids.push(failure.run_id);
    if (failure.cycle_id && !group.cycle_ids.includes(failure.cycle_id))
      group.cycle_ids.push(failure.cycle_id);
    if ((failure.at ?? "") < group.first_at) group.first_at = failure.at ?? "";
    if ((failure.at ?? "") >= group.last_at) {
      group.last_at = failure.at ?? "";
      group.message = failure.message;
    }
  }
  return [...groups.values()].sort((a, b) =>
    b.last_at.localeCompare(a.last_at),
  );
}
