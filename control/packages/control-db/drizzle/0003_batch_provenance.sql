-- Control boundaries: PostgreSQL needs an explicit array conversion and the old default removed.
ALTER TABLE "control"."batch" ALTER COLUMN "target_ids" DROP DEFAULT;
--> statement-breakpoint
ALTER TABLE "control"."batch" ALTER COLUMN "target_ids" SET DATA TYPE uuid[] USING target_ids::uuid[];--> statement-breakpoint
ALTER TABLE "control"."batch" ALTER COLUMN "target_ids" SET DEFAULT '{}';--> statement-breakpoint
ALTER TABLE "control"."cycle_attempt" ADD COLUMN "git_sha" text;--> statement-breakpoint
ALTER TABLE "control"."cycle_attempt" ADD COLUMN "image_digest" text;--> statement-breakpoint
ALTER TABLE "control"."batch" ADD COLUMN "dump_ids" uuid[] DEFAULT '{}' NOT NULL;
--> statement-breakpoint
-- Retain existing single-dump registrations in the multi-dump representation.
UPDATE control.batch SET dump_ids = ARRAY[dump_id] WHERE dump_id IS NOT NULL;
