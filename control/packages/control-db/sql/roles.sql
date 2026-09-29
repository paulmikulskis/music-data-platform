-- Run after databases.sql as the cluster administrator; supply all *_password psql variables.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') THEN
    CREATE ROLE migrator LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE migrator LOGIN NOINHERIT PASSWORD :'migrator_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rights_sync') THEN
    CREATE ROLE rights_sync LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE rights_sync LOGIN NOINHERIT PASSWORD :'rights_sync_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'control_rt') THEN
    CREATE ROLE control_rt LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE control_rt LOGIN NOINHERIT PASSWORD :'control_rt_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'functions_rt') THEN
    CREATE ROLE functions_rt LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE functions_rt LOGIN NOINHERIT PASSWORD :'functions_rt_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_read') THEN
    CREATE ROLE service_read LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE service_read LOGIN NOINHERIT PASSWORD :'service_read_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'loader_wh') THEN
    CREATE ROLE loader_wh LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE loader_wh LOGIN NOINHERIT PASSWORD :'loader_wh_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dbt_transform') THEN
    CREATE ROLE dbt_transform LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE dbt_transform LOGIN NOINHERIT PASSWORD :'dbt_transform_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'workbench_wh') THEN
    CREATE ROLE workbench_wh LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE workbench_wh LOGIN NOINHERIT PASSWORD :'workbench_wh_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'reader_wh') THEN
    CREATE ROLE reader_wh LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE reader_wh LOGIN NOINHERIT PASSWORD :'reader_wh_password';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'api_key_reader') THEN
    CREATE ROLE api_key_reader LOGIN NOINHERIT;
  END IF;
END
$$;
ALTER ROLE api_key_reader LOGIN NOINHERIT PASSWORD :'api_key_reader_password';

ALTER DATABASE control OWNER TO migrator;
REVOKE ALL ON DATABASE control FROM PUBLIC;
REVOKE ALL ON DATABASE warehouse FROM PUBLIC;
GRANT CONNECT ON DATABASE control TO migrator, rights_sync, control_rt, functions_rt, api_key_reader;
GRANT CONNECT ON DATABASE warehouse TO service_read, loader_wh, dbt_transform, workbench_wh, reader_wh;

\ir showcase-role.sql
ALTER ROLE showcase_wh PASSWORD :'showcase_wh_password';
