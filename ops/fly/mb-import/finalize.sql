-- Finalize one generation database after the upstream import: reader grants, the MDP
-- schema (mdp-schema.sql), and the one mdp.generation row. Run connected to the generation database.
-- Input: generation (the export stamp from LATEST). MB_READER_PASSWORD, when set in the environment,
-- sets the reader password; the one-time handoff over SSH leaves the existing password alone.
-- validated_at stays null here: the importer sets it only after verify.sql passes.
\set ON_ERROR_STOP on
SET TIME ZONE 'UTC';

SELECT 'CREATE ROLE mb_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mb_reader') \gexec
\getenv reader_password MB_READER_PASSWORD
\if :{?reader_password}
SELECT format('ALTER ROLE mb_reader PASSWORD %L', :'reader_password') \gexec
\unset reader_password
\endif

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO mb_reader', current_database()) \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA musicbrainz FROM PUBLIC;
GRANT USAGE ON SCHEMA musicbrainz TO mb_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA musicbrainz FROM mb_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA musicbrainz TO mb_reader;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA musicbrainz FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA musicbrainz GRANT SELECT ON TABLES TO mb_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA musicbrainz REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER ROLE mb_reader SET search_path = musicbrainz, public;
ALTER ROLE mb_reader SET default_transaction_read_only = on;

\ir ../mb-db/mdp-schema.sql

SELECT set_config('mdp.generation', :'generation', false) \g /dev/null
DO $$
BEGIN
  IF current_setting('mdp.generation') !~ '^[0-9]{8}-[0-9]{6}$' THEN
    RAISE EXCEPTION 'reference_import_failed: generation % is not an export stamp', current_setting('mdp.generation');
  END IF;
END $$;

-- A generation database holds exactly one generation; finalizing again replaces the row and clears
-- validated_at, so verify.sql must pass again before the generation serves.
BEGIN;
DELETE FROM mdp.generation;
INSERT INTO mdp.generation (generation, export_date, replication_sequence, schema_sequence, counts)
SELECT :'generation', to_timestamp(:'generation', 'YYYYMMDD-HH24MISS'),
       rc.current_replication_sequence, rc.current_schema_sequence,
       jsonb_build_object(
         'recording', (SELECT count(*) FROM musicbrainz.recording),
         'isrc', (SELECT count(*) FROM musicbrainz.isrc),
         'url', (SELECT count(*) FROM musicbrainz.url),
         'l_recording_url', (SELECT count(*) FROM musicbrainz.l_recording_url),
         'l_release_url', (SELECT count(*) FROM musicbrainz.l_release_url),
         'l_artist_url', (SELECT count(*) FROM musicbrainz.l_artist_url),
         'release', (SELECT count(*) FROM musicbrainz.release),
         'track', (SELECT count(*) FROM musicbrainz.track),
         'artist', (SELECT count(*) FROM musicbrainz.artist),
         'artist_credit', (SELECT count(*) FROM musicbrainz.artist_credit))
FROM musicbrainz.replication_control rc;
COMMIT;
