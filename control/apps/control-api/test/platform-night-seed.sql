-- Synthetic data for an empty, migrated test database only.
-- Run through platform-night-budget.test.ts; it creates and removes its database.
BEGIN;
CREATE FUNCTION pg_temp.night_id(key text) RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
  SELECT overlay(overlay(md5(key) placing '4' from 13 for 1) placing '8' from 17 for 1)::uuid
$$;
CREATE TEMP TABLE night_runs ON COMMIT DROP AS
SELECT n, pg_temp.night_id('night-run-' || n)::uuid AS id,
       pg_temp.night_id('night-cycle-' || ((n - 1) / 32))::uuid AS cycle_id,
       pg_temp.night_id('night-revision-' || ((n - 1) / 32))::uuid AS revision_id,
       timestamptz '2026-09-28 00:00:00Z' - n * interval '1440 minutes' / 832 AS at
FROM generate_series(1, 2352) n;

INSERT INTO control.warehouse(id, adapter, database, dsn_secret_ref, is_production)
VALUES (pg_temp.night_id('night-warehouse')::uuid, 'postgres', 'night_budget', 'unused', true);
INSERT INTO control.streamline(id, source_key, layer)
VALUES (pg_temp.night_id('night-source')::uuid, 'sp_playlist', 'bronze'),
       (pg_temp.night_id('night-other-source')::uuid, 'apple_playlist', 'bronze');
INSERT INTO control.target_set(id, kind, name)
VALUES (pg_temp.night_id('night-set')::uuid, 'playlist', 'Night test');
INSERT INTO control.target(id, target_set_id, platform, platform_account_id)
SELECT pg_temp.night_id('night-target-' || n)::uuid, pg_temp.night_id('night-set')::uuid, 'spotify', n::text
FROM generate_series(1, 48) n;

INSERT INTO control.cycle(id, cadence, scope, opened_by_dbt_run_id, opened_at, closed_at, status, close_no)
SELECT cycle_id, 'daily', 'global', 'core:' || cycle_id, min(at), max(at) + interval '1 minute', 'closed',
       row_number() OVER (ORDER BY min(at))
FROM night_runs GROUP BY cycle_id;
INSERT INTO control.cycle_attempt(dbt_run_id, cycle_id, runner, reason_category)
SELECT opened_by_dbt_run_id, id, 'core', 'scheduled' FROM control.cycle;
INSERT INTO control.target_export(id, cycle_id, target_set_id, taken_at, member_count)
SELECT revision_id, cycle_id, pg_temp.night_id('night-set')::uuid, min(at), 48
FROM night_runs GROUP BY revision_id, cycle_id;
INSERT INTO control.target_export_member(revision_id, target_id, resource_kind, target_json)
SELECT e.id, t.id, CASE WHEN e.id = pg_temp.night_id('night-revision-0')::uuid AND t.platform_account_id::int % 2 = 0
                      THEN 'account' ELSE 'playlist' END,
       jsonb_build_object('platform', t.platform, 'platform_account_id', t.platform_account_id)
FROM control.target_export e CROSS JOIN control.target t;
-- One frozen empty revision tests zero membership versus an absent revision.
DELETE FROM control.target_export_member WHERE revision_id = pg_temp.night_id('night-revision-1')::uuid;
UPDATE control.target_export SET member_count = 0 WHERE id = pg_temp.night_id('night-revision-1')::uuid;

INSERT INTO control.run(id, kind, work_key, scope, warehouse_id, streamline_id, cycle_id,
                        revision_id, resolved_config, status, created_at)
SELECT id, CASE WHEN n = 3 THEN 'dbt' ELSE 'invoke' END::control.run_kind,
       CASE WHEN n = 13 THEN 'manual:night-probe' WHEN n = 16 THEN 'manual:night-real' ELSE 'night:' || n END, 'global', pg_temp.night_id('night-warehouse')::uuid,
       CASE WHEN n = 3 THEN NULL WHEN n % 3 = 0 THEN pg_temp.night_id('night-other-source')::uuid
            ELSE pg_temp.night_id('night-source')::uuid END,
       CASE WHEN n <> 3 THEN cycle_id END,
       CASE WHEN n NOT IN (1, 3) THEN revision_id END,
       CASE WHEN n = 4 THEN '{}'::jsonb ELSE '{"target_coverage": 1}'::jsonb END,
       CASE WHEN n = 5 THEN 'queued' ELSE 'succeeded' END::control.run_status, at
FROM night_runs;
INSERT INTO control.run_attempt(id, run_id, attempt_no, dbt_run_id, started_at, ended_at, deadline_at, status)
SELECT pg_temp.night_id('night-attempt-' || n)::uuid, id, 1, 'core:' || cycle_id, at, at + interval '1 minute',
       at + interval '1 hour', 'succeeded'
FROM night_runs WHERE n <> 5;
-- Attempts outside the window and excluded test attempts still count against attribution.
INSERT INTO control.run_attempt(run_id, attempt_no, dbt_run_id, started_at, ended_at, deadline_at, status)
SELECT id, a,
       CASE WHEN a = 4 THEN 'test:night:' || id ELSE 'restore:' || id || ':' || a END,
       CASE WHEN a = 2 THEN at - interval '1 day' ELSE at + interval '1 minute' END,
       CASE WHEN a = 2 THEN at - interval '23 hours' ELSE at + interval '2 minutes' END,
       at + interval '1 hour', CASE WHEN a = 2 THEN 'superseded' ELSE 'failed' END::control.run_status
FROM night_runs CROSS JOIN generate_series(2, 4) a WHERE n % 7 = 0;
INSERT INTO control.cycle_attempt(dbt_run_id, cycle_id, runner, reason_category)
SELECT a.dbt_run_id, r.cycle_id, 'core', 'other'
FROM control.run_attempt a JOIN control.run r ON r.id = a.run_id WHERE a.attempt_no > 1;

INSERT INTO control.batch(id, run_id, index, target_ids, cursor_checkpoint, status, updated_at)
SELECT pg_temp.night_id('night-batch-' || n || '-' || b)::uuid, r.id, b,
       array_agg(t.id ORDER BY t.id),
       jsonb_build_object('completed_targets', jsonb_agg(
         CASE WHEN t.platform_account_id::int % 13 = 0 THEN 'skipped:' || t.id
              WHEN t.platform_account_id::int % 17 = 0 THEN 'stale_target:' || t.id
              WHEN t.platform_account_id::int % 19 = 0 THEN 'rejected:' || t.id
              ELSE t.id::text END ORDER BY t.id)), 'succeeded', at + interval '1 minute'
FROM night_runs r CROSS JOIN generate_series(0, 4) b
JOIN control.target t ON t.platform_account_id::int % 5 = b
WHERE (n <= 832 OR b < 3) AND n <> 5
GROUP BY n, r.id, b, at;
INSERT INTO control.dump(id, kind, run_id, streamline_id, cycle_id, uri_prefix, created_at)
SELECT pg_temp.night_id('night-dump-' || n || '-' || d)::uuid,
       CASE WHEN d = 10 THEN 'input' ELSE 'output' END::control.dump_kind,
       r.id, r.streamline_id, r.cycle_id, 'file:///night-test', f.at
FROM night_runs f JOIN control.run r ON r.id = f.id CROSS JOIN generate_series(1, 10) d;
INSERT INTO control.load(dump_id, warehouse_id, target_table, status, rows_inserted, loaded_at)
SELECT id, pg_temp.night_id('night-warehouse')::uuid, 'raw.observations', 'loaded', 17, created_at
FROM control.dump;
-- A second table must not multiply the dump count. Mirror receipts never count as output rows.
INSERT INTO control.load(dump_id, warehouse_id, target_table, status, rows_inserted, loaded_at)
SELECT d.id, pg_temp.night_id('night-warehouse')::uuid, t, 'loaded', 3, d.created_at
FROM control.dump d JOIN night_runs r ON r.id = d.run_id
CROSS JOIN (VALUES ('raw.details'), ('raw.cycles')) tables(t) WHERE r.n % 11 = 0;
INSERT INTO control.alert(class, severity, subject_type, subject_id, run_id, attempt_no, opened_at)
SELECT 'partial_coverage', 'warning', 'run', id::text, id, 3, at + interval '1 minute'
FROM night_runs WHERE n % 7 = 0;
-- Audit history keeps unrelated actions and a small set of real probe shapes.
INSERT INTO control.audit_log(actor, action, subject, after, at)
SELECT 'night-seed', CASE WHEN n <= 11200 THEN 'email.failed'
                         WHEN n <= 12900 THEN 'source.canary'
                         WHEN n <= 13900 THEN 'targets.parkStale'
                         ELSE 'targets.probeChecked' END,
       'night-history-' || n,
       jsonb_build_object('state', 'failed', 'error_class', 'email_delivery_failed'),
       timestamptz '2026-09-28 00:00:00Z' - n * interval '1 minute'
FROM generate_series(1, 14900) n;
INSERT INTO control.audit_log(actor, action, subject, after)
SELECT 'night-seed', 'streamlines.probe', 'night-probe-' || n,
       CASE WHEN n = 1 THEN jsonb_build_object('result', jsonb_build_object('run_id', pg_temp.night_id('night-run-12')))
            WHEN n = 2 THEN '{"input":{"key":"night-probe"}}'::jsonb
            WHEN n = 3 THEN jsonb_build_object('remote', jsonb_build_array(
              jsonb_build_object('result', jsonb_build_object('run_id', pg_temp.night_id('night-run-14'))),
              jsonb_build_object('result', jsonb_build_object('run_id', pg_temp.night_id('night-run-14')))))
            ELSE '{"result":{"run_id":"absent"},"input":{},"remote":[{}, {"result":{"run_id":"not-a-uuid"}}]}'::jsonb END
FROM generate_series(1, 100) n;
COMMIT;
ANALYZE;
