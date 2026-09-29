
ALTER TABLE "control"."dbt_job" ADD COLUMN "due_hour" integer;--> statement-breakpoint
ALTER TABLE "control"."dbt_job" ADD COLUMN "due_weekday" integer;--> statement-breakpoint
ALTER TABLE "control"."dbt_job" ADD COLUMN "timezone" text DEFAULT 'UTC' NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."cycle" ADD COLUMN "local_weekday" integer;--> statement-breakpoint
ALTER TABLE "control"."dbt_job" ADD CONSTRAINT "dbt_job_due_hour" CHECK ("control"."dbt_job"."due_hour" BETWEEN 0 AND 23);--> statement-breakpoint
ALTER TABLE "control"."dbt_job" ADD CONSTRAINT "dbt_job_due_weekday" CHECK ("control"."dbt_job"."due_weekday" BETWEEN 1 AND 7);
