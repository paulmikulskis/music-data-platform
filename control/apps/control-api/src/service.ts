import { AsyncLocalStorage } from "node:async_hooks";
import { recordRemote } from "./audit.js";
import { z } from "zod";
import { errorSchema } from "@mdp/contracts";
import { AppError, required } from "./db.js";
export const serviceDeadline = new AsyncLocalStorage<number>();

export async function service<T extends z.ZodType>(
  path: string,
  schema: T,
  body?: unknown,
  key?: string,
  options: { timeoutMs?: number } = {},
): Promise<z.output<T>> {
  let response: Response;
  let result: unknown;
  let signal: AbortSignal | undefined;
  try {
    const timeoutMs = options.timeoutMs ?? 20000;
    const remaining = (serviceDeadline.getStore() ?? (Date.now() + timeoutMs)) - Date.now();
    if (remaining <= 0) throw new AppError("invoke_timeout", "Service request deadline reached", 503);
    signal = AbortSignal.timeout(Math.min(timeoutMs, remaining));
    response = await fetch(new URL(path, required("MDP_SERVICE_URL")), {
      method: body === undefined ? "GET" : "POST",
      headers: {
        authorization: `Bearer ${required("MDP_SERVICE_TOKEN")}`,
        "content-type": "application/json",
        ...(key ? { "idempotency-key": key } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal,
    });
    result = await response.json();
  } catch (error) {
    if (error instanceof AppError) throw error;
    const timedOut = signal?.aborted || isTimeout(error);
    throw new AppError(
      timedOut ? "invoke_timeout" : "service_unreachable",
      timedOut ? "Service request deadline reached" : "Functions service is unavailable",
      503,
    );
  }
  if (!response.ok) {
    const error = errorSchema.safeParse(result);
    throw new AppError(
      error.success ? error.data.error_class : "service_error",
      error.success ? error.data.message : "Service request failed",
      response.status,
    );
  }
  if (body !== undefined) await recordRemote(path, result);
  return schema.parse(normalizeService(result));
}
function isTimeout(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const code = z.object({code:z.string()}).safeParse(error);
  return /(?:Timeout|Abort)Error$/.test(error.name) || (code.success && code.data.code.endsWith("_TIMEOUT")) || isTimeout(error.cause);
}

export async function dbtCloud(path: string, body?: unknown): Promise<unknown> {
  if (!process.env.DBT_CLOUD_TOKEN || !process.env.DBT_CLOUD_ACCOUNT_ID)
    throw new AppError(
      "dbt_api_unavailable",
      "Configure the dbt Cloud account and token",
      503,
    );
  try {
    const response = await fetch(
      `${process.env.DBT_CLOUD_HOST ?? "https://cloud.getdbt.com"}/api/v2/accounts/${process.env.DBT_CLOUD_ACCOUNT_ID}/${path}`,
      {
        method: body === undefined ? "GET" : "POST",
        headers: {
          authorization: `Bearer ${process.env.DBT_CLOUD_TOKEN}`,
          "content-type": "application/json",
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(15000),
      },
    );
    if (!response.ok) throw new Error("dbt Cloud refused request");
    const result = z.object({ data: z.unknown() }).parse(await response.json()).data;
    if (body !== undefined) await recordRemote(`dbt/${path}`, result);
    return result;
  } catch {
    throw new AppError(
      "dbt_api_unavailable",
      "dbt Cloud request unavailable",
      503,
    );
  }
}

export function normalizeService(value: unknown): unknown {
  if (
    typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)
  )
    return new Date(value).toISOString();
  if (Array.isArray(value)) return value.map(normalizeService);
  if (value !== null && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([k, v]) => [k, normalizeService(v)]),
    );
  return value;
}
