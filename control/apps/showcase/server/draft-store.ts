import { randomUUID } from "node:crypto";
import type { Sql, TransactionSql } from "postgres";
import { z } from "zod";
import type { Session } from "@mdp/showcase-auth";
import inventory from "../../../../ops/showcase/queries.json";
import {
  draft,
  draftRule,
  draftCandidate,
  draftWeek,
  draftReadDay,
  draftWindowPassed,
  exampleRules,
  type DraftCandidate,
} from "../lib/draft";
import { callSnapshot, CALL_TIME_ZONE } from "../lib/calls";
import { lock, CallError, iso } from "./call-store";

const columns = `week_start::text,${iso("opens_at")},${iso("closes_at")},candidates,rules,${iso("closed_at")},closed_by,close_key`;
export async function readDraft(db: Sql | TransactionSql, week: string) {
  const rows = await db.unsafe(
    `SELECT ${columns} FROM control.showcase_draft WHERE week_start=$1`,
    [draftWeek.parse(week)],
  );
  return rows[0] ? draft.parse(rows[0]) : null;
}
export async function readRules(db: Sql | TransactionSql) {
  return z
    .array(draftRule)
    .parse(
      await db`SELECT id,title,conditions,limit_per_draft,backed_by FROM control.showcase_rule WHERE retired_at IS NULL ORDER BY id`,
    );
}
export async function freezeDraft(
  db: Sql,
  week: string,
  candidates: DraftCandidate[],
  now = () => new Date(),
) {
  draftWeek.parse(week);
  const frozen = z.array(draftCandidate).parse(candidates);
  return db.begin(async (tx) => {
    await lock(tx, `showcase:draft:${week}`);
    const existing = await readDraft(tx, week);
    if (existing) return existing;
    if (draftWindowPassed(week, now())) return null;
    for (const rule of exampleRules) {
      draftRule.parse(rule);
      await tx`INSERT INTO control.showcase_rule(id,title,conditions,limit_per_draft,created_by)
        VALUES (${rule.id},${rule.title},${JSON.stringify(rule.conditions)}::text::jsonb,${rule.limit_per_draft},'example') ON CONFLICT DO NOTHING`;
    }
    await tx`INSERT INTO control.showcase_draft(week_start,opens_at,closes_at,candidates,rules)
      VALUES (${week},now(),(${draftReadDay(week)}::date + time '18:00') AT TIME ZONE ${CALL_TIME_ZONE},${JSON.stringify(frozen)}::text::jsonb,'[]')`;
    await tx`INSERT INTO control.audit_log(actor,action,subject) VALUES ('scheduler','showcase.draft_open',${week})`;
    return (await readDraft(tx, week))!;
  });
}
export async function backRule(
  db: Sql,
  current: Session,
  week: string,
  id: string,
) {
  draftWeek.parse(week);
  return db.begin(async (tx) => {
    await lock(tx, `showcase:draft:${week}`);
    const open = await readDraft(tx, week);
    if (!open) throw new CallError("draft_not_open");
    if (open.closed_at) throw new CallError("draft_closed");
    await lock(tx, `showcase:call:${current.handle}:${week}`);
    const rule = (await readRules(tx)).find((r) => r.id === id);
    if (!rule) throw new CallError("rule_not_found");
    draftRule.parse(rule); // Stored JSON is checked again at the write boundary.
    if (!rule.backed_by) {
      await tx`UPDATE control.showcase_rule SET backed_by=${current.handle},backed_at=now() WHERE id=${id}`;
      await tx`INSERT INTO control.audit_log(actor,action,subject) VALUES (${`api-key:${current.person.api_key_id}`},'showcase.rule_back',${id})`;
    }
  });
}
const pickQuery = z
  .object({ id: z.literal("rule_picks"), sql: z.string() })
  .parse(inventory.find((q) => "id" in q && q.id === "rule_picks")).sql;
export async function closeDraft(
  db: Sql,
  week: string,
  closeKey: string,
  actor: string,
) {
  draftWeek.parse(week);
  z.string().min(1).max(128).parse(closeKey);
  return db.begin(async (tx) => {
    await lock(tx, `showcase:draft:${week}`);
    const current = await readDraft(tx, week);
    if (!current) throw new CallError("draft_not_open");
    // Both same-key retries and a second closer return the committed result.
    if (current.closed_at) return current;
    const rules = (await readRules(tx)).filter((r) => r.backed_by !== null);
    const candidates = z.array(draftCandidate).parse(current.candidates);
    z.array(draftRule).parse(rules); // Validate persisted conditions before evaluation.
    await lock(tx, `showcase:call:rules:${week}`);
    const picks = z
      .array(z.object({ song_key: z.string(), rule_ids: z.array(z.string()) }))
      .parse(
        await tx.unsafe(pickQuery, [
          JSON.stringify(candidates),
          JSON.stringify(rules),
          week,
        ]),
      );
    for (const pick of picks) {
      const candidate = candidates.find((c) => c.song_key === pick.song_key)!;
      const key = randomUUID();
      const facts = callSnapshot.parse({
        ...candidate.snapshot,
        handle: "rules",
        draft_week: week,
        idempotency_key: key,
      });
      const snapshot = JSON.stringify(facts);
      const [call] =
        await tx`INSERT INTO control.showcase_call(author,author_kind,idempotency_key,song_key,anchors,snapshot,facts,facts_day,close_no,week_start,draft_week)
        VALUES ('rules','rule',${key},${pick.song_key},${JSON.stringify({ items: facts.anchors, anchors_truncated: facts.anchors_truncated })}::text::jsonb,${snapshot},${snapshot}::text::jsonb,${facts.facts_day},${facts.close_no},${week},${week}) RETURNING id`;
      for (const id of pick.rule_ids)
        await tx`INSERT INTO control.showcase_call_rule(call_id,rule_id) VALUES (${call.id},${id})`;
      await tx`INSERT INTO control.audit_log(actor,action,subject) VALUES (${actor},'showcase.call',${`call:${call.id}`})`;
    }
    await tx`UPDATE control.showcase_draft SET rules=${JSON.stringify(rules)}::text::jsonb,closed_at=clock_timestamp(),closed_by=${actor},close_key=${closeKey} WHERE week_start=${week}`;
    await tx`INSERT INTO control.audit_log(actor,action,subject) VALUES (${actor},'showcase.draft_close',${week})`;
    return (await readDraft(tx, week))!;
  });
}

export async function draftCloser(db: Sql, actor: string | null) {
  if (!actor || actor === "scheduler") return null;
  const [row] = await db`SELECT display_name FROM control.showcase_actor
    WHERE 'api-key:' || api_key_id::text = ${actor}`;
  return row ? z.string().parse(row.display_name) : "a viewer";
}
