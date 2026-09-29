import { readTransaction } from "./read-deadline.js";
import { labelsFor } from "@mdp/data-sdk";
import postgres from "postgres";
import { ORPCError } from "@orpc/server";
import { z } from "zod";
import { authenticate } from "@mdp/control-api/auth";
import { AppError, type DB } from "../../control-api/src/db.js";
// Mart timestamps are UTC without zone metadata; parse them as UTC on any host time zone.
export const readerTypes = {
  utcTimestamp: {
    to: 1114,
    from: [1114],
    // Parameters arrive as wire strings; the server infers their timestamp type.
    serialize: (value: Date | string) =>
      value instanceof Date ? value.toISOString() : value,
    parse: (value: string) => new Date(`${value.replace(" ", "T")}Z`),
  },
};
// `keys` is the api_key_reader connection: the data API's one control credential.
// `pause` runs between the build-row read and the page read; the swap fixture injects a rebuild there.
export type ReadContext = {
  request: Request;
  warehouse: postgres.Sql;
  keys: DB;
  pause?: (tx: postgres.TransactionSql) => Promise<void>;
};
export type MartMetadata = {
  name: string;
  schema: string;
  tenant_scoped: boolean;
  // false for a global mart that tenant keys never read; operator identities still do.
  tenant_readable: boolean;
  // Every contract column; a relation built before the contract lacks some of them.
  columns: string[];
  grain: string[];
  grain_types: string[];
  time_columns: string[];
};
type Build = {
  cycle_id: string;
  close_no: string | null;
  built_at: string;
} | null;
const buildSchema = z
  .object({
    cycle_id: z.string(),
    close_no: z.string().nullable(),
    built_at: z.string(),
  })
  .strict()
  .nullable();
const cursorSchema = z
  .object({
    mart: z.string(),
    tenant: z.string().nullable(),
    query: z.string(),
    after: z.array(z.string()),
    build: buildSchema,
  })
  .strict();
const identifier = /^[a-z_][a-z0-9_-]*$/;
const castable =
  /^(text|varchar|bigint|integer|int|smallint|boolean|date|timestamp|timestamptz|double precision|numeric)$/;
export function dataError(
  error_class: string,
  message: string,
  status: number,
) {
  return new ORPCError("DATA", {
    status,
    message,
    data: { error_class, message },
  });
}
const sameBuild = (a: Build, b: Build) =>
  a === b ||
  (a !== null &&
    b !== null &&
    a.cycle_id === b.cycle_id &&
    a.close_no === b.close_no &&
    a.built_at === b.built_at);
const pgCode = (error: unknown) =>
  typeof error === "object" &&
  error !== null &&
  "code" in error &&
  typeof error.code === "string"
    ? error.code
    : "";
// A ZodError never reaches oRPC as an unlogged 500: it is logged with the mart and its columns.
export async function readMart<T extends z.ZodRawShape>(
  context: ReadContext,
  metadata: MartMetadata,
  row: z.ZodObject<T>,
  encoder: (value: unknown) => z.output<z.ZodObject<T>>,
  input: {
    limit: number;
    cursor?: string | undefined;
    filters?: unknown;
    range?: unknown;
  },
) {
  try {
    return await read(context, metadata, row, encoder, input);
  } catch (error) {
    if (!(error instanceof z.ZodError)) throw error;
    console.error(
      JSON.stringify({
        event: "data_parse_failed",
        mart: metadata.name,
        columns: [
          ...new Set(error.issues.map((issue) => String(issue.path[0] ?? ""))),
        ],
        codes: error.issues.map((issue) => issue.code),
      }),
    );
    throw dataError(
      "data_invalid",
      "A served row does not match the mart contract; see the data API log",
      500,
    );
  }
}
async function read<T extends z.ZodRawShape>(
  context: ReadContext,
  metadata: MartMetadata,
  row: z.ZodObject<T>,
  encoder: (value: unknown) => z.output<z.ZodObject<T>>,
  input: {
    limit: number;
    cursor?: string | undefined;
    filters?: unknown;
    range?: unknown;
  },
) {
  const identity = await authenticate(context.request, context.keys).catch(
    (error: unknown) => {
      if (error instanceof AppError)
        throw dataError(error.error_class, error.message, error.status);
      throw dataError(
        "auth_unavailable",
        "Key check failed; retry shortly",
        503,
      );
    },
  );
  if (identity.promoter || (identity.staff && !identity.admin))
    throw dataError(
      "forbidden",
      "This identity cannot use the data API. Ask an operator for a reader key.",
      403,
    );
  const tenant = metadata.tenant_scoped ? identity.tenant_id : null;
  if (!metadata.tenant_readable && identity.tenant_id)
    throw dataError(
      "tenant_forbidden",
      "This mart is not served to tenant keys",
      403,
    );
  let schema = metadata.schema;
  if (metadata.tenant_scoped) {
    // The authenticated key binds every tenant overlay read to its own tenant schema.
    if (
      !tenant ||
      !identity.tenant_slug ||
      !/^[a-z][a-z0-9_-]*$/.test(identity.tenant_slug)
    )
      throw dataError("tenant_required", "Use a key issued to a tenant", 403);
    schema = `tenant_${identity.tenant_slug}_${metadata.schema}`;
  }
  if (!metadata.grain.length)
    throw dataError("not_served", "This mart declares no grain", 404);
  for (const name of [schema, metadata.name, ...metadata.grain])
    if (!identifier.test(name))
      throw dataError("invalid_relation", "Invalid relation", 400);
  for (const type of metadata.grain_types)
    if (!castable.test(type))
      throw dataError("invalid_relation", "Unsupported grain type", 400);
  const parsedFilters = z
    .record(z.string(), z.unknown())
    .safeParse(input.filters ?? {});
  if (!parsedFilters.success)
    throw dataError(
      "invalid_filter",
      "Filters are an object of column values",
      400,
    );
  const parsedRange = z
    .record(
      z.string(),
      z.object({ from: z.string().optional(), to: z.string().optional() }),
    )
    .safeParse(input.range ?? {});
  if (!parsedRange.success)
    throw dataError(
      "invalid_range",
      "Ranges map time columns to from and to",
      400,
    );
  const filters = parsedFilters.data;
  const range = parsedRange.data;
  const query = JSON.stringify([
    Object.entries(filters).sort(([a], [b]) => a.localeCompare(b)),
    Object.entries(range).sort(([a], [b]) => a.localeCompare(b)),
  ]);
  const params: (string | number | boolean | null)[] = [];
  const clauses: string[] = [];
  for (const [key, value] of Object.entries(filters)) {
    if (!(key in row.shape) || key === "tenant_id" || !identifier.test(key))
      throw dataError("invalid_filter", `Unknown filter ${key}`, 400);
    if (value === null) clauses.push(`t."${key}" IS NULL`);
    else {
      const scalar = z
        .union([z.string(), z.number(), z.boolean()])
        .safeParse(value);
      if (!scalar.success)
        throw dataError(
          "invalid_filter",
          `Filter ${key} takes a scalar value`,
          400,
        );
      params.push(scalar.data);
      // Membership is frozen in the scored row, so paging uses the same build after a rename.
      const movement = [
        "mart_top_movers",
        "mart_top_movers_current",
        "mart_early_signals_current",
        "mart_arrivals_current",
      ].includes(metadata.name);
      if (
        movement &&
        !metadata.tenant_scoped &&
        schema === "marts" &&
        key === "song_key"
      ) {
        clauses.push(`t."member_song_keys"::jsonb ? $${params.length}::text`);
      } else {
        clauses.push(`t."${key}"=$${params.length}`);
      }
    }
  }
  for (const [key, bounds] of Object.entries(range)) {
    if (!metadata.time_columns.includes(key) || !identifier.test(key))
      throw dataError(
        "invalid_range",
        `Range filters apply to time columns only: ${key}`,
        400,
      );
    if (bounds.from !== undefined) {
      params.push(bounds.from);
      clauses.push(`t."${key}">=$${params.length}`);
    }
    if (bounds.to !== undefined) {
      params.push(bounds.to);
      clauses.push(`t."${key}"<$${params.length}`);
    }
  }
  if (metadata.tenant_scoped && "tenant_id" in row.shape) {
    params.push(tenant);
    clauses.push(`t.tenant_id=$${params.length}`);
  }
  let cursor: z.infer<typeof cursorSchema> | null = null;
  if (input.cursor) {
    const parsed = cursorSchema.safeParse(
      (() => {
        try {
          return JSON.parse(
            Buffer.from(input.cursor, "base64url").toString("utf8"),
          );
        } catch {
          return null;
        }
      })(),
    );
    if (!parsed.success || parsed.data.after.length !== metadata.grain.length)
      throw dataError("invalid_cursor", "Cursor is malformed", 400);
    if (
      parsed.data.mart !== metadata.name ||
      parsed.data.tenant !== tenant ||
      parsed.data.query !== query
    )
      throw dataError("invalid_cursor", "Cursor belongs to another query", 400);
    cursor = parsed.data;
  }
  // Grain order, with text in byte order so ordering and the keyset comparison agree.
  const text = (type: string) => type === "text" || type === "varchar";
  const columns = metadata.grain.map(
    (name, i) =>
      `t."${name}"${text(metadata.grain_types[i] ?? "") ? ' COLLATE "C"' : ""}`,
  );
  if (cursor) {
    const values = cursor.after.map((value, i) => {
      params.push(value);
      const type = metadata.grain_types[i] ?? "text";
      return `$${params.length}::${type}${text(type) ? ' COLLATE "C"' : ""}`;
    });
    clauses.push(`(${columns.join(",")})>(${values.join(",")})`);
  }
  params.push(input.limit + 1);
  const relation = `${schema}.${metadata.name}`;
  const grainText = metadata.grain.map((name) => `t."${name}"::text`).join(",");
  // One snapshot for the build row and the page. The first statement takes the share lock, before
  // any snapshot exists, so a rebuild's rename swap either commits wholly before this read or waits
  // for it to end. Without the lock a swap between the two reads leaves the page empty under the old
  // build id. The data API's connection sets statement_timeout, so the lock wait is bounded too.
  const result = await readTransaction(context.warehouse, async (tx, read) => {
    await read(
      tx.unsafe(
        `LOCK TABLE "${schema}"."${metadata.name}" IN ACCESS SHARE MODE`,
      ),
    );
    await read(tx`SET LOCAL statement_timeout='5s'`);
    // A relation built before this contract (by the previous release) is served again after its
    // next rebuild; until then it answers contract_pending, never a parse failure.
    const present = new Set(
      (
        await read(tx`SELECT attname FROM pg_attribute WHERE attrelid=${`"${schema}"."${metadata.name}"`}::regclass
        AND attnum>0 AND NOT attisdropped`)
      ).map((column) => String(column.attname)),
    );
    const missing = metadata.columns.filter((column) => !present.has(column));
    if (missing.length) {
      console.error(
        JSON.stringify({
          event: "contract_pending",
          mart: metadata.name,
          schema,
          missing,
        }),
      );
      throw dataError(
        "contract_pending",
        "The mart was built before its current contract; it is served again after its next rebuild",
        503,
      );
    }
    const stamped = await read(
      tx`SELECT to_regclass('marts._build') IS NOT NULL AS present`,
    );
    const found = stamped[0]?.present
      ? await read(
          tx`SELECT cycle_id, close_no::text AS close_no, built_at::text AS built_at FROM marts._build WHERE relation=${relation}`,
        )
      : [];
    const build = buildSchema.parse(
      found[0]
        ? {
            cycle_id: found[0].cycle_id,
            close_no: found[0].close_no,
            built_at: found[0].built_at,
          }
        : null,
    );
    if (cursor && !sameBuild(cursor.build, build))
      throw dataError(
        "cursor_stale",
        "The mart was rebuilt since this cursor was issued; restart paging",
        409,
      );
    if (context.pause) await context.pause(tx);
    const records = await read(
      tx.unsafe(
        `SELECT t.*, ARRAY[${grainText}] AS __grain FROM "${schema}"."${metadata.name}" t ${clauses.length ? "WHERE " + clauses.join(" AND ") : ""} ORDER BY ${columns.join(",")} LIMIT $${params.length}`,
        params,
      ),
    );
    return { build, records };
  }).catch((error: unknown) => {
    if (error instanceof ORPCError) throw error;
    const code = pgCode(error);
    // A tenant's schema, like its mart, exists only after its first build.
    if (code === "42P01" || code === "3F000")
      throw dataError(
        "data_unavailable",
        "The mart is not built for this scope",
        404,
      );
    if (code === "57014")
      throw dataError(
        "read_timeout",
        "The read exceeded its time limit; narrow the range",
        503,
      );
    if (code.startsWith("22"))
      throw dataError(
        "invalid_input",
        "A filter, range, or cursor value has the wrong type",
        400,
      );
    console.error(
      JSON.stringify({
        event: "data_read_failed",
        mart: metadata.name,
        schema,
        code,
        message: error instanceof Error ? error.message : "unknown",
      }),
    );
    throw dataError(
      "data_unavailable",
      "Read failed; verify the mart build and session scope",
      503,
    );
  });
  const page = result.records.slice(0, input.limit);
  const last = page.at(-1);
  const next_cursor =
    result.records.length > input.limit && last
      ? Buffer.from(
          JSON.stringify({
            mart: metadata.name,
            tenant,
            query,
            after: z.array(z.string()).parse(last.__grain),
            build: result.build,
          }),
        ).toString("base64url")
      : null;
  return {
    rows: page.map(encoder),
    next_cursor,
    labels: labelsFor(schema, metadata.name),
    build: {
      relation,
      scope: /^tenant_.+_marts$/.test(schema)
        ? ("tenant" as const)
        : ("global" as const),
      tenant_slug: metadata.tenant_scoped ? identity.tenant_slug : null,
      stamped: result.build !== null,
      cycle_id: result.build?.cycle_id ?? null,
      close_no: result.build?.close_no ?? null,
      built_at: result.build
        ? new Date(result.build.built_at).toISOString()
        : null,
    },
  };
}
