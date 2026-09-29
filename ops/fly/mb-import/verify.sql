-- Validate one finalized generation database. Exits nonzero on any failed check; the
-- importer sets mdp.generation.validated_at only after this passes. Run connected to the generation
-- database. Inputs: generation (the export stamp), expected_schema_sequence (the image's
-- MUSICBRAINZ_DB_SCHEMA_SEQUENCE).
\set ON_ERROR_STOP on
\pset pager off
\if :{?generation}
\else
\echo 'verify.sql needs -v generation=<export stamp>'
SELECT 1/0 AS generation_input_missing;
\endif
\if :{?expected_schema_sequence}
\else
\echo 'verify.sql needs -v expected_schema_sequence=<n>'
SELECT 1/0 AS schema_sequence_input_missing;
\endif
SELECT set_config('mdp.generation', :'generation', false),
       set_config('mdp.expected_schema_sequence', :'expected_schema_sequence', false) \g /dev/null

SELECT version();
SELECT now() AS verified_at, current_database() AS database,
       pg_size_pretty(pg_database_size(current_database())) AS database_size;
SELECT * FROM musicbrainz.replication_control;
CREATE TEMP TABLE mdp_verify_counts AS
SELECT 'recording' AS table_name, count(*) AS row_count FROM musicbrainz.recording
UNION ALL SELECT 'isrc', count(*) FROM musicbrainz.isrc
UNION ALL SELECT 'url', count(*) FROM musicbrainz.url
UNION ALL SELECT 'l_recording_url', count(*) FROM musicbrainz.l_recording_url
UNION ALL SELECT 'l_release_url', count(*) FROM musicbrainz.l_release_url
UNION ALL SELECT 'l_artist_url', count(*) FROM musicbrainz.l_artist_url
UNION ALL SELECT 'release', count(*) FROM musicbrainz.release
UNION ALL SELECT 'track', count(*) FROM musicbrainz.track
UNION ALL SELECT 'artist', count(*) FROM musicbrainz.artist
UNION ALL SELECT 'artist_credit', count(*) FROM musicbrainz.artist_credit;
SELECT * FROM mdp_verify_counts ORDER BY table_name;
SELECT generation, export_date, replication_sequence, schema_sequence, imported_at, validated_at
FROM mdp.generation;

DO $$
DECLARE
  g mdp.generation;
  missing text;
BEGIN
  IF EXISTS (SELECT FROM mdp_verify_counts WHERE row_count = 0) THEN
    RAISE EXCEPTION 'reference_import_failed: required identity table is empty: %',
      (SELECT string_agg(table_name, ', ') FROM mdp_verify_counts WHERE row_count = 0);
  END IF;
  IF (SELECT count(*) FROM musicbrainz.replication_control) <> 1 OR EXISTS (
    SELECT FROM musicbrainz.replication_control WHERE current_replication_sequence IS NULL
      OR current_schema_sequence <> current_setting('mdp.expected_schema_sequence')::int) THEN
    RAISE EXCEPTION 'reference_import_failed: replication_control is not one row at schema sequence %',
      current_setting('mdp.expected_schema_sequence');
  END IF;

  IF (SELECT count(*) FROM mdp.generation) <> 1 THEN
    RAISE EXCEPTION 'reference_import_failed: mdp.generation must hold exactly one row';
  END IF;
  SELECT * INTO g FROM mdp.generation;
  IF g.generation <> current_setting('mdp.generation') THEN
    RAISE EXCEPTION 'reference_import_failed: mdp.generation holds %, expected %', g.generation, current_setting('mdp.generation');
  END IF;
  IF g.replication_sequence <> (SELECT current_replication_sequence FROM musicbrainz.replication_control)
     OR g.schema_sequence <> (SELECT current_schema_sequence FROM musicbrainz.replication_control) THEN
    RAISE EXCEPTION 'reference_import_failed: mdp.generation sequences differ from replication_control';
  END IF;
  -- The recorded counts reconcile with the tables, key by key.
  SELECT string_agg(c.table_name, ', ') INTO missing FROM mdp_verify_counts c
  WHERE (g.counts ->> c.table_name)::bigint IS DISTINCT FROM c.row_count;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'reference_import_failed: mdp.generation counts do not reconcile for %', missing;
  END IF;

  SELECT string_agg(i, ', ') INTO missing
  FROM unnest(ARRAY['url_idx_mdp_tail_id', 'artist_credit_idx_mdp_name_trgm']) AS i
  WHERE NOT EXISTS (SELECT FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'musicbrainz' AND c.relname = i AND x.indisvalid);
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'reference_import_failed: missing or invalid index %', missing;
  END IF;

  IF NOT has_database_privilege('mb_reader', current_database(), 'CONNECT')
     OR NOT has_table_privilege('mb_reader', 'mdp.generation', 'SELECT')
     OR NOT has_function_privilege('mb_reader', 'mdp.url_tail_id(text)', 'EXECUTE')
     OR NOT coalesce(has_function_privilege('mb_reader', (SELECT p.oid FROM pg_proc p JOIN pg_depend d ON d.objid = p.oid
          JOIN pg_extension e ON e.oid = d.refobjid WHERE e.extname = 'pg_trgm' AND p.proname = 'similarity'), 'EXECUTE'), false) THEN
    RAISE EXCEPTION 'reference_import_failed: reader lacks CONNECT, mdp.generation, url_tail_id or similarity';
  END IF;
  IF EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('musicbrainz', 'mdp') AND c.relkind IN ('r', 'p') AND (
      has_table_privilege('mb_reader', c.oid, 'INSERT') OR has_table_privilege('mb_reader', c.oid, 'UPDATE')
      OR has_table_privilege('mb_reader', c.oid, 'DELETE') OR has_table_privilege('mb_reader', c.oid, 'TRUNCATE')))
    OR EXISTS (SELECT FROM unnest(ARRAY['public', 'musicbrainz', 'mdp']) s
               WHERE has_schema_privilege('mb_reader', s, 'CREATE')) THEN
    RAISE EXCEPTION 'reference_import_failed: reader has write privileges';
  END IF;
END $$;

SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 20;
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles WHERE rolname = 'mb_reader';
SELECT 'MDP_VERIFIED ' || current_setting('mdp.generation') AS result;
