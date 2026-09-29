import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import postgres from "postgres";
import { afterAll, describe, expect, it } from "vitest";
import {
  inputQuery,
  readableInputs,
  safeCopy,
  chartExample,
} from "../src/workbench-inputs.js";

const root = path.resolve(import.meta.dirname, "../../../..");
const url = process.env.MDP_WORKBENCH_EXAMPLE_TEST_URL;
const db = url ? postgres(url, { max: 1 }) : null;
afterAll(async () => {
  await db?.end();
});

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const name = path.join(directory, entry.name);
    return entry.isDirectory() ? files(name) : [name];
  });
}
const examples: { name: string; sql: string }[] = [
  { name: "Chart first run", sql: chartExample },
];
const docs = ["docs/analyst-access.md", "docs/operating.md"];
for (const name of docs) {
  const text = readFileSync(path.join(root, name), "utf8");
  for (const block of text.matchAll(/```sql\n([\s\S]*?)\n```/g)) {
    // Sandbox DDL is tested through its SELECT. DuckDB's attached database prefix is local to DuckDB.
    for (const statement of block[1]!.split(";")) {
      const sql = statement
        .trim()
        .replace(/^CREATE (?:TABLE|VIEW) \S+ AS\s+/i, "")
        .replace(/\bwarehouse\.marts\./g, "marts.");
      if (/^(SELECT|WITH)\b/i.test(sql))
        examples.push({
          name: `${name}:${text.slice(0, block.index).split("\n").length}`,
          sql,
        });
    }
  }
  for (const inline of text.matchAll(/`(SELECT [^`]+)`/gi)) {
    if (/\bFROM\s+/i.test(inline[1]!))
      examples.push({ name: `${name} inline`, sql: inline[1]! });
  }
}
for (const name of [
  ...files(path.join(root, "r/mdpr/inst/templates")),
  ...files(path.join(root, "analyses")),
]) {
  const text = readFileSync(name, "utf8");
  if (name.endsWith(".sql"))
    examples.push({ name: path.relative(root, name), sql: text });
  if (name.endsWith(".R")) {
    for (const table of text.matchAll(/mdp_tbl\(con,\s*"([a-z_]+)"\)/g)) {
      examples.push({
        name: path.relative(root, name),
        sql: inputQuery({ schema: "marts", name: table[1]! }),
      });
    }
  }
}

describe.skipIf(!db)("Workbench examples on the local warehouse", () => {
  it("uses the actual restricted role", async () => {
    expect((await db!`select current_user`)[0]?.current_user).toBe(
      "workbench_wh",
    );
  });
  it.each(examples)("runs $name: $sql", async ({ sql }) => {
    await db!.begin("read only", async (tx) => {
      await tx`set local statement_timeout = '5s'`;
      await tx.unsafe(sql);
    });
  });
  it("runs every relation offered by Explorer and Sandbox in Workbench", async () => {
    const inputs = await readableInputs(true, db!);
    expect(inputs.length).toBeGreaterThan(20);
    for (const relation of inputs) await db!.unsafe(inputQuery(relation));
  }, 30000);
  it("refuses the original first query and executes the suggested replacement", async () => {
    const refused = chartExample.replace("explore_staging.", "staging.");
    await expect(db!.unsafe(refused)).rejects.toMatchObject({ code: "42501" });
    const recovery = safeCopy(refused, await readableInputs(true, db!));
    expect(recovery).not.toBeNull();
    await db!.unsafe(recovery!.sql);
    const qualified = safeCopy(
      "select staging.stg_billboard__chart_entries.track_title from staging.stg_billboard__chart_entries limit 1",
      await readableInputs(true, db!),
    );
    expect(qualified).not.toBeNull();
    await db!.unsafe(qualified!.sql);
  });
});
