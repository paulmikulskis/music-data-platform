// Reconcile historical bootstrap metadata with the schema shipped in a fresh public snapshot.
// This command is for snapshot preparation, never an upgrade of an existing database.
import { generateDrizzleJson } from 'drizzle-kit/api';
import * as schema from '../src/schema/index.js';
import { readFileSync, writeFileSync, readdirSync, renameSync } from 'node:fs';
import { resolve } from 'node:path';
const root = resolve(import.meta.dirname, '../drizzle');
const current = generateDrizzleJson(schema, undefined, ['control']);
const retired = new Set<string>();
for (const name of readdirSync(resolve(root, 'meta')).filter(n => n.endsWith('_snapshot.json'))) {
  const path = resolve(root, 'meta', name);
  const snapshot = JSON.parse(readFileSync(path, 'utf8'));
  for (const table of Object.keys(snapshot.tables)) {
    if (!(table in current.tables)) { retired.add(table); delete snapshot.tables[table]; continue; }
    const target = current.tables[table]!;
    for (const key of Object.keys(snapshot.tables[table].checkConstraints ?? {})) {
      if (key in target.checkConstraints) snapshot.tables[table].checkConstraints[key] = target.checkConstraints[key];
    }
  }
  writeFileSync(path, JSON.stringify(snapshot, null, 2) + '\n');
}
for (const name of readdirSync(root).filter(n => n.endsWith('.sql'))) {
  const path = resolve(root, name);
  const statements = readFileSync(path, 'utf8').split('--> statement-breakpoint');
  const kept = statements.filter(statement => ![...retired].some(table => {
    const [namespace, relation] = table.split('.');
    return statement.includes(`"${namespace}"."${relation}"`) || statement.includes(`${namespace}.${relation}`);
  }));
  writeFileSync(path, kept.join('--> statement-breakpoint'));
}
// The resource-kind constraint is generated from the current declaration.
const target = current.tables['control.target_spec']!;
const constraint = target.checkConstraints['target_spec_resource_kind']!;
const journalPath = resolve(root, 'meta/_journal.json');
const journal = JSON.parse(readFileSync(journalPath, 'utf8'));
const entry = journal.entries.find((entry: {idx: number}) => entry.idx === 20);
const old = resolve(root, entry.tag + '.sql');
entry.tag = '0020_target_kind_constraint';
const path = resolve(root, entry.tag + '.sql');
if (old !== path) renameSync(old, path);
writeFileSync(path, `-- Generated from the target specification declaration.\nALTER TABLE "control"."target_spec" DROP CONSTRAINT "target_spec_resource_kind";--> statement-breakpoint\nALTER TABLE "control"."target_spec" ADD CONSTRAINT "target_spec_resource_kind" CHECK (${constraint.value});\n`);
writeFileSync(journalPath, JSON.stringify(journal, null, 2) + '\n');
