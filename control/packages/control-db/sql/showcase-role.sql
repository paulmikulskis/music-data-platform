-- The showcase inherits the same reviewed projections as a staff explorer.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='explorer_ro') THEN
    CREATE ROLE explorer_ro NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='showcase_wh') THEN
    CREATE ROLE showcase_wh LOGIN INHERIT;
  END IF;
END $$;
ALTER ROLE showcase_wh LOGIN INHERIT CONNECTION LIMIT 4;
ALTER ROLE showcase_wh SET default_transaction_read_only = on;
ALTER ROLE showcase_wh SET statement_timeout = '5s';
ALTER ROLE showcase_wh SET lock_timeout = '1s';
ALTER ROLE showcase_wh SET idle_in_transaction_session_timeout = '10s';
GRANT explorer_ro TO showcase_wh WITH INHERIT TRUE, SET FALSE;
GRANT CONNECT ON DATABASE warehouse TO showcase_wh;
