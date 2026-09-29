-- Disposable test database only. The close precedes the matching build stamp.
INSERT INTO control.cycle (
  id, cadence, scope, opened_by_dbt_run_id, status, close_no, closed_at
) VALUES (
  '21c978b9-6cb0-42b5-bc2a-532cddb6ff15', 'hourly', 'global',
  'core:count-fixture', 'closed', 105, '2026-09-27T14:54:47.900431+00:00'
) ON CONFLICT (id) DO NOTHING;
