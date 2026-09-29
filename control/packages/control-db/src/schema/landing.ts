import { sql } from 'drizzle-orm';
import { bigint, boolean, index, integer, jsonb, text, timestamp, unique, uuid, type AnyPgColumn } from 'drizzle-orm/pg-core';
import { control, dumpKind, loadOp, loadStatus } from './enums.js';
import { streamline, warehouse } from './config.js';
import { cycle, targetExport } from './cycles.js';
import { run } from './runs.js';
const created = () => timestamp('created_at', { withTimezone: true }).notNull().defaultNow();
const updated = () => timestamp('updated_at', { withTimezone: true }).notNull().defaultNow();
export const landedSeq = control.sequence('landed_seq');
export const dump = control.table('dump', {
  id: uuid().primaryKey().defaultRandom(), kind: dumpKind().notNull(), run_id: uuid().notNull().references((): AnyPgColumn => run.id),
  streamline_id: uuid().references(() => streamline.id), cycle_id: uuid().references(() => cycle.id),
  revision_id: uuid().references(() => targetExport.id),
  landed_seq: bigint({ mode: 'bigint' }).notNull().default(sql`nextval('control.landed_seq')`), uri_prefix: text().notNull(),
  files: jsonb().notNull().default([]), row_count: bigint({ mode: 'bigint' }).notNull().default(sql`0`),
  schema_fingerprint: text(), function_version: text(), lease_token: uuid(), published_at: timestamp({ withTimezone: true }),
  quarantined_at: timestamp({ withTimezone: true }), created_at: created(), updated_at: updated(),
  // Filled from the run on insert; close_no is the first close of this scope that saw the load to
  // the run's pinned warehouse loaded.
  scope: text(), close_no: bigint({ mode: 'number' }),
  // Set while the load to the run's pinned warehouse is terminally rejected; a repaired load clears it.
  rejected_at: timestamp({ withTimezone: true }),
}, t => [index('dump_run_idx').on(t.run_id), index('dump_cycle_idx').on(t.cycle_id),
  index('dump_unstamped_idx').on(t.scope).where(sql`${t.close_no} IS NULL AND ${t.kind} = 'output' AND ${t.quarantined_at} IS NULL AND ${t.rejected_at} IS NULL`),
  index('dump_scope_close_idx').on(t.scope, t.close_no).where(sql`${t.close_no} IS NOT NULL`)]);
export const load = control.table('load', {
  id: uuid().primaryKey().defaultRandom(), dump_id: uuid().notNull().references(() => dump.id),
  warehouse_id: uuid().notNull().references(() => warehouse.id), target_table: text().notNull(), op: loadOp().notNull().default('load'),
  generation: integer().notNull().default(1), status: loadStatus().notNull().default('pending'),
  repair_requested: boolean().notNull().default(false), claim_token: uuid(), claim_expires_at: timestamp({ withTimezone: true }),
  rows_inserted: bigint({ mode: 'bigint' }).notNull().default(sql`0`), loaded_at: timestamp({ withTimezone: true }),
  created_at: created(), updated_at: updated(),
}, t => [index('load_loaded_at_idx').on(t.loaded_at).where(sql`${t.status} = 'loaded'`),
  unique('load_dump_warehouse_table_unique').on(t.dump_id, t.warehouse_id, t.target_table)]);
