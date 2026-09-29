import postgres from "postgres";
import { errorHint } from "@mdp/contracts";
import { z } from "zod";
import { encodeWire } from "@mdp/data-sdk";
export type DB = Pick<postgres.Sql, "unsafe"> & Partial<Pick<postgres.Sql, "begin">>;
export type Param = string | number | boolean | null | string[];
let connection: postgres.Sql | undefined;
export function database() {
  if (!connection)
    connection = postgres(required("MDP_CONTROL_RT_URL"), {
      max: 10,
      connect_timeout: 5,
    });
  return connection;
}
export function required(key: string): string {
  const v = process.env[key];
  if (!v) throw new Error(`Missing environment variable ${key}`);
  return v;
}

export async function platformRead<T>(db: DB, read: (tx: DB) => Promise<T>): Promise<T> {
  if (!db.begin) throw new Error("Platform reads need a connection pool. Use the control database pool.");
  try {
    return await db.begin("read only", async tx => {
      await tx.unsafe("SET LOCAL statement_timeout = '2s'");
      await tx.unsafe("SET LOCAL lock_timeout = '500ms'");
      return await read(tx);
    }) as T;
  } catch (error) {
    if (error instanceof postgres.PostgresError && ["57014", "55P03"].includes(error.code))
      throw new AppError("platform_read_timeout", "This read reached its time limit. Open /ops and retry when the runner is idle.", 503);
    throw error;
  }
}
export async function rows<T extends z.ZodType>(
  db: DB,
  schema: T,
  query: string,
  params: Param[] = [],
): Promise<z.output<T>[]> {
  return z.array(schema).parse(encodeWire(await db.unsafe(query, params)));
}
export async function one<T extends z.ZodType>(
  db: DB,
  schema: T,
  query: string,
  params: Param[] = [],
): Promise<z.output<T>> {
  const result = await rows(db, schema, query, params);
  if (result[0] === undefined)
    throw new AppError("not_found", "No matching record", 404);
  return result[0];
}
export class AppError extends Error {
  get next_step() { return errorHint(this.error_class).next_step; }
  get runbook() { const slug = errorHint(this.error_class).runbook; return slug ? `/runbooks/${slug}` : null; }
  constructor(
    public error_class: string,
    message: string,
    public status = 400,
  ) {
    super(message);
  }
}
