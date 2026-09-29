import { describe, expect, it } from "vitest";
import {
  inputQuery,
  permittedInput,
  safeCopy,
  chartExample,
} from "../src/workbench-inputs.js";

const inputs = [
  { schema: "explore_staging", name: "stg_billboard__chart_entries" },
  { schema: "explore_raw", name: "account_snapshots" },
  { schema: "explore_reference", name: "rights_registry" },
  { schema: "explore_intermediate", name: "int_accounts" },
  { schema: "marts", name: "mart_chart_history" },
];
describe("readable Workbench inputs", () => {
  it("starts with the safe fixture account copy", () => {
    expect(chartExample).toContain(
      "from explore_staging.stg_billboard__chart_entries",
    );
  });
  it.each([
    "raw.account_snapshots",
    "reference.rights_registry",
    "intermediate.int_accounts",
    "staging.stg_billboard__chart_entries",
  ])("replaces %s", (relation) => {
    const result = safeCopy(`select * from ${relation}`, inputs);
    expect(result?.message).toContain(`explore_${relation}`);
    expect(result?.sql).toContain(`explore_${relation}`);
  });
  it("preserves literals, aliases, filters, joins and CTEs", () => {
    const sql = `with recent as (select * from "staging"."stg_billboard__chart_entries")
      select s.handle, 'raw.account_snapshots' as note from recent s
      join raw.account_snapshots r on r.handle = s.handle where s.followers > 10 limit 5`;
    const result = safeCopy(sql, inputs);
    expect(result).not.toBeNull();
    expect(result?.sql).toBe(
      sql
        .replace('"staging".', '"explore_staging".')
        .replace("join raw.", "join explore_raw."),
    );
  });
  it.each([
    {
      name: "a 19-digit integer and original whitespace and casing",
      sql: "SELECT  handle\nFROM raw.account_snapshots\nWHERE platform_account_id::bigint = 7123456789012345678;\n",
      expected:
        "SELECT  handle\nFROM explore_raw.account_snapshots\nWHERE platform_account_id::bigint = 7123456789012345678;\n",
    },
    {
      name: "quoted identifiers and a quoted alias",
      sql: 'Select "Account"."handle" FROM "raw" . "account_snapshots" AS "Account";',
      expected:
        'Select "Account"."handle" FROM "explore_raw" . "account_snapshots" AS "Account";',
    },
    {
      name: "both occurrences of a schema-qualified table",
      sql: "select a.handle from raw.account_snapshots a JOIN RAW.ACCOUNT_SNAPSHOTS b on a.handle = b.handle",
      expected:
        "select a.handle from explore_raw.account_snapshots a JOIN explore_raw.ACCOUNT_SNAPSHOTS b on a.handle = b.handle",
    },
    {
      name: "comments containing the table name, including between identifiers",
      sql: "-- raw.account_snapshots\nselect * from raw /* raw.account_snapshots */ . account_snapshots -- raw.account_snapshots\n",
      expected:
        "-- raw.account_snapshots\nselect * from explore_raw /* raw.account_snapshots */ . account_snapshots -- raw.account_snapshots\n",
    },
    {
      name: "a CTE with the same short name",
      sql: "WITH account_snapshots AS (SELECT * FROM raw.account_snapshots)\nSELECT account_snapshots.handle FROM account_snapshots;",
      expected:
        "WITH account_snapshots AS (SELECT * FROM explore_raw.account_snapshots)\nSELECT account_snapshots.handle FROM account_snapshots;",
    },
    {
      name: "schema-qualified column references with their table",
      sql: "select raw.account_snapshots.handle from raw.account_snapshots",
      expected:
        "select explore_raw.account_snapshots.handle from explore_raw.account_snapshots",
    },
    {
      name: "quoted column qualifiers and literal text",
      sql: `select "raw" /* keep */ . "account_snapshots" . "handle", 'raw.account_snapshots' from "raw"."account_snapshots"`,
      expected: `select "explore_raw" /* keep */ . "account_snapshots" . "handle", 'raw.account_snapshots' from "explore_raw"."account_snapshots"`,
    },
  ])("preserves $name exactly", ({ sql, expected }) => {
    expect(safeCopy(sql, inputs)?.sql).toBe(expected);
  });
  it("does not rewrite a literal, comment, alias or already readable table", () => {
    expect(
      safeCopy(
        "select 'staging.stg_billboard__chart_entries' from marts.mart_chart_history -- raw.account_snapshots",
        inputs,
      ),
    ).toBeNull();
    expect(
      safeCopy(
        "select staging.chart_position from marts.mart_chart_history staging",
        inputs,
      ),
    ).toBeNull();
    expect(safeCopy(chartExample, inputs)).toBeNull();
  });
  it("keeps the fallback when no readable copy exists or SQL is unsupported", () => {
    for (const sql of [
      "select * from staging.secret",
      "select * from {{ ref('example') }}",
      "select * from raw.account_snapshots; delete from raw.account_snapshots",
      "delete from raw.account_snapshots",
    ]) {
      expect(safeCopy(sql, inputs)).toBeNull();
    }
    expect(safeCopy("select * from raw.account_snapshots", [])).toBeNull();
  });
  it("uses an existing served copy and quotes suggested input names", () => {
    const relation = permittedInput(
      { schema: "staging", name: "mart_chart_history" },
      inputs,
    );
    expect(relation).toEqual(inputs[4]);
    expect(inputQuery(inputs[4]!)).toBe(
      'SELECT * FROM "marts"."mart_chart_history" LIMIT 100;',
    );
  });
});
