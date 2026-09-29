import { sql } from 'drizzle-orm';
import { bigint, boolean, index, integer, jsonb, timestamp, unique, uniqueIndex, uuid, text, type AnyPgColumn } from 'drizzle-orm/pg-core';
import { control, cycleInputPhase, cycleStatus, manifestMode, runner } from './enums.js';
import { target, targetSet } from './config.js';
import { dump } from './landing.js';
const created = () => timestamp('created_at', { withTimezone: true }).notNull().defaultNow();
export const cycle = control.table('cycle', {
  id: uuid().primaryKey().defaultRandom(), cadence: text().notNull(), scope: text().notNull(),
  opened_at: timestamp({ withTimezone: true }).notNull().defaultNow(), opened_by_dbt_run_id: text().notNull(),
  closed_at: timestamp({ withTimezone: true }), status: cycleStatus().notNull().default('open'), git_sha: text(), image_digest: text(),
  // D6: close_no from scope_close (0 for list-mode cycles, which the previous image closed); tenant
  // cycles freeze the global mirrored close and declared global tables. The service inserts stamp;
  // the list default covers a cycle the previous image opens during a deploy.
  manifest_mode: manifestMode().notNull().default('list'), close_no: bigint({ mode: 'number' }),
  global_close_no: bigint({ mode: 'number' }), global_inputs: text().array().notNull().default([]),
  // the same tenant revisions on a replay.
  tenant_close_nos: jsonb().$type<Record<string, number>>().notNull().default({}),
  // Tenant daily cycles opened by a scheduled run: the ISO weekday of opened_at in the tenant's
  // timezone, frozen at bind. Null for manual and backfill cycles.
  local_weekday: integer(),
  // Tenant cycles: the tenant's timezone at bind, so a Replay reads the local week its original
  // run read (the weekly call record's call week). Null for global cycles.
  timezone: text(),
  created_at: created(), updated_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
}, t => [index('cycle_opened_event_idx').on(t.opened_at, sql`('cycle_opened:' || ${t.id}::text) COLLATE "C"`),
  index('cycle_closed_event_idx').on(t.closed_at, sql`('cycle_closed:' || ${t.id}::text) COLLATE "C"`).where(sql`${t.closed_at} IS NOT NULL`),
  index('cycle_cadence_scope_status_idx').on(t.cadence, t.scope, t.status),
  uniqueIndex('cycle_scope_close_no_unique').on(t.scope, t.close_no).where(sql`${t.manifest_mode} = 'stamp'`)]);
// Every close in a scope locks this row, so commit order equals close_no order across cadences.
export const scopeClose = control.table('scope_close', {
  scope: text().primaryKey(), last_close_no: bigint({ mode: 'number' }).notNull().default(0),
  // Advanced by the mirror catch-up only after raw.dump_stamps and raw.cycles through that number commit.
  mirrored_close_no: bigint({ mode: 'number' }).notNull().default(-1),
});
// cycle stores opener provenance; each retry records its own here.
export const cycleAttempt = control.table('cycle_attempt', {
  dbt_run_id: text().primaryKey(), cycle_id: uuid().notNull().references(() => cycle.id),
  git_sha: text(), image_digest: text(),
  runner: runner('runner'),
  job_id: text('job_id'),
  bound_at: timestamp({ withTimezone: true }).notNull().defaultNow(), reason_category: text().notNull(), created_at: created(),
  // True for binds from a close-stamp hook. Attempts bound before 0011 stay false, and their close
  // is refused with runner_outdated, so a pre-stamp runner never closes a cycle.
  stamp_protocol: boolean().notNull().default(false),
});
// One pending Replay restore per Core runner lock key: written before the Replay, cleared after its
// restore. The next run.sh under that lock runs a pending restore first. control_rt is the only writer.
export const runnerRestore = control.table('runner_restore', {
  lock_key: text().primaryKey(), cycle_id: uuid().notNull().references(() => cycle.id),
  target: text().notNull(), vars: jsonb().notNull().default({}),
  requested_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
});
export const cycleInput = control.table('cycle_input', {
  id: uuid().primaryKey().defaultRandom(), cycle_id: uuid().notNull().references(() => cycle.id),
  dump_id: uuid().notNull().references((): AnyPgColumn => dump.id), phase: cycleInputPhase().notNull(),
  added_at: timestamp({ withTimezone: true }).notNull().defaultNow(), created_at: created(),
  // Null until the row is in raw.cycle_inputs; the mirror scans only these.
  mirrored_at: timestamp({ withTimezone: true }),
}, t => [index('cycle_input_cycle_idx').on(t.cycle_id), index('cycle_input_dump_idx').on(t.dump_id),
  index('cycle_input_unmirrored_idx').on(t.cycle_id).where(sql`${t.mirrored_at} IS NULL`),
  unique('cycle_input_cycle_dump_unique').on(t.cycle_id, t.dump_id)]);
export const targetExport = control.table('target_export', {
  id: uuid().primaryKey().defaultRandom(), cycle_id: uuid().notNull().references(() => cycle.id),
  target_set_id: uuid().notNull().references(() => targetSet.id), taken_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
  member_count: integer().notNull(), created_at: created(),
}, t => [unique('target_export_cycle_set_unique').on(t.cycle_id, t.target_set_id)]);
export const targetExportMember = control.table('target_export_member', {
  id: uuid().primaryKey().defaultRandom(), revision_id: uuid().notNull().references(() => targetExport.id),
  target_id: uuid().notNull().references(() => target.id), created_at: created(),
  resource_kind: text(), canonical_key: text(), params_json: jsonb().notNull().default({}),
  target_json: jsonb().notNull().default({}), spec_recovered: boolean().notNull().default(true),
}, t => [unique('target_export_member_revision_target_unique').on(t.revision_id, t.target_id)]);
