-- A long transaction on control.alert makes these ALTERs fail fast instead of queueing alert reads.
SET LOCAL lock_timeout = '2s';
--> statement-breakpoint
ALTER TABLE "control"."alert" ADD COLUMN "resolved_by" text;--> statement-breakpoint
ALTER TABLE "control"."alert" ADD COLUMN "resolution_reason" text;--> statement-breakpoint
SET LOCAL lock_timeout TO DEFAULT;
