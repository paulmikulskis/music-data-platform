import { astVisitor, locationOf, parse, type QName } from "pgsql-ast-parser";
import { z } from "zod";
import { platformRead, rows, type DB } from "./db.js";
import { warehouseReader } from "./explorer-data.js";

export const chartExample = `select chart_week, chart_position, track_title
from explore_staging.stg_billboard__chart_entries
order by chart_week desc
limit 100`;

const input = z.object({ schema: z.string(), name: z.string() });
export type WorkbenchInput = z.infer<typeof input>;
const key = (relation: WorkbenchInput) => `${relation.schema}.${relation.name}`;

// Read catalog metadata only. User SQL still runs through the Workbench service.
export async function readableInputs(
  admin: boolean,
  db: DB | null = warehouseReader(),
) {
  if (!db) return [];
  try {
    return await platformRead(db, (tx) =>
      rows(
        tx,
        input,
        `
      SELECT n.nspname AS schema, c.relname AS name
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE c.relkind IN ('r', 'v', 'm', 'p')
        AND n.nspname = ANY($2::text[])
        AND has_schema_privilege($1, n.oid, 'USAGE')
        AND has_table_privilege($1, c.oid, 'SELECT')
      ORDER BY n.nspname, c.relname`,
        [
          admin ? "workbench_wh" : "analyst_ro",
          admin
            ? [
                "marts",
                "staging",
                "intermediate",
                "reference",
                "explore_marts",
                "explore_staging",
                "explore_intermediate",
                "explore_reference",
                "explore_raw",
                "catalog",
              ]
            : [
                "explore_marts",
                "explore_staging",
                "explore_intermediate",
                "catalog",
              ],
        ],
      ),
    );
  } catch {
    // A failed catalog read cannot establish that a replacement is readable.
    return [];
  }
}

export function permittedInput(
  relation: WorkbenchInput,
  inputs: WorkbenchInput[],
) {
  if (
    ![
      "raw",
      "staging",
      "reference",
      "intermediate",
      "marts",
      "explore_raw",
      "explore_staging",
      "explore_reference",
      "explore_intermediate",
      "explore_marts",
      "catalog",
    ].includes(relation.schema)
  )
    return undefined;
  const available = new Map(inputs.map((item) => [key(item), item]));
  return (
    available.get(key(relation)) ??
    available.get(`explore_${relation.schema}.${relation.name}`) ??
    available.get(`marts.${relation.name}`) ??
    available.get(`explore_marts.${relation.name}`)
  );
}

export function inputQuery(relation: WorkbenchInput) {
  const quote = (value: string) => `"${value.replaceAll('"', '""')}"`;
  return `SELECT * FROM ${quote(relation.schema)}.${quote(relation.name)} LIMIT 100;`;
}

export function safeCopy(sql: string, inputs: WorkbenchInput[]) {
  try {
    const statements = parse(sql, { locationTracking: true });
    const statement = statements[0];
    if (statements.length !== 1 || !statement) return null;
    const replacements = new Map<string, WorkbenchInput>();
    let selectOnly = true;
    const visitor = astVisitor((map) => ({
      statement(node) {
        if (
          !["select", "with", "with recursive", "union", "union all"].includes(
            node.type,
          )
        )
          selectOnly = false;
        map.super().statement(node);
      },
      tableRef(table) {
        if (!table.schema) return;
        const relation = { schema: table.schema, name: table.name };
        const replacement = permittedInput(relation, inputs);
        if (replacement && key(replacement) !== key(relation))
          replacements.set(key(relation), replacement);
      },
    }));
    visitor.statement(statement);
    if (!selectOnly || !replacements.size) return null;
    const edits = new Map<number, { end: number; text: string }>();
    let missingSpan = false;
    function replaceSchema(table: QName) {
      const replacement = replacements.get(`${table.schema}.${table.name}`);
      if (!replacement) return;
      const { start, end } = locationOf(table);
      // The catalog counterpart keeps the table name. Change only its schema
      // token; a table node's full span can also include comments and an alias.
      const token = /^(?:"(?:[^"]|"")*"|[a-z_][a-z0-9_$]*)/i.exec(
        sql.slice(start, end),
      )?.[0];
      if (!token) {
        missingSpan = true;
        return;
      }
      edits.set(start, {
        end: start + token.length,
        text: token.startsWith('"')
          ? `"${replacement.schema.replaceAll('"', '""')}"`
          : replacement.schema,
      });
    }
    astVisitor(() => ({
      ref(column) {
        if (column.table?.schema) replaceSchema(column.table);
      },
      tableRef: replaceSchema,
    })).statement(statement);
    if (missingSpan || !edits.size) return null;
    let rewritten = sql;
    for (const [start, edit] of [...edits].sort(([a], [b]) => b - a)) {
      rewritten =
        rewritten.slice(0, start) + edit.text + rewritten.slice(edit.end);
    }
    const relations = [...new Set([...replacements.values()].map(key))];
    return {
      sql: rewritten,
      message: `This query uses a restricted table. Use the readable copy ${relations.join(", ")} and rerun.`,
    };
  } catch {
    // Unsupported SQL keeps the catalog's recovery step and the original draft.
    return null;
  }
}

export function relationFromName(value: string) {
  const match = /^([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)$/.exec(value);
  return match?.[1] && match[2] ? { schema: match[1], name: match[2] } : null;
}

export function workbenchLink(relation: WorkbenchInput) {
  return `/workbench?relation=${encodeURIComponent(key(relation))}`;
}
