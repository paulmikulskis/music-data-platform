import { sql } from 'drizzle-orm';
import { bigint, boolean, check, index, integer, jsonb, numeric, text, timestamp, unique, uniqueIndex, uuid } from 'drizzle-orm/pg-core';
import { adapter, budgetScope, control, hardAction, resolutionStatus, rightsStatus, runner, tenantStatus } from './enums.js';

const created = () => timestamp('created_at', { withTimezone: true }).notNull().defaultNow();
const updated = () => timestamp('updated_at', { withTimezone: true }).notNull().defaultNow();

// Exactly one production row at transaction completion: deferred trigger in 0002.
export const warehouse = control.table('warehouse', {
  id: uuid().primaryKey().defaultRandom(), adapter: adapter().notNull(), database: text().notNull(),
  dsn_secret_ref: text().notNull(), is_production: boolean().notNull().default(false),
  created_at: created(), updated_at: updated(),
}, t => [uniqueIndex('warehouse_one_production').on(t.is_production).where(sql`${t.is_production}`)]);
export const runnerMode = control.table('runner_mode', {
  id: boolean().primaryKey().default(true), runner: runner().notNull().default('cloud'),
  created_at: created(), updated_at: updated(),
}, t => [check('runner_mode_singleton', sql`${t.id}`)]);
export const tenant = control.table('tenant', {
  id: uuid().primaryKey().defaultRandom(), slug: text().notNull().unique(), name: text().notNull(),
  status: tenantStatus().notNull().default('active'), created_at: created(), updated_at: updated(),
});
export const budget = control.table('budget', {
  id: uuid().primaryKey().defaultRandom(), scope: budgetScope().notNull(), scope_id: uuid(), period: text().notNull(),
  cap_bytes: bigint({ mode: 'bigint' }),
  // A provider's vendor requests in the period; a vendor provider budget needs one (the runtime
  // treats a provider row without it as no row).
  cap_requests: bigint({ mode: 'bigint' }),
  cap_cents: bigint({ mode: 'bigint' }).notNull(), soft_pct: integer().notNull(), hard_action: hardAction().notNull(),
  ceiling_cents: bigint({ mode: 'bigint' }), raised_by: text(), raised_at: timestamp({ withTimezone: true }),
  created_at: created(), updated_at: updated(),
}, t => [check('budget_cap_bytes_check', sql`${t.cap_bytes} >= 0`),
  check('budget_cap_requests_check', sql`${t.cap_requests} >= 0`)]);
export const prompt = control.table('prompt', {
  id: uuid().primaryKey().defaultRandom(), name: text().notNull(), version: integer().notNull(), body: text().notNull(),
  created_at: created(),
}, t => [uniqueIndex("prompt_name_version_unique").on(t.name,t.version)]);
export const llmStep = control.table('llm_step', {
  id: uuid().primaryKey().defaultRandom(), source_key: text().notNull(), model: text().notNull(),
  prompt_id: uuid().references(() => prompt.id), prompt_version: integer(), params: jsonb().notNull().default({}),
  // step_version names the step's own model/prompt/params version; a run's config_version is separate.
  params_hash: text().notNull(), step_version: text().notNull(), litellm_key_alias: text(),
  // Expand and contract: the pre-0011 name, kept for services still on the previous image. A trigger
  // copies whichever one a writer leaves null. New code reads and writes step_version only; a later
  // release drops this column, its index and the trigger.
  config_version: text(),
  budget_id: uuid().references(() => budget.id), fallback_model: text(), enabled: boolean().notNull().default(true),
  created_at: created(), updated_at: updated(),
}, t => [uniqueIndex("llm_step_source_step_unique").on(t.source_key,t.step_version),
  uniqueIndex("llm_step_source_config_unique").on(t.source_key,t.config_version)]);
export const streamline = control.table('streamline', {
  id: uuid().primaryKey().defaultRandom(), source_key: text().notNull().unique(), layer: text().notNull(),
  tenant_bound: boolean().notNull().default(false), writes: text().array().notNull().default([]),
  reads: text().array().notNull().default([]), external: boolean().notNull().default(false),
  llm_step_id: uuid().references(() => llmStep.id), cadence_tag: text(), enabled: boolean().notNull().default(true),
  allow_partial: boolean().notNull().default(false), batch_size: integer().notNull().default(1),
  max_concurrency: integer().notNull().default(1), timeout_s: integer().notNull().default(300),
  transport_override: text(), proxy_provider: text(), proxy_country: text(),
  storage: text().notNull().default('heap'), acknowledged_fingerprints: text().array().notNull().default([]),
  created_at: created(), updated_at: updated(),
});
export const rightsSource = control.table('rights_source', {
  source_key: text().primaryKey(), provider: text().notNull(), category: text().notNull(), license_ref: text(),
  learning_eligible: boolean().notNull().default(false), resale_permitted: boolean().notNull().default(false),
  rights_status: rightsStatus().notNull().default('pending'), review_ref: text(),
  synced_at: timestamp({ withTimezone: true }).notNull().defaultNow(), created_at: created(), updated_at: updated(),
});
export const targetSet = control.table('target_set', {
  id: uuid().primaryKey().defaultRandom(), kind: text().notNull(), tenant_id: uuid().references(() => tenant.id),
  name: text().notNull(), created_at: created(), updated_at: updated(),
}, t => [unique('target_set_kind_tenant_unique').on(t.kind, t.tenant_id).nullsNotDistinct()]);
export const target = control.table('target', {
  id: uuid().primaryKey().defaultRandom(), target_set_id: uuid().notNull().references(() => targetSet.id),
  platform: text().notNull(), platform_account_id: text(), handle: text(), display_name: text(), role: text(),
  resolution_status: resolutionStatus().notNull().default('pending'), activated_at: timestamp({ withTimezone: true }),
  deactivated_at: timestamp({ withTimezone: true }), priority: integer().notNull().default(0),
  created_at: created(), updated_at: updated(),
}, t => [uniqueIndex('target_resolved_identity_unique').on(t.target_set_id, t.platform, t.platform_account_id)
  .where(sql`${t.resolution_status} = 'resolved'`),
  // Installed in custom migration 0002 alongside the deferred warehouse constraint.
  check('target_resolved_account_required', sql`(${t.resolution_status} <> 'resolved') OR (${t.platform_account_id} IS NOT NULL)`)]);
export const dbtJob = control.table('dbt_job', {
  job_id: text().primaryKey(), runner: runner().notNull(), cadence: text().notNull(), scope: text().notNull(),
  // Tenant jobs: the global raw tables their tenant models read, synced from the generated mdp_global_inputs().
  global_inputs: text().array().notNull().default([]),
  // The declared schedule: Core's due gate and dbt Cloud's cron read it. Control writes it;
  // registration fills it only for a new row. due_hour for daily and weekly, due_weekday (ISO) for weekly.
  due_hour: integer(), due_weekday: integer(), timezone: text().notNull().default('UTC'),
  created_at: created(), updated_at: updated(),
}, t => [check('dbt_job_due_hour', sql`${t.due_hour} BETWEEN 0 AND 23`),
  check('dbt_job_due_weekday', sql`${t.due_weekday} BETWEEN 1 AND 7`)]);
export const runbook = control.table('runbook', {
  id: uuid().primaryKey().defaultRandom(), slug: text().notNull().unique(), title: text().notNull(), body_md: text().notNull(),
  created_at: created(), updated_at: updated(),
});
export const auditLog = control.table('audit_log', {
  id: uuid().primaryKey().defaultRandom(), actor: text().notNull(), action: text().notNull(), subject: text().notNull(),
  before: jsonb(), after: jsonb(), at: timestamp({ withTimezone: true }).notNull().defaultNow(), created_at: created(),
}, t => [index('audit_log_probe_action_idx').on(t.action).where(sql`${t.action} = 'streamlines.probe'`)]);

export const targetSpec = control.table('target_spec', {
  target_id: uuid().primaryKey().references(() => target.id), resource_kind: text().notNull(),
  canonical_key: text().notNull(), params_json: jsonb().notNull().default({}),
  discovered_from_target_id: uuid().references(() => target.id), depth: integer().notNull().default(0),
  promotion_reason: text(),
}, t => [check('target_spec_resource_kind', sql`${t.resource_kind} IN ('account','curated','discovered','panel','hashtag','track','query','location','media','comment','playlist','curator','artist_page','hub','audio_asset','chart')`)]);
export const hostHealth = control.table('host_health', {
  host: text().primaryKey(), blocked_until: timestamp({ withTimezone: true }), last_signature: text(),
  host_rps: numeric().notNull().default('1'), http_version: text().notNull().default('auto'), user_agent: text(),
  robots_policy: text().notNull().default('respect'), robots_override_by: text(), updated_at: updated(),
}, t => [check('host_health_rate_positive', sql`${t.host_rps} > 0`),
  check('host_health_http_version', sql`${t.http_version} IN ('auto','1.1')`),
  check('host_health_robots_policy', sql`${t.robots_policy} IN ('respect','override')`),
  check('host_health_override_reason', sql`${t.robots_policy} <> 'override' OR length(trim(${t.robots_override_by})) > 0 AND ${t.robots_override_by} IS NOT NULL`)]);
