import { bigint, boolean, doublePrecision, jsonb, text, timestamp } from 'drizzle-orm/pg-core';
import { control } from './enums.js';

// design Reference page: one row per reference source. The functions reference probe writes the
// mirror and landing state; control_rt writes only the re-import request.
export const referenceSource = control.table('reference_source', {
  source: text().primaryKey(),
  // The validated generation serving in the mirror, and its export.
  imported_generation: text(), export_date: timestamp({ withTimezone: true }),
  replication_sequence: bigint({ mode: 'number' }), imported_at: timestamp({ withTimezone: true }),
  // The newest import attempt: running, validated, promoted or failed, with its phase.
  import_generation: text(), import_state: text(), import_phase: text(),
  import_started_at: timestamp({ withTimezone: true }), import_finished_at: timestamp({ withTimezone: true }),
  import_message: text(),
  // The newest generation landed in the warehouse, and whether it reconciles.
  landed_generation: text(), landed_at: timestamp({ withTimezone: true }), landed_reconciled: boolean(),
  mirror_counts: jsonb().$type<Record<string, number>>(), landed_counts: jsonb().$type<Record<string, number>>(),
  disk_used_bytes: bigint({ mode: 'number' }), disk_total_bytes: bigint({ mode: 'number' }),
  // The closure trigger, as the newest mb_spine landing measured it: a generation's projected
  // write as a share of the pgdata volume, and the tracked recordings in the newest landed generation.
  closure_write_share: doublePrecision(), closure_recordings: bigint({ mode: 'number' }),
  closure_measured_at: timestamp({ withTimezone: true }),
  probed_at: timestamp({ withTimezone: true }), probe_error: text(),
  reimport_requested_at: timestamp({ withTimezone: true }), reimport_requested_by: text(),
  updated_at: timestamp({ withTimezone: true }).notNull().defaultNow(),
});
