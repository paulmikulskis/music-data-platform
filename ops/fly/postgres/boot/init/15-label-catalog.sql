-- Warehouse metadata. Generated definitions are installed by 16-label-definitions.sql.
BEGIN;
CREATE SCHEMA IF NOT EXISTS catalog;
REVOKE ALL ON SCHEMA catalog FROM PUBLIC;
GRANT USAGE ON SCHEMA catalog TO analyst_ro, explorer_ro, service_read, workbench_wh;
CREATE TABLE IF NOT EXISTS catalog.label_definitions (relation text PRIMARY KEY, labels jsonb NOT NULL);
REVOKE ALL ON catalog.label_definitions FROM PUBLIC, analyst_ro, explorer_ro, service_read, workbench_wh;
CREATE TABLE IF NOT EXISTS catalog.query_audit (
  event_id text PRIMARY KEY, actor text NOT NULL, occurred_at timestamptz NOT NULL,
  channel text NOT NULL, query_hash text NOT NULL, tenants jsonb NOT NULL,
  labels jsonb NOT NULL, cross_tenant boolean NOT NULL, unresolved boolean NOT NULL
);
REVOKE ALL ON catalog.query_audit FROM PUBLIC, analyst_ro, explorer_ro, workbench_wh;
GRANT SELECT ON catalog.query_audit TO service_read;

CREATE SEQUENCE IF NOT EXISTS catalog.audit_slot;
DELETE FROM catalog.query_audit WHERE event_id IN (
  SELECT event_id FROM catalog.query_audit ORDER BY occurred_at DESC,event_id DESC OFFSET 99999);
ALTER TABLE catalog.query_audit ADD COLUMN IF NOT EXISTS slot bigint NOT NULL DEFAULT (nextval('catalog.audit_slot') % 100000);
CREATE UNIQUE INDEX IF NOT EXISTS query_audit_slot ON catalog.query_audit(slot);
CREATE INDEX IF NOT EXISTS query_audit_time ON catalog.query_audit(occurred_at);
CREATE OR REPLACE FUNCTION catalog.bound_query_audit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('catalog.query_audit.bound'));
  DELETE FROM catalog.query_audit WHERE occurred_at < now()-interval '30 days';
  DELETE FROM catalog.query_audit WHERE slot=NEW.slot;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION catalog.bound_query_audit() FROM PUBLIC,service_read;
CREATE OR REPLACE TRIGGER query_audit_bound BEFORE INSERT ON catalog.query_audit
FOR EACH ROW EXECUTE FUNCTION catalog.bound_query_audit();

CREATE OR REPLACE FUNCTION catalog.label_schema(schema_name text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE tenant text; label jsonb;
BEGIN
  IF schema_name LIKE 'pg\_%' OR schema_name IN ('information_schema','control') THEN RETURN; END IF;
  IF mdp.is_sandbox(schema_name) THEN RETURN; END IF;
  tenant := substring(schema_name FROM '^tenant_(.+)_(?:staging|intermediate|marts)$');
  label := jsonb_build_object('layer',CASE WHEN schema_name IN ('raw','explore_raw','staging') OR schema_name LIKE '%\_staging' THEN 'bronze' ELSE 'silver' END,
    'category','personal','tenant',coalesce(tenant,'global'),'learning',false,'resale',false,
    'licence_status','unverified','description','Container labels are conservative. Read each relation label before reuse.');
  IF obj_description((SELECT oid FROM pg_namespace WHERE nspname=schema_name),'pg_namespace') IS DISTINCT FROM label::text THEN
    EXECUTE format('COMMENT ON SCHEMA %I IS %L',schema_name,label::text);
  END IF;
END $$;
REVOKE ALL ON FUNCTION catalog.label_schema(text) FROM PUBLIC, service_read;

CREATE OR REPLACE FUNCTION catalog.label_relation(rel oid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; t text; kind "char"; family text; physical_schema text; tenant text; label jsonb; col record;
BEGIN
  SELECT n.nspname,c.relname,c.relkind INTO s,t,kind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE c.oid=rel AND c.relkind IN ('r','p','v','m','f');
  IF s IS NULL OR s LIKE 'pg\_%' OR s IN ('information_schema','control') THEN RETURN; END IF;
  physical_schema := s;
  IF s LIKE 'explore\_%' THEN
    SELECT n.nspname INTO family FROM pg_rewrite rw JOIN pg_depend d ON d.objid=rw.oid AND d.classid='pg_rewrite'::regclass
      JOIN pg_class c ON c.oid=d.refobjid JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE rw.ev_class=rel AND d.refclassid='pg_class'::regclass AND c.oid<>rel AND n.nspname<>'mdp' LIMIT 1;
    s := coalesce(family,substring(s FROM 9));
  END IF;
  tenant := substring(s FROM '^tenant_(.+)_(?:staging|intermediate|marts)$');
  family := CASE WHEN tenant IS NOT NULL THEN regexp_replace(s,'^tenant_.+_(staging|intermediate|marts)$','tenant_*_\1')
                 WHEN s='explore_raw' THEN 'raw' ELSE s END;
  SELECT labels INTO label FROM catalog.label_definitions WHERE relation=family||'.'||t;
  label := coalesce(label, '{"layer":"bronze","category":"personal","tenant":"unknown","grain":[],"learning":false,"resale":false,"licence_status":"unverified","description":"Unclassified relation; review before reuse."}'::jsonb);
  IF tenant IS NOT NULL THEN label := label || jsonb_build_object('tenant',tenant,'learning',false); END IF;
  IF mdp.is_sandbox(physical_schema) THEN
    label := label || jsonb_build_object('layer','sandbox','tenant',CASE WHEN EXISTS (
      SELECT 1 FROM mdp.sandbox_state st WHERE st.schema_name=physical_schema AND st.is_explorer) THEN 'unknown' ELSE 'global' END,
      'derived_from',coalesce((SELECT jsonb_agg(DISTINCT n.nspname||'.'||c.relname)
        FROM pg_rewrite rw JOIN pg_depend d ON d.objid=rw.oid AND d.classid='pg_rewrite'::regclass
        JOIN pg_class c ON c.oid=d.refobjid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE rw.ev_class=rel AND d.refclassid='pg_class'::regclass AND c.oid<>rel),'[]'::jsonb),
      'description','Sandbox copy; derived inputs may be unrecorded. Keep rights columns and review input labels before reuse.');
  END IF;
  EXECUTE format('COMMENT ON %s %I.%I IS %L', CASE kind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END,physical_schema,t,(label-'columns')::text);
  FOR col IN SELECT attname FROM pg_attribute WHERE attrelid=rel AND attnum>0 AND NOT attisdropped LOOP
    EXECUTE format('COMMENT ON COLUMN %I.%I.%I IS %L',physical_schema,t,col.attname,
      ((label-'columns') || jsonb_build_object('description',coalesce(label->'columns'->>col.attname,replace(col.attname,'_',' '))))::text);
  END LOOP;

END $$;
REVOKE ALL ON FUNCTION catalog.label_relation(oid) FROM PUBLIC, service_read;
CREATE OR REPLACE FUNCTION catalog.refresh_labels() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT nspname FROM pg_namespace LOOP PERFORM catalog.label_schema(r.nspname); END LOOP;
  FOR r IN SELECT oid FROM pg_class WHERE relkind IN ('r','p','v','m','f') LOOP PERFORM catalog.label_relation(r.oid); END LOOP;
END $$;
REVOKE ALL ON FUNCTION catalog.refresh_labels() FROM PUBLIC, service_read;
CREATE OR REPLACE FUNCTION catalog.label_ddl() RETURNS event_trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    IF r.classid='pg_namespace'::regclass THEN
      PERFORM catalog.label_schema((SELECT nspname FROM pg_namespace WHERE oid=r.objid));
    ELSIF r.classid='pg_class'::regclass THEN
      PERFORM catalog.label_relation(r.objid);
    END IF;
  END LOOP;
END $$;
REVOKE ALL ON FUNCTION catalog.label_ddl() FROM PUBLIC, service_read;
DROP EVENT TRIGGER IF EXISTS mdp_relation_labels;
CREATE EVENT TRIGGER mdp_relation_labels ON ddl_command_end
  WHEN TAG IN ('CREATE SCHEMA','CREATE TABLE','CREATE TABLE AS','SELECT INTO','ALTER TABLE','CREATE VIEW','CREATE MATERIALIZED VIEW','CREATE FOREIGN TABLE','ALTER FOREIGN TABLE')
  EXECUTE FUNCTION catalog.label_ddl();

CREATE OR REPLACE VIEW catalog.relations AS
SELECT n.nspname AS schema, c.relname AS name,
       labels->>'layer' AS layer, labels->>'category' AS category,
       labels->>'tenant' AS tenant, labels->'grain' AS grain,
       coalesce(labels->'learning'='true'::jsonb,false) AS learning,
       coalesce(labels->'resale'='true'::jsonb,false) AS resale,
       labels->>'licence_status' AS licence_status, labels->>'description' AS description,
       has_schema_privilege(current_user,n.oid,'USAGE') AND has_table_privilege(current_user,c.oid,'SELECT') AS readable,
       labels
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
CROSS JOIN LATERAL (SELECT CASE WHEN pg_input_is_valid(obj_description(c.oid,'pg_class'),'jsonb')
  THEN obj_description(c.oid,'pg_class')::jsonb ELSE '{"category":"personal","learning":false,"resale":false,"tenant":"unknown","licence_status":"unverified"}'::jsonb END AS labels) l
WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT LIKE 'pg\_%' AND n.nspname NOT IN ('information_schema','control');
GRANT SELECT ON catalog.relations TO analyst_ro, explorer_ro, service_read, workbench_wh;
CREATE OR REPLACE VIEW catalog.denials AS
SELECT DISTINCT schema,
  CASE schema WHEN 'raw' THEN 'Raw is bronze as landed. Staff read explore_raw.<table>; analysts read explore_staging.stg_<source>__<table>.'
    WHEN 'reference' THEN 'Source rights appear in catalog.relations. Staff read explore_reference.<table>.'
    ELSE 'Use explore_'||schema||'.<table>. Unknown or private fields are null; tenant copies need a staff explore login.' END AS readable_copy
FROM catalog.relations WHERE NOT readable AND schema NOT IN ('mdp','control')
UNION ALL SELECT 'mdp','Internal invocation plumbing. Use the workbench to query data.'
UNION ALL SELECT 'control','Private service configuration. Use the operator console.';
GRANT SELECT ON catalog.denials TO analyst_ro, explorer_ro, service_read, workbench_wh;
COMMIT;
