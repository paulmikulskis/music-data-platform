import {
  readFileSync,
  writeFileSync,
  mkdirSync,
  readdirSync,
  existsSync,
  rmSync,
} from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";
import { z } from "zod";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..");
const column = z.object({
  name: z.string(),
  data_type: z.string(),
  constraints: z.array(z.object({ type: z.string() })).default([]),
  meta: z.object({ nullable: z.boolean().optional() }).optional(),
  data_tests: z.array(z.unknown()).default([]),
  tests: z.array(z.unknown()).default([]),
});
const contract = z.object({
  models: z
    .array(
      z.object({
        name: z.string(),
        config: z
          .object({
            meta: z
              .object({
                tenant_scoped: z.boolean().optional(),
                // false: operator identities read it, tenant keys never do.
                tenant_readable: z.boolean().optional(),
                api_schema: z.string().optional(),
                grain: z.array(z.string()).optional(),
              })
              .optional(),
          })
          .optional(),
        columns: z.array(column),
      }),
    )
    .default([]),
});
function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(resolve(dir, e.name)) : [resolve(dir, e.name)],
  );
}
export function scalar(type: string): string {
  const t = type.toLowerCase();
  if (/^(bigint|int8|hugeint)/.test(t)) return "bigintWire";
  if (/^(decimal|numeric)/.test(t)) return "decimalWire";
  if (/^(timestamp|datetime)/.test(t)) return "timestampWire";
  if (t === "date") return "dateWire";
  if (/^(int|smallint|tinyint)/.test(t)) return "z.number().int()";
  if (/^(float|double|real)/.test(t)) return "z.number()";
  if (/^(bool)/.test(t)) return "z.boolean()";
  if (/^(text|varchar|char|string|uuid)/.test(t)) return "z.string()";
  if (/^json/.test(t)) return "z.json()";
  throw new Error(`Unsupported contract type ${type}`);
}
const contractOnly = process.argv.includes("--contract-only");
const catalogPath =
  process.argv.slice(2).find((arg) => !arg.startsWith("--")) ??
  process.env.MDP_CATALOG_PATH ??
  resolve(root, "dbt/target/catalog.json");
if (!contractOnly && !existsSync(catalogPath))
  throw new Error("Catalog is missing; pass --contract-only for explicit YAML generation");
const catalog = !contractOnly && existsSync(catalogPath)
  ? z
      .object({
        nodes: z.record(
          z.string(),
          z.object({
            metadata: z.object({ name: z.string() }),
            columns: z.record(
              z.string(),
              z.object({ name: z.string(), type: z.string() }),
            ),
          }),
        ),
      })
      .parse(JSON.parse(readFileSync(catalogPath, "utf8")))
  : null;
const models = files(resolve(root, "dbt/models/marts"))
  .filter((p) => /\.ya?ml$/.test(p))
  .flatMap((p) =>
    contract
      .parse(parse(readFileSync(p, "utf8")))
      .models.map((m) => ({
        m,
        tenant: m.config?.meta?.tenant_scoped ?? p.includes("/tenant/"),
      })),
  )
  .sort((a, b) => a.m.name.localeCompare(b.m.name));
const out = resolve(root, "control/packages/data-sdk/src/generated");
mkdirSync(out, { recursive: true });
for (const stale of readdirSync(out))
  if (stale.endsWith('.zod.ts') && stale !== 'marts.zod.ts' && !models.some(({m}) => stale === `${m.name}.zod.ts`)) rmSync(resolve(out, stale));
// Served marts declare meta.grain; each gets a generated uniqueness test over it.
const grainTests = resolve(root, "dbt/tests/grain");
mkdirSync(grainTests, { recursive: true });
for (const stale of readdirSync(grainTests))
  if (!models.some(({ m }) => stale === `grain__${m.name}.sql` && m.config?.meta?.grain))
    rmSync(resolve(grainTests, stale));
const exports: string[] = [];
for (const { m, tenant } of models) {
  const node =
    catalog &&
    Object.values(catalog.nodes).find((n) => n.metadata.name === m.name);
  if (catalog && !node) throw new Error(`Catalog missing mart ${m.name}`);
  if (node)
    for (const c of m.columns)
      if (!Object.values(node.columns).some((n) => n.name === c.name))
        throw new Error(`Catalog missing ${m.name}.${c.name}`);
  const fields = m.columns.map((c) => {
    const nullable =
      c.meta?.nullable ??
      !(
        c.constraints.some((x) => x.type === "not_null") ||
        [...c.tests, ...c.data_tests].some(
          (t) =>
            t === "not_null" ||
            (typeof t === "object" && t !== null && "not_null" in t),
        )
      );
    const catalogColumn =
      node && Object.values(node.columns).find((n) => n.name === c.name);
    if (catalogColumn && scalar(catalogColumn.type) !== scalar(c.data_type))
      throw new Error(`Catalog type mismatch ${m.name}.${c.name}`);
    return { name: c.name, wire: scalar(catalogColumn?.type ?? c.data_type), nullable };
  });
  const grain = m.config?.meta?.grain ?? [];
  for (const name of grain) {
    const column = m.columns.find((c) => c.name === name);
    if (!column) throw new Error(`Grain column missing from contract ${m.name}.${name}`);
    // Keyset paging compares grain tuples; an enforced constraint keeps nulls out of the swap.
    if (!column.constraints.some((x) => x.type === "not_null"))
      throw new Error(`Grain column needs a not_null constraint ${m.name}.${name}`);
  }
  if (grain.length)
    for (const name of ["learning_eligible", "resale_permitted", "source_keys"])
      if (!fields.some((f) => f.name === name))
        throw new Error(`Served mart must carry the annotation contract ${m.name}.${name}`);
  const timeColumns = fields
    .filter((f) => f.wire === "timestampWire" || f.wire === "dateWire")
    .map((f) => f.name);
  if (grain.length)
    writeFileSync(
      resolve(grainTests, `grain__${m.name}.sql`),
      `-- Generated from meta.grain by pnpm --filter @mdp/data-sdk generate. Do not edit.\nselect ${grain.join(", ")}, count(*) as rows_per_grain\nfrom {{ ref('${m.name}') }}\ngroup by ${grain.join(", ")}\nhaving count(*) > 1\n`,
    );
  const shape = fields.map((f) => `  ${JSON.stringify(f.name)}: ${f.wire}${f.nullable ? ".nullable()" : ""},`);
  const range = timeColumns.map((name) => {
    const wire = fields.find((f) => f.name === name)?.wire ?? "timestampWire";
    return `  ${JSON.stringify(name)}: z.object({ from: ${wire}, to: ${wire} }).partial().strict(),`;
  });
  const metadata = {
    name: m.name,
    schema: m.config?.meta?.api_schema ?? "marts",
    tenant_scoped: tenant,
    tenant_readable: m.config?.meta?.tenant_readable !== false,
    columns: m.columns.map((c) => c.name),
    grain,
    grain_types: grain.map((name) => m.columns.find((c) => c.name === name)?.data_type ?? "text"),
    time_columns: timeColumns,
  };
  const code = `// Generated from the catalog and mart contracts. Do not edit.\nimport { z } from 'zod';\nimport { bigintWire, decimalWire, timestampWire, dateWire, encodeMartRow } from '../wire.js';\nexport const ${m.name} = z.object({\n${shape.join("\n")}\n});\nexport type ${m.name} = z.infer<typeof ${m.name}>;\n// Half-open time ranges: from <= column < to.\nexport const range = z.object({\n${range.join("\n")}\n}).partial().strict();\nexport const encode = (row: unknown) => ${m.name}.parse(encodeMartRow(row,${JSON.stringify(m.columns.filter((c) => c.data_type.toLowerCase() === "date").map((c) => c.name))}));\nexport const decode = (row: unknown) => ${m.name}.parse(row);\nexport const metadata = ${JSON.stringify(metadata)};\n`;
  writeFileSync(resolve(out, `${m.name}.zod.ts`), code);
  exports.push(
    `export { ${m.name}, range as range_${m.name}, encode as encode_${m.name}, decode as decode_${m.name}, metadata as metadata_${m.name} } from './${m.name}.zod.js';`,
  );
}
writeFileSync(
  resolve(out, "marts.zod.ts"),
  "// Generated exports.\n" + exports.join("\n") + "\n",
);
console.log(`Generated ${models.length} mart schemas`);
