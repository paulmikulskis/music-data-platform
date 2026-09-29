-- Control boundaries: control owns session lifecycle; warehouses change only through migrations.
GRANT INSERT, UPDATE, DELETE ON control.workbench_session TO control_rt;
--> statement-breakpoint
REVOKE INSERT, UPDATE, DELETE ON control.warehouse FROM control_rt;
--> statement-breakpoint
-- Control boundaries: prompt publication is INSERT-only through the PR workflow.
REVOKE UPDATE, DELETE ON control.prompt FROM control_rt;
--> statement-breakpoint
-- Control boundaries: registry INSERT cannot override control-owned knob defaults.
REVOKE INSERT ON control.streamline FROM functions_rt;
--> statement-breakpoint
GRANT INSERT (source_key, layer, tenant_bound, writes, reads, external, llm_step_id, cadence_tag)
  ON control.streamline TO functions_rt;
--> statement-breakpoint
-- Control boundaries: only control may acknowledge or resolve an alert.
REVOKE INSERT, UPDATE ON control.alert FROM functions_rt;
--> statement-breakpoint
GRANT INSERT (id, class, severity, subject_type, subject_id, run_id, opened_at, runbook_slug, created_at, updated_at),
  UPDATE (id, class, severity, subject_type, subject_id, run_id, opened_at, runbook_slug, created_at, updated_at)
  ON control.alert TO functions_rt;
--> statement-breakpoint
-- Control boundaries: resolved identities cannot bypass uniqueness through a NULL account.
ALTER TABLE control.target ADD CONSTRAINT target_resolved_account_required
  CHECK ((resolution_status <> 'resolved') OR (platform_account_id IS NOT NULL));
--> statement-breakpoint
-- Control boundaries: the partial unique index supplies the upper bound; this deferred trigger
-- supplies the lower bound and permits clearing the old flag before setting the new.
CREATE FUNCTION control.require_production_warehouse() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, control AS $$
BEGIN
  IF (SELECT count(*) FROM control.warehouse WHERE is_production) <> 1 THEN
    RAISE EXCEPTION 'exactly one production warehouse is required'
      USING ERRCODE = '23514', CONSTRAINT = 'warehouse_exactly_one_production';
  END IF;
  RETURN NULL;
END
$$;
--> statement-breakpoint
CREATE CONSTRAINT TRIGGER warehouse_exactly_one_production
AFTER INSERT OR UPDATE OR DELETE ON control.warehouse
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION control.require_production_warehouse();
--> statement-breakpoint
-- TRUNCATE does not fire row constraint triggers; forbid that invariant bypass.
CREATE FUNCTION control.prevent_warehouse_truncate() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'warehouse must retain exactly one production row'
    USING ERRCODE = '23514', CONSTRAINT = 'warehouse_exactly_one_production';
END
$$;
--> statement-breakpoint
CREATE TRIGGER warehouse_no_truncate BEFORE TRUNCATE ON control.warehouse
FOR EACH STATEMENT EXECUTE FUNCTION control.prevent_warehouse_truncate();
