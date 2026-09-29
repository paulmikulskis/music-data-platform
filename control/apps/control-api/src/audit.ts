import { AsyncLocalStorage } from "node:async_hooks";
import { z } from "zod";
import { jsonRow } from "@mdp/contracts";
import { database, rows, type DB } from "./db.js";

export function sanitize(value: unknown, depth = 0): unknown {
  if (Array.isArray(value)) return value.map(item => sanitize(item, depth + 1));
  if (value !== null && typeof value === "object")
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      /secret|token|password|authorization|api.?key|csv|body|prompt|sql|^link_url$/i.test(key)
        ? "[REDACTED]" : depth > 0 && key === "key" ? "[REDACTED]" : sanitize(item, depth + 1)]));
  if (typeof value === "string") return value.replace(/(\w+:\/\/)[^/\s:@]+:[^/\s@]+@/g, "$1[REDACTED]@");
  return value;
}
export async function auditSubject(db: DB, action: string, value: unknown) {
  const input = z.record(z.string(), z.unknown()).parse(value ?? {});
  const group = action.split(".")[0];
  const table = ({ tenants: "tenant", targets: "target", streamlines: "streamline",
    runs: "run", budgets: "budget", alerts: "alert", functions: "streamline", dbt: "dbt_job" })[group ?? ""];
  let subject_type = table ?? group ?? "operation";
  let subject_id = String(input.id ?? input.dump_id ?? input.source_key ?? input.job_id ?? input.target_set_id ?? input.person ?? "new");
  let query = "";
  let params: (string | string[])[] = [];
  if (action === "targets.undoImport") {
    const original = await rows(db, jsonRow, "SELECT after FROM control.audit_log WHERE id=$1 AND action='targets.importTargets'", [String(input.audit_id)]);
    const record = z.object({after:z.object({result:z.object({rows:z.array(z.object({id:z.string()}))})})}).safeParse(original[0]);
    const ids = record.success ? record.data.after.result.rows.map(r=>r.id) : [];
    const before = ids.length ? await rows(db,jsonRow,"SELECT * FROM control.target WHERE id=ANY($1::uuid[]) ORDER BY id",[ids]) : [];
    return {subject_type:"target",subject_id:ids.join(",") || String(input.audit_id),before:sanitize(before)};
  }
 else if (action === "functions.register") {
    subject_type = "streamline"; subject_id = "registry";
    query = "SELECT * FROM control.streamline ORDER BY source_key";
  } else if (action === "dbt.webhook") {
    subject_type = "run"; subject_id = `dbt:${String(input.run_id)}`;
    query = "SELECT * FROM control.run WHERE work_key=$1"; params = [subject_id];
  } else if (group === "reference") {
    subject_type = "reference_source"; subject_id = String(input.source ?? "new");
    query = "SELECT * FROM control.reference_source WHERE source=$1"; params = [subject_id];
  } else if (action === "dbt.runnerMode.set") {
    subject_type = "runner_mode"; subject_id = "singleton";
    query = "SELECT * FROM control.runner_mode WHERE id";
  } else if (action === "targets.createSet" || action === "targets.importTargets") {
    subject_type = "target_set";
    if (input.target_set_id || input.id) { query = "SELECT * FROM control.target_set WHERE id=$1"; params = [String(input.target_set_id ?? input.id)]; }
  } else if (action === "streamlines.repair") {
    subject_type = "dump"; query = "SELECT * FROM control.dump WHERE id=$1"; params = [subject_id];
  } else if (action === "streamlines.resetCursor") {
    subject_type = "cursor";
    query = "SELECT c.* FROM control.cursor c JOIN control.streamline s ON s.id=c.streamline_id WHERE s.source_key=$1 AND c.target_id IS NOT DISTINCT FROM $2::uuid AND c.cursor_key=$3";
    const found = await rows(db, jsonRow, query, [String(input.source_key), input.target_id === null ? null : String(input.target_id), String(input.cursor_key)]);
    return { subject_type, subject_id: `${String(input.source_key)}:${String(input.target_id ?? "global")}:${String(input.cursor_key)}`, before: sanitize(found) };
  } else if (group === "apiKeys") {
    // Metadata only: audit snapshots never carry the key hash.
    subject_type = "api_key";
    if (subject_id !== "new") {
      query = "SELECT id,label,tenant_id,role,created_at,expires_at,revoked_at FROM control.api_key WHERE id=$1"; params = [subject_id];
    }
  } else if (Array.isArray(input.ids) && table === "target") {
    const ids = z.array(z.string()).parse(input.ids);
    subject_id = ids.join(","); query = "SELECT * FROM control.target WHERE id=ANY($1::uuid[]) ORDER BY id"; params = [ids];
  } else if (table && subject_id !== "new") {
    const key = input.source_key ? "source_key" : input.job_id ? "job_id" : "id";
    query = `SELECT * FROM control.${table} WHERE ${key}=$1`; params = [subject_id];
  }
  const before = query ? await rows(db, jsonRow, query, params) : [];
  if (before.length === 1 && before[0]?.id) subject_id = String(before[0].id);
  return { subject_type, subject_id, before: sanitize(before) };
}

export const auditContext = new AsyncLocalStorage<string>();
export async function recordRemote(path: string, result: unknown) {
  const id = auditContext.getStore();
  if (!id) return;
  await database().unsafe("UPDATE control.audit_log SET after=jsonb_set(after,'{remote}',coalesce(after->'remote','[]'::jsonb) || $2::text::jsonb) WHERE id=$1",
    [id,JSON.stringify([{path,result:sanitize(result)}])]);
}
