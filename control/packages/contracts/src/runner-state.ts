import { z } from "zod";
import { encodeWire } from "@mdp/data-sdk";
import { runnerState } from "./platform.js";
import { scheduledCycleSql } from "./health-policy.generated.js";

export type RunnerDatabase = {
  unsafe(query: string, parameters?: string[]): PromiseLike<unknown>;
};
async function readRows<T extends z.ZodType>(
  db: RunnerDatabase,
  schema: T,
  query: string,
  parameters: string[] = [],
) {
  return z.array(schema).parse(encodeWire(await db.unsafe(query, parameters)));
}
async function readOne<T extends z.ZodType>(
  db: RunnerDatabase,
  schema: T,
  query: string,
  parameters: string[] = [],
) {
  return schema.parse((await readRows(db, schema, query, parameters))[0]);
}

export async function readRunnerState(
  db: RunnerDatabase,
): Promise<z.infer<typeof runnerState>> {
  const unknown = {
    state: "unknown" as const,
    busy: true,
    sessions: [],
    next_scheduled_at: null,
    next_step: "Use cached reads. Open /ops to check the runner.",
  };
  try {
    const mode = await readOne(
      db,
      z.object({ runner: z.string() }),
      "SELECT runner FROM control.runner_mode WHERE id",
    );
    if (mode.runner !== "core") return unknown;
    // All Core holders use control_rt. Its own activity is visible without pg_read_all_stats.
    const activity = await readRows(
      db,
      z.object({
        application_name: z.string(),
        usename: z.string().nullable(),
        holds_lock: z.boolean(),
      }),
      // Control workers also hold advisory locks (for example target-probes every minute).
      // Only Core's keys indicate an unnamed runner, including the gap before it sets its name.
      `WITH runner_locks AS (
        SELECT hashtext('core:' || cadence || ':' || scope)::bigint AS key
        FROM control.dbt_job WHERE runner='core'
        UNION
        SELECT hashtext('core:' || cadence || ':global')::bigint
        FROM unnest(ARRAY['hourly','daily','weekly']) AS cadence
      )
      SELECT a.application_name,a.usename,EXISTS (
        SELECT 1 FROM pg_locks l JOIN runner_locks k
          ON l.classid=((k.key >> 32) & 4294967295)::oid
          AND l.objid=(k.key & 4294967295)::oid AND l.objsubid=1
        WHERE l.pid=a.pid AND l.locktype='advisory' AND l.granted
      ) AS holds_lock
      FROM pg_stat_activity a
      WHERE a.datname=current_database()
        AND (a.application_name LIKE 'mdp-runner:%' OR a.usename='control_rt')`,
    );
    const sessions: z.infer<typeof runnerState>["sessions"] = [];
    for (const row of activity) {
      if (!row.application_name.startsWith("mdp-runner:")) {
        if (row.holds_lock) return unknown;
        continue;
      }
      const parts = /^mdp-runner:(-?\d+):(scheduled|other):(\d+)$/.exec(
        row.application_name,
      );
      if (!parts || row.usename !== "control_rt") return unknown;
      const stamp = Number(parts[3]) * 1000;
      if (!Number.isFinite(stamp) || stamp <= 0 || stamp > Date.now() + 60000)
        return unknown;
      sessions.push({
        lock: parts[1]!,
        kind: z.enum(["scheduled", "other"]).parse(parts[2]),
        started_at: new Date(stamp).toISOString(),
      });
    }
    return {
      state: sessions.length ? "busy" : "idle",
      busy: sessions.length > 0,
      sessions,
      next_scheduled_at: await readNextScheduledRun(db),
      next_step: sessions.length
        ? "Use cached reads. Open /ops to follow the run."
        : "Refresh the view. Open /ops for the next scheduled run.",
    };
  } catch {
    return unknown;
  }
}

// The same due hours, local periods and 45-minute hourly credit as Core's due gate.
// Fly ticks are fuzzy; this is eligibility, not a promise of an exact machine start.
export const nextScheduledSql = `WITH jobs AS (
 SELECT j.*, $1::timestamptz AT TIME ZONE j.timezone AS local_now,
 CASE WHEN cadence='daily' THEN date_trunc('day',$1::timestamptz AT TIME ZONE j.timezone)
      ELSE date_trunc('week',$1::timestamptz AT TIME ZONE j.timezone) END AS period_start
 FROM control.dbt_job j WHERE j.runner='core' AND scope='global' AND cadence IN ('hourly','daily','weekly')
), due AS (
 SELECT j.*, c.last_closed, c.failures, c.last_failed,
 (period_start + make_interval(hours=>coalesce(due_hour,CASE WHEN cadence='daily' THEN 2 ELSE 3 END),
 days=>CASE WHEN cadence='weekly' THEN coalesce(due_weekday,1)-1 ELSE 0 END)) AT TIME ZONE j.timezone AS period_due
 FROM jobs j LEFT JOIN LATERAL (
 SELECT max(opened_at) FILTER (WHERE status='closed') AS last_closed,
 count(*) FILTER (WHERE status<>'closed') AS failures, (array_agg(opened_at ORDER BY opened_at DESC) FILTER (WHERE status<>'closed'))[2] AS last_failed
 FROM control.cycle c WHERE c.scope=j.scope AND c.cadence=j.cadence AND ${scheduledCycleSql("c.opened_by_dbt_run_id")}
 AND c.opened_at <= $1::timestamptz AND c.opened_at >= CASE WHEN j.cadence='hourly' THEN $1::timestamptz-interval '45 minutes' ELSE j.period_start AT TIME ZONE j.timezone END
 ) c ON true
) SELECT min(CASE WHEN cadence='hourly' THEN greatest($1::timestamptz,last_closed+interval '45 minutes',CASE WHEN failures>=2 THEN last_failed+interval '45 minutes' END)
 WHEN last_closed IS NOT NULL OR failures>=2 THEN (period_start + CASE WHEN cadence='daily' THEN interval '1 day' ELSE interval '1 week' END
 + make_interval(hours=>coalesce(due_hour,CASE WHEN cadence='daily' THEN 2 ELSE 3 END),days=>CASE WHEN cadence='weekly' THEN coalesce(due_weekday,1)-1 ELSE 0 END)) AT TIME ZONE timezone
 ELSE greatest($1::timestamptz,period_due) END) AS next_scheduled_at FROM due`;
export async function readNextScheduledRun(
  db: RunnerDatabase,
  now = new Date(),
) {
  return (
    await readOne(
      db,
      z.object({ next_scheduled_at: z.iso.datetime().nullable() }),
      nextScheduledSql,
      [now.toISOString()],
    )
  ).next_scheduled_at;
}
