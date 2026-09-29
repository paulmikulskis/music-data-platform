-- Fail before queued migration locks can hold up alert reads.
SET LOCAL lock_timeout = '2s';
--> statement-breakpoint
ALTER TABLE "control"."alert" ADD COLUMN "attempt_no" integer;--> statement-breakpoint
CREATE UNIQUE INDEX "alert_run_attempt_class_unique" ON "control"."alert" USING btree ("run_id","attempt_no","class") WHERE "control"."alert"."subject_type" = 'run' AND "control"."alert"."attempt_no" IS NOT NULL;