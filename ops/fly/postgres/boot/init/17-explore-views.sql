-- One projection builder serves bootstrap and loader widening. Policies come from declarations.
BEGIN;
CREATE OR REPLACE FUNCTION catalog.refresh_explore(raw_table regclass) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; t text; labels jsonb; col record; mode text; expression text; row_filter text; projections text[] := '{}';
BEGIN
  SELECT n.nspname,c.relname INTO s,t FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=raw_table;
  IF s IS DISTINCT FROM 'raw' THEN RAISE EXCEPTION 'Explore projections require a raw relation'; END IF;
  SELECT d.labels INTO labels FROM catalog.label_definitions d WHERE relation='raw.'||t;
  IF labels IS NULL THEN RETURN; END IF;
  FOR col IN SELECT attname,format_type(atttypid,atttypmod) AS type FROM pg_attribute
    WHERE attrelid=raw_table AND attnum>0 AND NOT attisdropped ORDER BY attnum LOOP
    mode := labels->'privacy'->>col.attname;
    expression := CASE mode
      WHEN 'non_personal' THEN format('%I',col.attname)
      WHEN 'pseudonym' THEN format('CASE WHEN %1$I IS NOT NULL THEN encode(sha256(convert_to((SELECT key FROM mdp.pseudonym_key)||'':''||%1$I::text,''UTF8'')),''hex'') END',col.attname)
      ELSE format('NULL::%s',col.type) END;
    projections := array_append(projections,format('%s AS %I',expression,col.attname));
  END LOOP;
  row_filter := coalesce(labels->>'explore_filter','true');
  -- A partially bootstrapped warehouse cannot establish membership. Keep its copy empty.
  IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(labels->'explore_filter_relations') dependency
    WHERE to_regclass(dependency) IS NULL) THEN row_filter := 'false'; END IF;
  EXECUTE format('CREATE OR REPLACE VIEW explore_raw.%I WITH (security_barrier=true) AS SELECT %s FROM raw.%I src WHERE (%s)',
    t,array_to_string(projections,','),t,row_filter);
  EXECUTE format('ALTER VIEW explore_raw.%I OWNER TO dbt_transform',t);
  EXECUTE format('GRANT SELECT ON explore_raw.%I TO explorer_ro,workbench_wh',t);
END $$;
REVOKE ALL ON FUNCTION catalog.refresh_explore(regclass) FROM PUBLIC,service_read;
-- The loader can only remove the generated projection of its own raw table. No CASCADE.
CREATE OR REPLACE FUNCTION catalog.prepare_explore_widen(raw_table regclass) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; t text; present boolean;
BEGIN
  SELECT n.nspname,c.relname INTO s,t FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=raw_table;
  IF s IS DISTINCT FROM 'raw' THEN RAISE EXCEPTION 'Explore widening requires a raw relation'; END IF;
  present := to_regclass(format('explore_raw.%I',t)) IS NOT NULL;
  IF present THEN EXECUTE format('DROP VIEW explore_raw.%I',t); END IF;
  RETURN present;
END $$;
REVOKE ALL ON FUNCTION catalog.prepare_explore_widen(regclass) FROM PUBLIC,service_read;
GRANT USAGE ON SCHEMA catalog TO loader_wh;
GRANT EXECUTE ON FUNCTION catalog.prepare_explore_widen(regclass),catalog.refresh_explore(regclass) TO loader_wh;
CREATE SCHEMA IF NOT EXISTS explore_raw AUTHORIZATION dbt_transform;
GRANT USAGE ON SCHEMA explore_raw TO explorer_ro,workbench_wh;
-- Staff only read projections whose every column has a reviewed declaration.
CREATE OR REPLACE FUNCTION catalog.explore_schema(original text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
 SELECT CASE WHEN length(original)<=55 THEN 'explore_'||original
 ELSE 'explore_'||left(original,45)||'_'||left(md5(original),8) END
$$;
REVOKE ALL ON FUNCTION catalog.explore_schema(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION catalog.explore_schema(text) TO analyst_ro,explorer_ro,workbench_wh,service_read;

CREATE OR REPLACE FUNCTION catalog.share_relation(rel oid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE s text; t text; family text; policy jsonb; col record; cols text;
  source_owner text; safe boolean := true; projections text[] := '{}'; expression text; mode text; copy_schema text;
BEGIN
  SELECT n.nspname,c.relname INTO s,t FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE c.oid=rel AND c.relkind IN ('r','p','v','m','f');
  IF mdp.is_sandbox(s) THEN RETURN; END IF;
  IF s IS NULL OR s LIKE 'pg\_%' OR s LIKE 'wb\_%'
    OR s IN ('raw','catalog','mdp','control','information_schema') THEN RETURN; END IF;
  -- Column ACLs survive column renames and table-level revocations.
  SELECT string_agg(quote_ident(attname),',') INTO cols FROM pg_attribute
    WHERE attrelid=rel AND attnum>0 AND NOT attisdropped;
  EXECUTE format('REVOKE SELECT ON %I.%I FROM analyst_ro,explorer_ro,workbench_wh',s,t);
  IF cols IS NOT NULL THEN
    EXECUTE format('REVOKE SELECT (%s) ON %I.%I FROM analyst_ro,explorer_ro,workbench_wh',cols,s,t);
  END IF;
  IF s LIKE 'explore\_%' THEN RETURN; END IF;
  IF cols IS NULL THEN RETURN; END IF;
  family := regexp_replace(s,'^tenant_.+_(staging|intermediate|marts)$','tenant_*_\1');
  SELECT labels->'shared_privacy' INTO policy FROM catalog.label_definitions
    WHERE relation=family||'.'||regexp_replace(t,'__dbt_.*$','');
  IF EXISTS (SELECT 1 FROM jsonb_each_text(coalesce(policy,'{}')) WHERE value='private') THEN
    EXECUTE format('REVOKE SELECT ON %I.%I FROM service_read,reader_wh',s,t);
    EXECUTE format('REVOKE SELECT (%s) ON %I.%I FROM service_read,reader_wh',cols,s,t);
  END IF;
  IF t LIKE '%\_\_dbt\_%' THEN RETURN; END IF;
  FOR col IN SELECT attname,format_type(atttypid,atttypmod) AS type FROM pg_attribute
    WHERE attrelid=rel AND attnum>0 AND NOT attisdropped ORDER BY attnum LOOP
    mode := policy->>col.attname;
    IF mode IS NULL OR mode NOT IN ('non_personal','pseudonym','omitted') THEN safe := false; END IF;
    -- An omitted column is null even when an old model still stores its former values.
    expression := CASE WHEN mode IN ('non_personal','pseudonym') THEN quote_ident(col.attname)
      ELSE format('NULL::%s',col.type) END;
    projections := array_append(projections,format('%s AS %I',expression,col.attname));
  END LOOP;
  -- Canonical shared reads are limited to entirely non-personal relations. Masked fields always
  -- use the generated copy, which protects old stored tables before their next rebuild too.
  safe := safe AND NOT EXISTS (SELECT 1 FROM jsonb_each_text(coalesce(policy,'{}')) WHERE value<>'non_personal');
  IF safe THEN
    EXECUTE format('GRANT SELECT ON %I.%I TO explorer_ro,workbench_wh',s,t);
    IF s IN ('staging','intermediate','marts') THEN EXECUTE format('GRANT SELECT ON %I.%I TO analyst_ro',s,t); END IF;
  END IF;
  -- Analysts recheck model inputs without access to reference tables or control.
  IF s = 'reference' AND t = 'rights_registry' THEN
    EXECUTE 'CREATE OR REPLACE VIEW catalog.learning_rights AS SELECT source_key, learning_eligible, resale_permitted FROM reference.rights_registry';
    EXECUTE 'GRANT SELECT ON catalog.learning_rights TO analyst_ro,explorer_ro';
  END IF;
  SELECT pg_get_userbyid(relowner) INTO source_owner FROM pg_class WHERE oid=rel;
  copy_schema := catalog.explore_schema(s);
  IF to_regnamespace(copy_schema) IS NULL THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('explore schema:'||copy_schema,0));
    IF to_regnamespace(copy_schema) IS NULL THEN EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION dbt_transform',copy_schema); END IF;
  END IF;
  IF NOT has_schema_privilege('explorer_ro',copy_schema,'USAGE') THEN EXECUTE format('GRANT USAGE ON SCHEMA %I TO explorer_ro',copy_schema); END IF;
  IF NOT has_schema_privilege('workbench_wh',copy_schema,'USAGE') THEN EXECUTE format('GRANT USAGE ON SCHEMA %I TO workbench_wh',copy_schema); END IF;
  BEGIN
    EXECUTE format('CREATE OR REPLACE VIEW %I.%I WITH (security_barrier=true) AS SELECT %s FROM %I.%I',copy_schema,t,array_to_string(projections,','),s,t);
  EXCEPTION WHEN invalid_table_definition THEN
    -- A rebuilt model can reorder or change columns. Replace its generated copy atomically.
    -- No CASCADE: an independently owned dependent relation is never removed here.
    EXECUTE format('DROP VIEW %I.%I',copy_schema,t);
    EXECUTE format('CREATE VIEW %I.%I WITH (security_barrier=true) AS SELECT %s FROM %I.%I',copy_schema,t,array_to_string(projections,','),s,t);
  END;
  EXECUTE format('ALTER VIEW %I.%I OWNER TO %I',copy_schema,t,source_owner);
  EXECUTE format('GRANT SELECT ON %I.%I TO explorer_ro,workbench_wh',copy_schema,t);
  IF s IN ('staging','intermediate','marts') THEN
    IF NOT has_schema_privilege('analyst_ro',copy_schema,'USAGE') THEN EXECUTE format('GRANT USAGE ON SCHEMA %I TO analyst_ro',copy_schema); END IF;
    EXECUTE format('GRANT SELECT ON %I.%I TO analyst_ro',copy_schema,t);
  END IF;
END $$;
REVOKE ALL ON FUNCTION catalog.share_relation(oid) FROM PUBLIC,service_read;
-- Reconcile old direct grants before accepting a staff login.
SELECT catalog.share_relation(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname NOT LIKE 'explore\_%'
 AND n.nspname NOT IN ('information_schema','control','mdp','raw','catalog');

COMMIT;
