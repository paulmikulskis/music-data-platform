REVOKE ALL ON SCHEMA control FROM PUBLIC;
--> statement-breakpoint
GRANT USAGE ON SCHEMA control TO rights_sync, control_rt, functions_rt;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE ON control.rights_source TO rights_sync;
--> statement-breakpoint
GRANT SELECT ON ALL TABLES IN SCHEMA control TO control_rt, functions_rt;
--> statement-breakpoint
GRANT INSERT, UPDATE, DELETE ON
  control.warehouse, control.runner_mode, control.tenant, control.target_set,
  control.target, control.dbt_job, control.budget, control.llm_step,
  control.prompt, control.runbook, control.audit_log TO control_rt;
--> statement-breakpoint
GRANT UPDATE (enabled, allow_partial, batch_size, max_concurrency, timeout_s, storage, acknowledged_fingerprints)
  ON control.streamline TO control_rt;
--> statement-breakpoint
GRANT UPDATE (acknowledged_by, resolved_at) ON control.alert TO control_rt;
--> statement-breakpoint
GRANT UPDATE (reset_generation) ON control.cursor TO control_rt;
--> statement-breakpoint
GRANT INSERT, UPDATE ON
  control.cycle, control.cycle_attempt, control.cycle_input, control.target_export,
  control.target_export_member, control.run, control.run_attempt, control.batch,
  control.run_event, control.cursor, control.dump, control.load, control.dead_letter,
  control.budget_reservation, control.cost_ledger, control.call_ledger, control.alert,
  control.workbench_run TO functions_rt;
--> statement-breakpoint
GRANT INSERT ON control.streamline TO functions_rt;
--> statement-breakpoint
GRANT UPDATE (source_key, layer, tenant_bound, writes, reads, external, llm_step_id, cadence_tag)
  ON control.streamline TO functions_rt;
--> statement-breakpoint
GRANT USAGE ON SEQUENCE control.landed_seq TO functions_rt;
--> statement-breakpoint
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA control
  GRANT SELECT ON TABLES TO control_rt, functions_rt;
