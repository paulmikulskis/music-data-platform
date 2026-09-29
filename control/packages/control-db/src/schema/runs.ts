import { sql } from 'drizzle-orm';
import { bigint, boolean, index, integer, jsonb, numeric, text, timestamp, unique, uniqueIndex, uuid, type AnyPgColumn } from 'drizzle-orm/pg-core';
import { alertSeverity, batchStatus, control, costOrigin, coverage, runKind, runStatus } from './enums.js';
import { budget, llmStep, runbook, streamline, target, targetSet, tenant, warehouse } from './config.js';
import { cycle, targetExport } from './cycles.js';
import { dump } from './landing.js';
const created = () => timestamp('created_at', { withTimezone: true }).notNull().defaultNow();
const updated = () => timestamp('updated_at', { withTimezone: true }).notNull().defaultNow();
const count = () => bigint({ mode: 'bigint' }).notNull().default(sql`0`);
export const run = control.table('run', {
  id: uuid().primaryKey().defaultRandom(), kind: runKind().notNull(), work_key: text().notNull(),
  cycle_id: uuid().references(() => cycle.id), scope: text().notNull(), streamline_id: uuid().references(() => streamline.id),
  target_set_id: uuid().references(() => targetSet.id), revision_id: uuid().references(() => targetExport.id),
  tenant_id: uuid().references(() => tenant.id), warehouse_id: uuid().notNull().references(() => warehouse.id),
  parent_run_id: uuid().references((): AnyPgColumn => run.id), trace_id: text(), status: runStatus().notNull().default('queued'),
  expected_batches: integer(), resolved_config: jsonb(), config_version: text(), input_relation: text(), input_dump_id: uuid().references((): AnyPgColumn => dump.id), rows_written: count(), rows_rejected: count(),
  settled_at: timestamp({ withTimezone: true }),
  coverage: coverage(), cost_cents: count(), error_class: text(), error_message: text(), created_at: created(), updated_at: updated(),
}, t => [index('run_admitted_event_idx').on(t.created_at, sql`('run_admitted:' || ${t.id}::text) COLLATE "C"`),
  index('run_settled_event_idx').on(t.updated_at, sql`('run_settled:' || ${t.id}::text) COLLATE "C"`).where(sql`${t.status} IN ('succeeded','partial','failed','superseded')`),
  uniqueIndex('run_work_key_unique').on(t.work_key), index('run_cycle_idx').on(t.cycle_id),
  index('run_streamline_status_idx').on(t.streamline_id, t.status),
  index('run_streamline_created_idx').on(t.streamline_id, t.created_at)]);
export const runAttempt = control.table('run_attempt', {
  id: uuid().primaryKey().defaultRandom(), run_id: uuid().notNull().references(() => run.id), attempt_no: integer().notNull(),
  dbt_run_id: text(), deadline_at: timestamp({ withTimezone: true }).notNull(),
  started_at: timestamp({ withTimezone: true }).notNull().defaultNow(), ended_at: timestamp({ withTimezone: true }),
  status: runStatus().notNull().default('queued'), created_at: created(), updated_at: updated(),
}, t => [unique('run_attempt_run_number_unique').on(t.run_id, t.attempt_no)]);
export const batch = control.table('batch', {
  id: uuid().primaryKey().defaultRandom(), run_id: uuid().notNull().references(() => run.id), index: integer().notNull(),
  target_ids: uuid().array().notNull().default([]), status: batchStatus().notNull().default('queued'),
  attempt_id: uuid().references(() => runAttempt.id), worker_id: text(), lease_token: uuid(),
  lease_expires_at: timestamp({ withTimezone: true }), heartbeat_at: timestamp({ withTimezone: true }),
  // design: register every dump from a page emitting rows for several tables in
  // dump_ids and advance the cursor in the same transaction. dump_id is primary/legacy.
  dump_ids: uuid().array().notNull().default([]),
  dump_id: uuid().references((): AnyPgColumn => dump.id), last_part_uploaded: integer(), cursor_checkpoint: jsonb(),
  created_at: created(), updated_at: updated(),
}, t => [unique('batch_run_index_unique').on(t.run_id, t.index)]);
export const runEvent = control.table('run_event', {
  id: uuid().primaryKey().defaultRandom(), run_id: uuid().notNull().references(() => run.id),
  at: timestamp({ withTimezone: true }).notNull().defaultNow(), level: text().notNull(), event_type: text().notNull(),
  message: text().notNull(), attrs: jsonb().notNull().default({}), created_at: created(),
}, t => [index('run_event_run_at_idx').on(t.run_id, t.at)]);
// A PostgreSQL primary key cannot contain nullable target_id; this is the composite natural key.
export const cursor = control.table('cursor', {
  streamline_id: uuid().notNull().references(() => streamline.id), target_id: uuid().references(() => target.id),
  cursor_key: text().notNull(), cursor_value: jsonb(), version: bigint({ mode: 'bigint' }).notNull().default(sql`0`),
  reset_generation: bigint({ mode: 'bigint' }).notNull().default(sql`0`), dump_id: uuid().references((): AnyPgColumn => dump.id),
  created_at: created(), updated_at: updated(),
}, t => [unique('cursor_streamline_target_key_unique').on(t.streamline_id, t.target_id, t.cursor_key).nullsNotDistinct()]);
export const deadLetter = control.table('dead_letter', {
  id: uuid().primaryKey().defaultRandom(), run_id: uuid().notNull().references(() => run.id),
  streamline_id: uuid().references(() => streamline.id), target_id: uuid().references(() => target.id),
  reason: text().notNull(), payload_ref: text(), diff: jsonb(), first_seen_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
  resolved_at: timestamp({ withTimezone: true }), created_at: created(), updated_at: updated(),
});
export const budgetReservation = control.table('budget_reservation', {
  id: uuid().primaryKey().defaultRandom(), budget_id: uuid().notNull().references(() => budget.id),
  run_id: uuid().notNull().references(() => run.id), parent_reservation_id: uuid().references((): AnyPgColumn => budgetReservation.id),
  reserved_cents: count(), consumed_cents: count(), settled_at: timestamp({ withTimezone: true }),
  policy_snapshot: jsonb().notNull(), created_at: created(), updated_at: updated(),
}, t => [index('budget_reservation_run_idx').on(t.run_id)]);
export const costLedger = control.table('cost_ledger', {
  id: uuid().primaryKey().defaultRandom(), occurred_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
  tenant_id: uuid().references(() => tenant.id), streamline_id: uuid().references(() => streamline.id),
  run_id: uuid().references(() => run.id), llm_step_id: uuid().references(() => llmStep.id),
  vendor: text().notNull(), provider_request_id: text().notNull(), unit: text().notNull(), quantity: numeric().notNull(),
  cost_microcents: bigint({ mode: 'bigint' }).notNull().default(sql`0`),
  cost_cents: bigint({ mode: 'bigint' }).notNull(), origin: costOrigin().notNull(), is_current: boolean().notNull().default(true),
  created_at: created(), updated_at: updated(),
}, t => [index('cost_ledger_current_time_idx').on(t.occurred_at).where(sql`${t.is_current}`),
  unique('cost_ledger_vendor_request_origin_unique').on(t.vendor, t.provider_request_id, t.origin)]);
export const callLedger = control.table('call_ledger', {
  id: uuid().primaryKey().defaultRandom(), run_id: uuid().notNull().references(() => run.id),
  target_id: uuid().references(() => target.id), vendor: text().notNull(), endpoint: text().notNull(), request_id: text(),
  tier: text().notNull().default('direct'), provider: text(), bytes_in: count(), bytes_out: count(), block_signature: text(),
  http_status: integer(), duration_ms: integer(), attempt: integer().notNull(), cost_cents: count(), created_at: created(), updated_at: updated(),
}, t => [index('call_ledger_request_idx').on(t.request_id, t.id), index('call_ledger_run_idx').on(t.run_id, t.id)]);
export const alert = control.table('alert', {
  id: uuid().primaryKey().defaultRandom(), class: text().notNull(), severity: alertSeverity().notNull(),
  subject_type: text().notNull(), subject_id: text().notNull(), run_id: uuid().references(() => run.id),
  opened_at: timestamp({ withTimezone: true }).notNull().defaultNow(), acknowledged_by: text(), resolved_at: timestamp({ withTimezone: true }),
  resolved_by: text(), resolution_reason: text(),
  attempt_no: integer(),
  runbook_slug: text().references(() => runbook.slug), created_at: created(), updated_at: updated(),
}, t => [uniqueIndex('alert_run_attempt_class_unique').on(t.run_id, t.attempt_no, t.class)
  .where(sql`${t.subject_type} = 'run' AND ${t.attempt_no} IS NOT NULL`),
  index('alert_opened_event_idx').on(t.opened_at, sql`('alert_opened:' || ${t.id}::text) COLLATE "C"`),
  index('alert_resolved_event_idx').on(t.resolved_at, sql`('alert_resolved:' || ${t.id}::text) COLLATE "C"`).where(sql`${t.resolved_at} IS NOT NULL`),
  index('alert_subject_resolved_idx').on(t.subject_type, t.subject_id, t.resolved_at)]);
