-- Review 1b: cursor.reset_generation is control-owned; functions_rt keeps the service-owned columns only.
REVOKE INSERT, UPDATE ON control.cursor FROM functions_rt;
--> statement-breakpoint
GRANT INSERT (streamline_id, target_id, cursor_key, cursor_value, version, dump_id, created_at, updated_at) ON control.cursor TO functions_rt;
--> statement-breakpoint
GRANT UPDATE (cursor_value, version, dump_id, updated_at) ON control.cursor TO functions_rt;
--> statement-breakpoint
-- Review 1b: the exactly-one-production invariant must hold for existing state at install, not only for later writes.
-- A fresh database (zero rows) is allowed: ops/local/init.sh and the image init seed the first production row immediately after migrating,
-- and the service refuses admission with warehouse_unavailable while no production warehouse exists.
DO $$
DECLARE n integer; t integer;
BEGIN
  SELECT count(*) FILTER (WHERE is_production), count(*) INTO n, t FROM control.warehouse;
  IF t > 0 AND n <> 1 THEN
    RAISE EXCEPTION 'control.warehouse must have exactly one is_production row before this migration (found % of % rows)', n, t;
  END IF;
END $$;
