import { pgSchema } from 'drizzle-orm/pg-core';

export const control = pgSchema('control');
export const runStatus = control.enum('run_status', ['queued', 'running', 'succeeded', 'partial', 'failed', 'superseded']);
export const cycleStatus = control.enum('cycle_status', ['open', 'closed', 'superseded']);
export const batchStatus = control.enum('batch_status', ['queued', 'running', 'draining', 'succeeded', 'partial', 'failed']);
export const loadStatus = control.enum('load_status', ['pending', 'claimed', 'loaded', 'rejected']);
export const loadOp = control.enum('load_op', ['load', 'repair']);
export const dumpKind = control.enum('dump_kind', ['output', 'input', 'payload', 'rejected']);
export const runKind = control.enum('run_kind', ['export', 'close', 'invoke', 'repair', 'backfill', 'migrate', 'workbench', 'dbt']);
export const coverage = control.enum('coverage', ['full', 'partial', 'empty']);
export const budgetScope = control.enum('budget_scope', ['global', 'tenant', 'streamline', 'llm_step', 'provider']);
export const hardAction = control.enum('hard_action', ['warn', 'pause', 'degrade']);
export const costOrigin = control.enum('cost_origin', ['estimate', 'litellm', 'invoice']);
export const cycleInputPhase = control.enum('cycle_input_phase', ['bronze', 'derived']);
// D6: cycles closed before close stamps keep their per-cycle lists.
export const manifestMode = control.enum('manifest_mode', ['list', 'stamp']);
export const adapter = control.enum('adapter', ['postgres', 'snowflake']);
export const runner = control.enum('runner', ['cloud', 'core']);
export const alertSeverity = control.enum('alert_severity', ['info', 'warning', 'critical']);
export const workbenchRunKind = control.enum('workbench_run_kind', ['query', 'preview', 'backtest', 'explain']);
// PLAN leaves these vocabularies open; establish initial state sets here.
export const tenantStatus = control.enum('tenant_status', ['active', 'inactive']);
export const resolutionStatus = control.enum('resolution_status', ['pending', 'resolved', 'failed']);
export const rightsStatus = control.enum('rights_status', ['pending', 'approved', 'restricted', 'denied']);
