import postgres from 'postgres';
import { drizzle } from 'drizzle-orm/postgres-js';
import { createSelectSchema } from 'drizzle-zod';
import * as schema from './schema/index.js';

export * from './schema/index.js';
export { schema };
export function createControlDb(url: string) {
  const client = postgres(url);
  return drizzle(client, { schema });
}

export const selectSchemas = {
  api_key: createSelectSchema(schema.apiKey),
  run: createSelectSchema(schema.run),
  run_attempt: createSelectSchema(schema.runAttempt),
  batch: createSelectSchema(schema.batch),
  run_event: createSelectSchema(schema.runEvent),
  cursor: createSelectSchema(schema.cursor),
  dead_letter: createSelectSchema(schema.deadLetter),
  budget_reservation: createSelectSchema(schema.budgetReservation),
  cost_ledger: createSelectSchema(schema.costLedger),
  call_ledger: createSelectSchema(schema.callLedger),
  alert: createSelectSchema(schema.alert),
  cycle: createSelectSchema(schema.cycle),
  cycle_attempt: createSelectSchema(schema.cycleAttempt),
  cycle_input: createSelectSchema(schema.cycleInput),
  scope_close: createSelectSchema(schema.scopeClose),
  target_export: createSelectSchema(schema.targetExport),
  target_export_member: createSelectSchema(schema.targetExportMember),
  workbench_session: createSelectSchema(schema.workbenchSession),
  workbench_run: createSelectSchema(schema.workbenchRun),
  dump: createSelectSchema(schema.dump),
  load: createSelectSchema(schema.load),
  warehouse: createSelectSchema(schema.warehouse),
  runner_mode: createSelectSchema(schema.runnerMode),
  tenant: createSelectSchema(schema.tenant),
  budget: createSelectSchema(schema.budget),
  prompt: createSelectSchema(schema.prompt),
  llm_step: createSelectSchema(schema.llmStep),
  streamline: createSelectSchema(schema.streamline),
  rights_source: createSelectSchema(schema.rightsSource),
  target_set: createSelectSchema(schema.targetSet),
  target: createSelectSchema(schema.target),
  dbt_job: createSelectSchema(schema.dbtJob),
  runbook: createSelectSchema(schema.runbook),
  audit_log: createSelectSchema(schema.auditLog),
  reference_source: createSelectSchema(schema.referenceSource),
};
