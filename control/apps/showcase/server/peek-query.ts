import { z } from "zod";
import { lineage } from "../lib/lineage";
const scalar = z.union([z.string().max(512), z.number().finite(), z.boolean()]);
export const peekInput = z
  .object({
    relation: z.string(),
    filters: z.record(z.string(), scalar).default({}),
  })
  .strict();
// The only interpolated identifiers come from the generated, reviewed projection.
export function peekQuery(value: unknown) {
  const input = peekInput.parse(value);
  const spec = lineage.peeks[input.relation];
  if (!spec || !/^marts\.[a-z][a-z0-9_]*$/.test(input.relation)) return null;
  if (Object.keys(input.filters).some((key) => !spec.filters.includes(key)))
    return null;
  const predicates: string[] = [];
  const params = Object.entries(input.filters).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  params.forEach(([key], i) => predicates.push(`a."${key}"=$${i + 1}`));
  const sql = `SELECT ${spec.columns.map((key) => `a."${key}"`).join(", ")} FROM ${input.relation} a${predicates.length ? ` WHERE ${predicates.join(" AND ")}` : ""} ORDER BY ${spec.order.map((key) => `a."${key}" DESC`).join(", ")} LIMIT 5`;
  // PostgreSQL escape strings preserve apostrophes, backslashes and textarea payloads as data.
  const literal = (value: z.infer<typeof scalar>) =>
    typeof value === "string"
      ? `E'${value.replaceAll("\\", "\\\\").replaceAll("'", "''")}'`
      : String(value);
  const prefill = sql.replace(/\$(\d+)/g, (_, n: string) =>
    literal(params[Number(n) - 1][1]),
  );
  return {
    input,
    spec,
    sql,
    values: params.map(([, value]) => value),
    prefill:
      spec.workbench && Buffer.byteLength(prefill, "utf8") <= 4096
        ? prefill
        : null,
  };
}
