-- pg_stat_statements is preloaded by the image. Existing volumes need this bootstrap.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements WITH SCHEMA catalog;
REVOKE ALL ON catalog.pg_stat_statements,catalog.pg_stat_statements_info FROM PUBLIC,analyst_ro,explorer_ro,service_read,workbench_wh;
DO $$ DECLARE f record; BEGIN
  FOR f IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='catalog' AND p.proname LIKE 'pg_stat_statements%' LOOP
    EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,analyst_ro,explorer_ro,service_read,workbench_wh',f.signature);
  END LOOP;
END $$;
DROP TABLE IF EXISTS catalog.audit_offsets;
CREATE TABLE IF NOT EXISTS catalog.audit_counters (
  userid oid NOT NULL,queryid bigint NOT NULL,stats_since timestamptz NOT NULL,calls bigint NOT NULL,
  PRIMARY KEY(userid,queryid,stats_since)
);
REVOKE ALL ON catalog.audit_counters FROM PUBLIC,analyst_ro,explorer_ro,service_read,workbench_wh;
-- Remove the old per-login settings. No session may retain SQL statement logging after the restart.
DO $$ DECLARE r record; setting text; suffix text; BEGIN
  FOR r IN SELECT DISTINCT s.setrole,s.setdatabase FROM pg_db_role_setting s,unnest(s.setconfig) c
    WHERE split_part(c,'=',1) IN ('log_statement','log_min_duration_statement','log_min_duration_sample','log_duration','log_min_error_statement') LOOP
    FOREACH setting IN ARRAY ARRAY['log_statement','log_min_duration_statement','log_min_duration_sample','log_duration','log_min_error_statement'] LOOP
      IF r.setrole<>0 THEN
        suffix := CASE WHEN r.setdatabase=0 THEN '' ELSE format('IN DATABASE %I',(SELECT datname FROM pg_database WHERE oid=r.setdatabase)) END;
        EXECUTE format('ALTER ROLE %I %s RESET %I',pg_get_userbyid(r.setrole),suffix,setting);
      ELSIF r.setdatabase<>0 THEN
        EXECUTE format('ALTER DATABASE %I RESET %I',(SELECT datname FROM pg_database WHERE oid=r.setdatabase),setting);
      END IF;
    END LOOP;
  END LOOP;
  FOR r IN SELECT m.member FROM pg_auth_members m JOIN pg_roles pr ON pr.oid=m.member WHERE m.roleid='explorer_ro'::regrole AND pr.rolcanlogin LOOP
    EXECUTE format('GRANT explorer_ro TO %I WITH INHERIT TRUE, SET FALSE',pg_get_userbyid(r.member));
  END LOOP;
END $$;
COMMIT;
