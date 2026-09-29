-- Promote a validated generation: next_db becomes serving_db, the old serving database
-- is renamed to prev_db and dropped only after the swap commits. Run connected to meta_db.
-- Inputs: run_id, generation, serving_db, next_db, prev_db, meta_db.
-- Promotion refuses unless the import_run row is 'validated' for next_db and next_db holds exactly
-- that generation with validated_at set. Open reader sessions on either database are terminated.
\set ON_ERROR_STOP on

\connect :meta_db
SELECT set_config('mdp.run_id', :'run_id', false), set_config('mdp.generation', :'generation', false),
       set_config('mdp.serving_db', :'serving_db', false), set_config('mdp.next_db', :'next_db', false),
       set_config('mdp.prev_db', :'prev_db', false) \g /dev/null
DO $$
DECLARE r public.import_run;
BEGIN
  SELECT * INTO r FROM public.import_run WHERE id = current_setting('mdp.run_id')::bigint;
  IF NOT FOUND OR r.state <> 'validated' OR r.target_db <> current_setting('mdp.next_db')
     OR r.generation <> current_setting('mdp.generation') THEN
    RAISE EXCEPTION 'reference_import_failed: run % is not a validated import of % into %; promotion refused',
      current_setting('mdp.run_id'), current_setting('mdp.generation'), current_setting('mdp.next_db');
  END IF;
  IF NOT EXISTS (SELECT FROM pg_database WHERE datname = current_setting('mdp.next_db')) THEN
    RAISE EXCEPTION 'reference_import_failed: % does not exist; promotion refused', current_setting('mdp.next_db');
  END IF;
  IF EXISTS (SELECT FROM pg_database WHERE datname = current_setting('mdp.prev_db')) THEN
    RAISE EXCEPTION '% is left from an earlier promotion; drop it before promoting', current_setting('mdp.prev_db');
  END IF;
END $$;

\connect :next_db
SELECT set_config('mdp.run_id', :'run_id', false), set_config('mdp.generation', :'generation', false),
       set_config('mdp.serving_db', :'serving_db', false), set_config('mdp.next_db', :'next_db', false),
       set_config('mdp.prev_db', :'prev_db', false) \g /dev/null
DO $$
BEGIN
  IF to_regclass('mdp.generation') IS NULL OR (SELECT count(*) FROM mdp.generation) <> 1
     OR NOT EXISTS (SELECT FROM mdp.generation WHERE generation = current_setting('mdp.generation')
                    AND validated_at IS NOT NULL) THEN
    RAISE EXCEPTION 'reference_import_failed: % does not hold validated generation %; promotion refused',
      current_database(), current_setting('mdp.generation');
  END IF;
END $$;

\connect :meta_db
SELECT set_config('mdp.run_id', :'run_id', false), set_config('mdp.generation', :'generation', false),
       set_config('mdp.serving_db', :'serving_db', false), set_config('mdp.next_db', :'next_db', false),
       set_config('mdp.prev_db', :'prev_db', false) \g /dev/null
UPDATE public.import_run SET phase = 'promote' WHERE id = :run_id AND state = 'validated';
-- Refuse new sessions on the serving database so a reconnecting reader cannot block the rename.
SELECT format('ALTER DATABASE %I WITH ALLOW_CONNECTIONS false', :'serving_db')
WHERE EXISTS (SELECT FROM pg_database WHERE datname = :'serving_db') \gexec
\set ON_ERROR_STOP off
DO $$
DECLARE
  serving text := current_setting('mdp.serving_db');
  nxt text := current_setting('mdp.next_db');
  prev text := current_setting('mdp.prev_db');
BEGIN
  PERFORM pg_terminate_backend(pid, 5000) FROM pg_stat_activity
  WHERE datname IN (serving, nxt) AND pid <> pg_backend_pid();
  IF EXISTS (SELECT FROM pg_database WHERE datname = serving) THEN
    EXECUTE format('ALTER DATABASE %I RENAME TO %I', serving, prev);
  END IF;
  EXECUTE format('ALTER DATABASE %I RENAME TO %I', nxt, serving);
  UPDATE public.import_run SET state = 'promoted', phase = 'promoted', finished_at = now(),
         message = format('generation %s serves as %s', current_setting('mdp.generation'), serving)
  WHERE id = current_setting('mdp.run_id')::bigint AND state = 'validated';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'run % changed state during promotion', current_setting('mdp.run_id');
  END IF;
END $$;
\if :ERROR
\set ON_ERROR_STOP on
SELECT format('ALTER DATABASE %I WITH ALLOW_CONNECTIONS true', :'serving_db')
WHERE EXISTS (SELECT FROM pg_database WHERE datname = :'serving_db') \gexec
DO $$
BEGIN
  RAISE EXCEPTION 'reference_import_failed: promotion of % did not commit; % still serves the previous generation',
    current_setting('mdp.generation'), current_setting('mdp.serving_db');
END $$;
\endif
\set ON_ERROR_STOP on

-- The swap committed: the previous generation is no longer reachable by name.
SELECT format('DROP DATABASE %I WITH (FORCE)', :'prev_db')
WHERE EXISTS (SELECT FROM pg_database WHERE datname = :'prev_db') \gexec
SELECT 'MDP_JSON ' || json_build_object('promoted', :'generation', 'serving_db', :'serving_db', 'run_id', :run_id);
