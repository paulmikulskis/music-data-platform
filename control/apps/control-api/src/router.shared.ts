import { randomUUID } from "node:crypto";
import { implement, ORPCError } from "@orpc/server";
import { z } from "zod";
import { contract } from "@mdp/contracts";
import { database, one, AppError, type DB, type Param } from "./db.js";
import { STAFF_ANALYSIS_PATHS, PROMOTER_PROCEDURES, type Identity } from "./auth.js";
import { auditContext, auditSubject, sanitize } from "./audit.js";

export type Context = { identity: Identity; db: DB; audit_id?: string };

const base = implement(contract).$context<Context>();

export const impl = base.use(async ({ context, next, path, procedure }, input) => {
  const method = procedure["~orpc"].route.method;
  const mutating = method !== "GET";
  const auditId = randomUUID();
  if (mutating) context.audit_id = auditId;
  let detail: Record<string, unknown> = {};
  try {
    if (!context.identity.admin && !(context.identity.staff && (method === "GET" || (method === "POST" && STAFF_ANALYSIS_PATHS.has(procedure["~orpc"].route.path ?? "")))) && !(context.identity.promoter && PROMOTER_PROCEDURES.has(path.join("."))))
      throw new AppError(
        "forbidden",
        "This action needs the admin role. Ask an operator to perform it; open the access runbook.",
        403,
      );
    if (!mutating) return await next();
    const subject = await auditSubject(database(), path.join("."), input);
    detail = { subject_type: subject.subject_type, subject_id: subject.subject_id,
      input: sanitize(input), correlation_id: auditId };
    await database().unsafe(
      "INSERT INTO control.audit_log(id,actor,action,subject,after,before) VALUES ($1,$2,$3,$4,$5::text::jsonb,$6::text::jsonb)",
      [
        auditId,
        context.identity.actor,
        path.join("."),
        subject.subject_id,
        JSON.stringify({ ...detail, state: "started" }),
        JSON.stringify(subject.before),
      ],
    );
    return await database().begin(async (tx) => {
      const result = await auditContext.run(auditId, () => next({ context: { ...context, db: tx } }));
      const output = z.record(z.string(), z.unknown()).safeParse(result.output);
      if (path.join(".") === "targets.parkStale") {
        const empty = z.object({ parked: z.array(z.string()), warnings: z.array(z.unknown()) }).safeParse(result.output);
        if (empty.success && empty.data.parked.length === 0 && empty.data.warnings.length === 0) {
          await tx.unsafe("DELETE FROM control.audit_log WHERE id=$1", [auditId]);
          return result;
        }
      }
      const subjectId = path.join(".") === "dbt.webhook" && output.success && typeof output.data.run_id === "string" ? output.data.run_id
        : detail.subject_id === "new" && output.success && typeof output.data.id === "string" ? output.data.id : String(detail.subject_id);
      const afterInput = detail.subject_id === "new" && output.success && typeof output.data.id === "string" ? { id: output.data.id } : input;
      await tx.unsafe(
        "UPDATE control.audit_log SET subject=$3,after=after || $2::text::jsonb WHERE id=$1",
        [auditId, JSON.stringify({ ...detail, subject_id: subjectId, state: "succeeded", result: sanitize(result.output),
          snapshot: (await auditSubject(tx, path.join("."), afterInput)).before }), subjectId],
      );
      return result;
    });
  } catch (e) {
    if (mutating)
      await database().unsafe(
        "UPDATE control.audit_log SET after=after || $2::text::jsonb WHERE id=$1",
        [
          auditId,
          JSON.stringify({
            ...detail,
            state: "failed",
            message: e instanceof AppError ? e.message : "The operation could not complete; inspect its trace.",
            error_class:
              e instanceof AppError ? e.error_class : "internal_error",
          }),
        ],
      );
    if (e instanceof AppError)
      throw new ORPCError("SERVICE", {
        status: e.status,
        message: e.message,
        data: { error_class: e.error_class, message: e.message, next_step: e.next_step, runbook: e.runbook },
      });
    throw e;
  }
});

export const dbOf = (c: Context) => c.db;

export async function patch<T extends z.ZodType>(
  db: DB,
  schema: T,
  table: string,
  key: string,
  value: string,
  values: Record<string, Param | undefined>,
) {
  const fields = Object.entries(values).filter(
    (e): e is [string, Param] => e[1] !== undefined,
  );
  if (!fields.length)
    return one(db, schema, `SELECT * FROM control.${table} WHERE ${key}=$1`, [
      value,
    ]);
  return one(
    db,
    schema,
    `UPDATE control.${table} SET ${fields.map(([k], i) => `${k}=$${i + 2}`).join(",")} WHERE ${key}=$1 RETURNING *`,
    [value, ...fields.map(([, v]) => v)],
  );
}
