import type { Sql, TransactionSql } from "postgres";
import { z } from "zod";
import type { Session } from "@mdp/showcase-auth";
import {
  callWeek,
  savedCall,
  CALLS_PER_WEEK,
  CALL_UNDO_MS,
  type SignedCard,
} from "../lib/calls";
import { verifyCall } from "./call-token";

// ISO 8601 in UTC, so every browser parses the times the same way.
export const iso = (column: string) =>
  `to_char(${column} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS ${column}`;
export const callColumns = `id, author, song_key, week_start::text, ${iso("submitted_at")}, ${iso("undone_at")}, ${iso("hidden_at")}, facts`;
export type CallRefusal =
  | "call_snapshot_expired"
  | "call_limit"
  | "call_undo_expired"
  | "call_not_found"
  | "draft_closed"
  | "draft_not_open"
  | "rule_not_found";
export class CallError extends Error {
  constructor(public code: CallRefusal) {
    super(code);
  }
}
export async function lock(tx: TransactionSql, key: string) {
  await tx`SELECT pg_advisory_xact_lock(hashtextextended(${key}, 0))`;
}
async function audit(
  tx: TransactionSql,
  current: Session,
  action: string,
  subject: string,
) {
  await tx`INSERT INTO control.audit_log (actor, action, subject) VALUES (${`api-key:${current.person.api_key_id}`}, ${action}, ${subject})`;
}
export async function submitCall(
  db: Sql,
  current: Session,
  signed: SignedCard,
) {
  return db.begin(async (tx) => {
    // Read only the week to choose a lock. Authenticity is checked before any write.
    const draftWeek = z
      .object({ draft_week: z.iso.date().optional() })
      .parse(JSON.parse(signed.body)).draft_week;
    if (draftWeek) await lock(tx, `showcase:draft:${draftWeek}`);
    await lock(
      tx,
      `showcase:callkey:${current.handle}:${signed.idempotency_key}`,
    );
    const existing = await tx.unsafe(
      `SELECT ${callColumns} FROM control.showcase_call WHERE author=$1 AND idempotency_key=$2`,
      [current.handle, signed.idempotency_key],
    );
    if (existing[0]) return savedCall.parse(existing[0]);
    const facts = verifyCall(signed, current.handle);
    if (!facts || current.handle === "rules")
      throw new CallError("call_snapshot_expired");
    const [clock] =
      await tx`SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS at`;
    const week = callWeek(new Date(clock.at));
    if (draftWeek) {
      const [open] = await tx`SELECT closed_at, opens_at<=now() AS started,
        EXISTS (SELECT 1 FROM jsonb_array_elements(candidates) candidate WHERE candidate->>'song_key'=${facts.song_key}) AS in_tray
        FROM control.showcase_draft WHERE week_start=${draftWeek}`;
      if (!open || !open.started) throw new CallError("draft_not_open");
      if (open.closed_at || draftWeek !== week)
        throw new CallError("draft_closed");
      if (!open.in_tray) throw new CallError("call_snapshot_expired");
    }
    await lock(tx, `showcase:call:${current.handle}:${week}`);
    const duplicate = await tx.unsafe(
      `SELECT ${callColumns} FROM control.showcase_call c WHERE author=$1 AND week_start=$2 AND undone_at IS NULL
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(c.anchors->'items') a
      JOIN jsonb_array_elements($3::text::jsonb) b ON a->>'platform'=b->>'platform' AND a->>'platform_track_id'=b->>'platform_track_id') LIMIT 1`,
      [current.handle, week, JSON.stringify(facts.anchors)],
    );
    if (duplicate[0]) return savedCall.parse(duplicate[0]);
    const [count] =
      await tx`SELECT count(*)::int AS n FROM control.showcase_call WHERE author=${current.handle} AND week_start=${week}`;
    if (count.n >= CALLS_PER_WEEK) throw new CallError("call_limit");
    const rows = await tx.unsafe(
      `INSERT INTO control.showcase_call (author,author_kind,idempotency_key,song_key,anchors,snapshot,facts,facts_day,close_no,week_start,draft_week)
      VALUES ($1,'ear',$2,$3,$4::text::jsonb,$5,$6::text::jsonb,$7,$8,$9,$10) RETURNING ${callColumns}`,
      [
        current.handle,
        facts.idempotency_key,
        facts.song_key,
        JSON.stringify({
          items: facts.anchors,
          anchors_truncated: facts.anchors_truncated,
        }),
        signed.body,
        signed.body,
        facts.facts_day,
        facts.close_no,
        week,
        draftWeek ?? null,
      ],
    );
    const call = savedCall.parse(rows[0]);
    await audit(tx, current, "showcase.call", `call:${call.id}`);
    return call;
  });
}
export async function changeCall(
  db: Sql,
  current: Session,
  id: string,
  action: "undo" | "hide",
) {
  return db.begin(async (tx) => {
    const rows = await tx.unsafe(
      `SELECT ${callColumns} FROM control.showcase_call WHERE id=$1 AND author=$2`,
      [id, current.handle],
    );
    if (!rows[0]) throw new CallError("call_not_found");
    const call = savedCall.parse(rows[0]);
    if (call.facts.draft_week)
      await lock(tx, `showcase:draft:${call.facts.draft_week}`);
    await lock(tx, `showcase:call:${current.handle}:${call.week_start}`);
    const changed =
      action === "undo"
        ? await tx`UPDATE control.showcase_call SET undone_at=now() WHERE id=${id} AND author=${current.handle} AND undone_at IS NULL AND now()-submitted_at <= ${CALL_UNDO_MS} * interval '1 millisecond' RETURNING id`
        : await tx`UPDATE control.showcase_call SET hidden_at=now(),hidden_by=${current.handle} WHERE id=${id} AND author=${current.handle} AND hidden_at IS NULL RETURNING id`;
    if (!changed.length && action === "undo") {
      const [row] =
        await tx`SELECT undone_at FROM control.showcase_call WHERE id=${id}`;
      if (!row.undone_at) throw new CallError("call_undo_expired");
    }
    if (changed.length)
      await audit(tx, current, `showcase.call_${action}`, `call:${id}`);
    return savedCall.parse(
      (
        await tx.unsafe(
          `SELECT ${callColumns} FROM control.showcase_call WHERE id=$1`,
          [id],
        )
      )[0],
    );
  });
}
// Every call of this person and week uses a slot, undone ones too.
export async function callsLeft(db: Sql, handle: string, week: string) {
  const [count] =
    await db`SELECT count(*)::int AS n FROM control.showcase_call WHERE author=${handle} AND week_start=${week}`;
  return Math.max(
    0,
    CALLS_PER_WEEK -
      z
        .number()
        .int()
        .parse(count?.n ?? 0),
  );
}
export async function readCalls(db: Sql, week: string) {
  const rows = await db.unsafe(
    `SELECT ${callColumns} FROM control.showcase_call WHERE week_start=$1 ORDER BY submitted_at DESC,id LIMIT 50`,
    [week],
  );
  return rows.map((row) => savedCall.parse(row));
}
export async function recordCallsView(db: Sql, current: Session, week: string) {
  await db.begin(async (tx) => {
    await lock(tx, `showcase:calls_view:${current.handle}:${week}`);
    await audit(tx, current, "showcase.calls_view", week);
  });
}
