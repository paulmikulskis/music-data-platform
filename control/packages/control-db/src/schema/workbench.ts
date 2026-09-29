import { bigint, integer, jsonb, text, timestamp, uuid } from 'drizzle-orm/pg-core';
import { control, runStatus, workbenchRunKind } from './enums.js';
export const workbenchSession = control.table('workbench_session', {
  id: uuid().primaryKey().defaultRandom(), user_id: text().notNull(), scratch_schema: text().notNull().unique(),
  created_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
  last_used_at: timestamp({ withTimezone: true }).notNull().defaultNow(), expires_at: timestamp({ withTimezone: true }).notNull(),
  updated_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
});
export const workbenchRun = control.table('workbench_run', {
  id: uuid().primaryKey().defaultRandom(), session_id: uuid().notNull().references(() => workbenchSession.id),
  kind: workbenchRunKind().notNull(), input: jsonb().notNull(), compiled_sql: text(), result: jsonb(), error: jsonb(), status: runStatus().notNull().default('queued'),
  rows: bigint({ mode: 'bigint' }), duration_ms: integer(), artifact_ref: text(),
  created_at: timestamp({ withTimezone: true }).notNull().defaultNow(), updated_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
});
