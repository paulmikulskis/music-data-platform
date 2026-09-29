ALTER TABLE "control"."run" ADD COLUMN "resolved_config" jsonb;--> statement-breakpoint
-- Step 1b: the work key carries no configuration. In each cycle, the first-admitted configured run
-- of a key takes the bare key (unless a bare-key run exists); later configured runs become manual
-- reruns, `manual:["config:<run id>","<source_key>","<scope>"]`.
WITH configured AS (
  SELECT r.id, split_part(r.work_key, ':config:', 1) AS base,
    row_number() OVER (PARTITION BY split_part(r.work_key, ':config:', 1) ORDER BY r.created_at, r.id) AS n
  FROM control.run r WHERE r.work_key LIKE '%:config:%'
)
UPDATE control.run r SET work_key = CASE
  WHEN c.n = 1 AND NOT EXISTS (SELECT 1 FROM control.run b WHERE b.work_key = c.base) THEN c.base
  ELSE 'manual:["config:' || r.id::text || '","' || s.source_key || '","' || r.scope || '"]' END
FROM configured c, control.streamline s
WHERE r.id = c.id AND s.id = r.streamline_id;
