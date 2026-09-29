-- Fail before queued migration locks can hold up alert reads.
SET LOCAL lock_timeout = '2s';
--> statement-breakpoint
GRANT INSERT (attempt_no) ON control.alert TO functions_rt;
--> statement-breakpoint
-- Legacy alerts have no attempt identity. Keep one existing alert per run and class
-- as the current attempt's baseline. Preserve every alert and its resolution.
-- A later attempt has a larger number and can open a new alert.
WITH baseline AS (
  SELECT DISTINCT ON (a.run_id, a.class) a.id, a.run_id, a.class,
    coalesce((SELECT max(r.attempt_no) FROM control.run_attempt r WHERE r.run_id = a.run_id), 0) AS attempt_no
  FROM control.alert a
  WHERE a.subject_type = 'run' AND a.run_id IS NOT NULL AND a.attempt_no IS NULL
  ORDER BY a.run_id, a.class, a.opened_at DESC, a.id DESC
)
UPDATE control.alert a SET attempt_no = b.attempt_no
FROM baseline b
WHERE a.id = b.id AND NOT EXISTS (
  SELECT 1 FROM control.alert existing
  WHERE existing.subject_type = 'run' AND existing.run_id = b.run_id
    AND existing.class = b.class AND existing.attempt_no = b.attempt_no
);
