-- Disposable test database only. Counts use synthetic rows with production-shaped metadata.
CREATE SCHEMA IF NOT EXISTS marts;
CREATE TABLE IF NOT EXISTS marts._build (
  relation text PRIMARY KEY, cycle_id text, close_no bigint, built_at timestamptz
);
CREATE TABLE IF NOT EXISTS marts.mart_chart_history (
  chart_name text, chart_week date, chart_position integer
);
TRUNCATE marts.mart_chart_history;
INSERT INTO marts.mart_chart_history VALUES ('hot-100', '2026-09-27', 1);
INSERT INTO marts._build VALUES (
  'marts.mart_chart_history', '21c978b9-6cb0-42b5-bc2a-532cddb6ff15',
  105, '2026-09-27T14:55:00.132684+00:00'
) ON CONFLICT (relation) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,
  close_no=EXCLUDED.close_no, built_at=EXCLUDED.built_at;
GRANT USAGE ON SCHEMA marts TO showcase_wh;
GRANT SELECT ON marts.mart_chart_history TO showcase_wh;
