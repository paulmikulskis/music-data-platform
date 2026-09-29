-- The persistent mdp_meta database on the mirror server: the import ledger and the volume size the
-- reference probe reads. Idempotent. Run connected to any existing database (postgres).
-- Input: meta_db (default mdp_meta; tests use mbt_meta_<n>).
\set ON_ERROR_STOP on
\if :{?meta_db}
\else
\set meta_db mdp_meta
\endif

-- The reader role is created here without a password; finalize.sql sets it from MB_READER_PASSWORD.
SELECT 'CREATE ROLE mb_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mb_reader') \gexec
-- pg_database_size() of a database the reader cannot connect to needs pg_read_all_stats.
GRANT pg_read_all_stats TO mb_reader;

SELECT format('CREATE DATABASE %I', :'meta_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'meta_db') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'meta_db') \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO mb_reader', :'meta_db') \gexec

\connect :meta_db
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- One row per import attempt. The importer and refresh.py write it; state only moves forward:
-- running -> validated -> promoted, or failed with the alert class in message.
CREATE TABLE IF NOT EXISTS public.import_run (
  id bigserial PRIMARY KEY,
  generation text NOT NULL,
  target_db text NOT NULL,
  state text NOT NULL CHECK (state IN ('running', 'validated', 'promoted', 'failed')),
  phase text,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  message text
);

-- The capacity PostgreSQL can use on the mirror volume, measured with df (used + available).
CREATE TABLE IF NOT EXISTS public.volume (
  id int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  total_bytes bigint NOT NULL,
  checked_at timestamptz NOT NULL DEFAULT now()
);

GRANT USAGE ON SCHEMA public TO mb_reader;
REVOKE ALL ON public.import_run, public.volume FROM mb_reader;
GRANT SELECT ON public.import_run, public.volume TO mb_reader;
