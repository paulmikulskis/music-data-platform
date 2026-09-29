import { z } from "zod";
import { platformEvent } from "@mdp/contracts";
import { AppError, rows, type DB } from "./db.js";
import { scheduledCycleSql } from "./health-policy.generated.js";

const cursorSchema = z.object({
  v: z.literal(1), newest: z.iso.datetime().nullable(),
  page: z.object({ time: z.iso.datetime(), key: z.string().regex(/^(cycle_opened|cycle_closed|run_admitted|run_settled|alert_opened|alert_resolved):[0-9a-f-]{36}$/) }).optional(),
});
type Cursor = z.infer<typeof cursorSchema>;
function decode(after?: string): Cursor {
  if (after === undefined) return { v: 1, newest: null };
  try {
    if (!/^[A-Za-z0-9_-]+$/.test(after)) throw new Error();
    return cursorSchema.parse(JSON.parse(Buffer.from(after, "base64url").toString("utf8")));
  } catch {
    throw new AppError("invalid_cursor", "The event cursor is invalid. Run pnpm --dir control mdp platform events to start again.");
  }
}

// Each family reads its time/key index before the merge. At most six pages reach the final sort.
export function platformEventsSql(mode: "start" | "overlap" | "page") {
  const families = [
    { table: "cycle", kind: "cycle_opened", time: "opened_at", where: "true", cycle: "id", scope: "scope", status: "status::text", run: "NULL::uuid" },
    { table: "cycle", kind: "cycle_closed", time: "closed_at", where: "closed_at IS NOT NULL", cycle: "id", scope: "scope", status: "status::text", run: "NULL::uuid" },
    { table: "run", kind: "run_admitted", time: "created_at", where: "true", cycle: "cycle_id", scope: "scope", status: "status::text", run: "NULL::uuid" },
    { table: "run", kind: "run_settled", time: "updated_at", where: "status IN ('succeeded','partial','failed','superseded')", cycle: "cycle_id", scope: "scope", status: "status::text", run: "NULL::uuid" },
    { table: "alert", kind: "alert_opened", time: "opened_at", where: "true", cycle: "NULL::uuid", scope: "NULL::text", status: "class", run: "run_id" },
    { table: "alert", kind: "alert_resolved", time: "resolved_at", where: "resolved_at IS NOT NULL", cycle: "NULL::uuid", scope: "NULL::text", status: "class", run: "run_id" },
  ];
  const cap = mode === "start" ? "$1" : mode === "overlap" ? "$2" : "$3";
  const candidates = families.map(f => {
    const key = `('${f.kind}:' || id::text) COLLATE "C"`;
    // Text parameters preserve microseconds through the driver's timestamp serializer.
    const window = mode === "start" ? "true" : mode === "overlap"
      ? `${f.time} >= $1::text::timestamptz - interval '120 seconds'`
      : `(${f.time},${key}) > ($1::text::timestamptz,$2::text COLLATE "C")`;
    return `(SELECT '${f.kind}'::text AS kind,id AS subject_id,${f.time} AS event_time,${key} AS key,
      ${f.cycle} AS cycle_id,${f.scope} AS scope,${f.status} AS status,${f.run} AS run_id
      FROM control.${f.table} WHERE ${f.where} AND ${window}
      ORDER BY ${f.time},${key} LIMIT ${cap}::int)`;
  }).join(" UNION ALL ");
  return `WITH candidates AS (${candidates}), page AS (
    SELECT * FROM candidates ORDER BY event_time,key COLLATE "C" LIMIT ${cap}::int
  ) SELECT e.kind,e.subject_id,coalesce(e.cycle_id,r.cycle_id) AS cycle_id,coalesce(e.scope,r.scope) AS scope,e.status,e.key,
    to_char(e.event_time AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS occurred_at,
    CASE WHEN c.id IS NULL THEN NULL ELSE (${scheduledCycleSql('c.opened_by_dbt_run_id')}) END AS scheduled
    FROM page e LEFT JOIN LATERAL (
      SELECT cycle_id,scope FROM control.run WHERE id=e.run_id LIMIT 1
    ) r ON true LEFT JOIN LATERAL (
      SELECT id,opened_by_dbt_run_id FROM control.cycle WHERE id=coalesce(e.cycle_id,r.cycle_id) LIMIT 1
    ) c ON true
    ORDER BY e.event_time,e.key COLLATE "C"`;
}

export async function readPlatformEvents(db: DB, input: { after?: string | undefined; limit: number }) {
  const cursor = decode(input.after);
  const mode = cursor.page ? "page" : cursor.newest ? "overlap" : "start";
  const params = cursor.page ? [cursor.page.time, cursor.page.key, input.limit + 1]
    : cursor.newest ? [cursor.newest, input.limit + 1] : [input.limit + 1];
  const found = await rows(db, platformEvent, platformEventsSql(mode), params);
  const events = found.slice(0, input.limit), last = events.at(-1);
  const has_more = found.length > input.limit;
  // All emitted timestamps have six fractional digits. Keep the high water mark if rows disappear.
  const newest = last && (!cursor.newest || last.occurred_at > cursor.newest) ? last.occurred_at : cursor.newest;
  const next: Cursor = { v: 1, newest, ...(has_more && last ? { page: { time: last.occurred_at, key: last.key } } : {}) };
  const overlap_start = newest === null ? null
    : new Date(Date.parse(newest) - 120000).toISOString().slice(0, 19) + newest.slice(19);
  return { events, overlap_start, next_cursor: Buffer.from(JSON.stringify(next)).toString("base64url"), has_more };
}
