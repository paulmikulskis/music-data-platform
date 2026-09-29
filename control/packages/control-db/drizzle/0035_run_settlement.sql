-- Stop before a waiting schema lock holds up scheduled work.
SET LOCAL lock_timeout = '2s';
--> statement-breakpoint
ALTER TABLE "control"."run" ADD COLUMN "settled_at" timestamp with time zone;