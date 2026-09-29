-- The closure trigger of design on the Reference page: the newest mb_spine landing's projected write
-- as a share of the pgdata volume and its tracked recordings, as the reference probe copies them.
ALTER TABLE "control"."reference_source" ADD COLUMN "closure_write_share" double precision;--> statement-breakpoint
ALTER TABLE "control"."reference_source" ADD COLUMN "closure_recordings" bigint;--> statement-breakpoint
ALTER TABLE "control"."reference_source" ADD COLUMN "closure_measured_at" timestamp with time zone;