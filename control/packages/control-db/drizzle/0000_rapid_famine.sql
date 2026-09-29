CREATE SCHEMA "control";
--> statement-breakpoint
CREATE TYPE "control"."adapter" AS ENUM('postgres', 'snowflake');--> statement-breakpoint
CREATE TYPE "control"."alert_severity" AS ENUM('info', 'warning', 'critical');--> statement-breakpoint
CREATE TYPE "control"."batch_status" AS ENUM('queued', 'running', 'draining', 'succeeded', 'failed');--> statement-breakpoint
CREATE TYPE "control"."budget_scope" AS ENUM('global', 'tenant', 'streamline', 'llm_step');--> statement-breakpoint
CREATE TYPE "control"."cost_origin" AS ENUM('estimate', 'litellm', 'invoice');--> statement-breakpoint
CREATE TYPE "control"."coverage" AS ENUM('full', 'partial', 'empty');--> statement-breakpoint
CREATE TYPE "control"."cycle_input_phase" AS ENUM('bronze', 'derived');--> statement-breakpoint
CREATE TYPE "control"."cycle_status" AS ENUM('open', 'closed', 'superseded');--> statement-breakpoint
CREATE TYPE "control"."dump_kind" AS ENUM('output', 'input', 'payload', 'rejected');--> statement-breakpoint
CREATE TYPE "control"."hard_action" AS ENUM('warn', 'pause', 'degrade');--> statement-breakpoint
CREATE TYPE "control"."load_op" AS ENUM('load', 'repair');--> statement-breakpoint
CREATE TYPE "control"."load_status" AS ENUM('pending', 'claimed', 'loaded', 'rejected');--> statement-breakpoint
CREATE TYPE "control"."resolution_status" AS ENUM('pending', 'resolved', 'failed');--> statement-breakpoint
CREATE TYPE "control"."rights_status" AS ENUM('pending', 'approved', 'restricted', 'denied');--> statement-breakpoint
CREATE TYPE "control"."run_kind" AS ENUM('export', 'close', 'invoke', 'repair', 'backfill', 'migrate', 'workbench', 'dbt');--> statement-breakpoint
CREATE TYPE "control"."run_status" AS ENUM('queued', 'running', 'succeeded', 'partial', 'failed', 'superseded');--> statement-breakpoint
CREATE TYPE "control"."runner" AS ENUM('cloud', 'core');--> statement-breakpoint
CREATE TYPE "control"."tenant_status" AS ENUM('active', 'inactive');--> statement-breakpoint
CREATE TYPE "control"."workbench_run_kind" AS ENUM('query', 'preview', 'backtest', 'explain');--> statement-breakpoint
CREATE SEQUENCE "control"."landed_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1;--> statement-breakpoint
CREATE TABLE "control"."audit_log" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"actor" text NOT NULL,
	"action" text NOT NULL,
	"subject" text NOT NULL,
	"before" jsonb,
	"after" jsonb,
	"at" timestamp with time zone DEFAULT now() NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."budget" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"scope" "control"."budget_scope" NOT NULL,
	"scope_id" uuid,
	"period" text NOT NULL,
	"cap_cents" bigint NOT NULL,
	"soft_pct" integer NOT NULL,
	"hard_action" "control"."hard_action" NOT NULL,
	"ceiling_cents" bigint,
	"raised_by" text,
	"raised_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."dbt_job" (
	"job_id" text PRIMARY KEY NOT NULL,
	"runner" "control"."runner" NOT NULL,
	"cadence" text NOT NULL,
	"scope" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."llm_step" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"source_key" text NOT NULL,
	"model" text NOT NULL,
	"prompt_id" uuid,
	"prompt_version" integer,
	"params" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"params_hash" text NOT NULL,
	"config_version" integer NOT NULL,
	"litellm_key_alias" text,
	"budget_id" uuid,
	"fallback_model" text,
	"enabled" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."prompt" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"version" integer NOT NULL,
	"body" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."rights_source" (
	"source_key" text PRIMARY KEY NOT NULL,
	"provider" text NOT NULL,
	"category" text NOT NULL,
	"license_ref" text,
	"learning_eligible" boolean DEFAULT false NOT NULL,
	"resale_permitted" boolean DEFAULT false NOT NULL,
	"rights_status" "control"."rights_status" DEFAULT 'pending' NOT NULL,
	"review_ref" text,
	"synced_at" timestamp with time zone DEFAULT now() NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."runbook" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"slug" text NOT NULL,
	"title" text NOT NULL,
	"body_md" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "runbook_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "control"."runner_mode" (
	"id" boolean PRIMARY KEY DEFAULT true NOT NULL,
	"runner" "control"."runner" DEFAULT 'cloud' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "runner_mode_singleton" CHECK ("control"."runner_mode"."id")
);
--> statement-breakpoint
CREATE TABLE "control"."streamline" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"source_key" text NOT NULL,
	"layer" text NOT NULL,
	"tenant_bound" boolean DEFAULT false NOT NULL,
	"writes" text[] DEFAULT '{}' NOT NULL,
	"reads" text[] DEFAULT '{}' NOT NULL,
	"external" boolean DEFAULT false NOT NULL,
	"llm_step_id" uuid,
	"cadence_tag" text,
	"enabled" boolean DEFAULT true NOT NULL,
	"allow_partial" boolean DEFAULT false NOT NULL,
	"batch_size" integer DEFAULT 1 NOT NULL,
	"max_concurrency" integer DEFAULT 1 NOT NULL,
	"timeout_s" integer DEFAULT 300 NOT NULL,
	"storage" text DEFAULT 'heap' NOT NULL,
	"acknowledged_fingerprints" text[] DEFAULT '{}' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "streamline_source_key_unique" UNIQUE("source_key")
);
--> statement-breakpoint
CREATE TABLE "control"."target" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"target_set_id" uuid NOT NULL,
	"platform" text NOT NULL,
	"platform_account_id" text,
	"handle" text,
	"display_name" text,
	"role" text,
	"resolution_status" "control"."resolution_status" DEFAULT 'pending' NOT NULL,
	"activated_at" timestamp with time zone,
	"deactivated_at" timestamp with time zone,
	"priority" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."target_set" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"kind" text NOT NULL,
	"tenant_id" uuid,
	"name" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "target_set_kind_tenant_unique" UNIQUE NULLS NOT DISTINCT("kind","tenant_id")
);
--> statement-breakpoint
CREATE TABLE "control"."tenant" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"slug" text NOT NULL,
	"name" text NOT NULL,
	"status" "control"."tenant_status" DEFAULT 'active' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "tenant_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "control"."warehouse" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"adapter" "control"."adapter" NOT NULL,
	"database" text NOT NULL,
	"dsn_secret_ref" text NOT NULL,
	"is_production" boolean DEFAULT false NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."cycle" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"cadence" text NOT NULL,
	"scope" text NOT NULL,
	"opened_at" timestamp with time zone DEFAULT now() NOT NULL,
	"opened_by_dbt_run_id" text NOT NULL,
	"closed_at" timestamp with time zone,
	"status" "control"."cycle_status" DEFAULT 'open' NOT NULL,
	"git_sha" text,
	"image_digest" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."cycle_attempt" (
	"dbt_run_id" text PRIMARY KEY NOT NULL,
	"cycle_id" uuid NOT NULL,
	"bound_at" timestamp with time zone DEFAULT now() NOT NULL,
	"reason_category" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."cycle_input" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"cycle_id" uuid NOT NULL,
	"dump_id" uuid NOT NULL,
	"phase" "control"."cycle_input_phase" NOT NULL,
	"added_at" timestamp with time zone DEFAULT now() NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "cycle_input_cycle_dump_unique" UNIQUE("cycle_id","dump_id")
);
--> statement-breakpoint
CREATE TABLE "control"."target_export" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"cycle_id" uuid NOT NULL,
	"target_set_id" uuid NOT NULL,
	"taken_at" timestamp with time zone DEFAULT now() NOT NULL,
	"member_count" integer NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "target_export_cycle_set_unique" UNIQUE("cycle_id","target_set_id")
);
--> statement-breakpoint
CREATE TABLE "control"."target_export_member" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"revision_id" uuid NOT NULL,
	"target_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "target_export_member_revision_target_unique" UNIQUE("revision_id","target_id")
);
--> statement-breakpoint
CREATE TABLE "control"."alert" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"class" text NOT NULL,
	"severity" "control"."alert_severity" NOT NULL,
	"subject_type" text NOT NULL,
	"subject_id" text NOT NULL,
	"run_id" uuid,
	"opened_at" timestamp with time zone DEFAULT now() NOT NULL,
	"acknowledged_by" text,
	"resolved_at" timestamp with time zone,
	"runbook_slug" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."batch" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"run_id" uuid NOT NULL,
	"index" integer NOT NULL,
	"target_ids" text[] DEFAULT '{}' NOT NULL,
	"status" "control"."batch_status" DEFAULT 'queued' NOT NULL,
	"attempt_id" uuid,
	"worker_id" text,
	"lease_token" uuid,
	"lease_expires_at" timestamp with time zone,
	"heartbeat_at" timestamp with time zone,
	"dump_id" uuid,
	"last_part_uploaded" integer,
	"cursor_checkpoint" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "batch_run_index_unique" UNIQUE("run_id","index")
);
--> statement-breakpoint
CREATE TABLE "control"."budget_reservation" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"budget_id" uuid NOT NULL,
	"run_id" uuid NOT NULL,
	"parent_reservation_id" uuid,
	"reserved_cents" bigint DEFAULT 0 NOT NULL,
	"consumed_cents" bigint DEFAULT 0 NOT NULL,
	"settled_at" timestamp with time zone,
	"policy_snapshot" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."call_ledger" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"run_id" uuid NOT NULL,
	"target_id" uuid,
	"vendor" text NOT NULL,
	"endpoint" text NOT NULL,
	"request_id" text,
	"http_status" integer,
	"duration_ms" integer,
	"attempt" integer NOT NULL,
	"cost_cents" bigint DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."cost_ledger" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"occurred_at" timestamp with time zone DEFAULT now() NOT NULL,
	"tenant_id" uuid,
	"streamline_id" uuid,
	"run_id" uuid,
	"llm_step_id" uuid,
	"vendor" text NOT NULL,
	"provider_request_id" text NOT NULL,
	"unit" text NOT NULL,
	"quantity" numeric NOT NULL,
	"cost_cents" bigint NOT NULL,
	"origin" "control"."cost_origin" NOT NULL,
	"is_current" boolean DEFAULT true NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "cost_ledger_vendor_request_origin_unique" UNIQUE("vendor","provider_request_id","origin")
);
--> statement-breakpoint
CREATE TABLE "control"."cursor" (
	"streamline_id" uuid NOT NULL,
	"target_id" uuid,
	"cursor_key" text NOT NULL,
	"cursor_value" jsonb,
	"version" bigint DEFAULT 0 NOT NULL,
	"reset_generation" bigint DEFAULT 0 NOT NULL,
	"dump_id" uuid,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "cursor_streamline_target_key_unique" UNIQUE NULLS NOT DISTINCT("streamline_id","target_id","cursor_key")
);
--> statement-breakpoint
CREATE TABLE "control"."dead_letter" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"run_id" uuid NOT NULL,
	"streamline_id" uuid,
	"target_id" uuid,
	"reason" text NOT NULL,
	"payload_ref" text,
	"diff" jsonb,
	"first_seen_at" timestamp with time zone DEFAULT now() NOT NULL,
	"resolved_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."run" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"kind" "control"."run_kind" NOT NULL,
	"work_key" text NOT NULL,
	"cycle_id" uuid,
	"scope" text NOT NULL,
	"streamline_id" uuid,
	"target_set_id" uuid,
	"revision_id" uuid,
	"tenant_id" uuid,
	"warehouse_id" uuid NOT NULL,
	"parent_run_id" uuid,
	"trace_id" text,
	"status" "control"."run_status" DEFAULT 'queued' NOT NULL,
	"expected_batches" integer,
	"input_dump_id" uuid,
	"rows_written" bigint DEFAULT 0 NOT NULL,
	"rows_rejected" bigint DEFAULT 0 NOT NULL,
	"coverage" "control"."coverage",
	"cost_cents" bigint DEFAULT 0 NOT NULL,
	"error_class" text,
	"error_message" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."run_attempt" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"run_id" uuid NOT NULL,
	"attempt_no" integer NOT NULL,
	"dbt_run_id" text,
	"deadline_at" timestamp with time zone NOT NULL,
	"started_at" timestamp with time zone DEFAULT now() NOT NULL,
	"ended_at" timestamp with time zone,
	"status" "control"."run_status" DEFAULT 'queued' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "run_attempt_run_number_unique" UNIQUE("run_id","attempt_no")
);
--> statement-breakpoint
CREATE TABLE "control"."run_event" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"run_id" uuid NOT NULL,
	"at" timestamp with time zone DEFAULT now() NOT NULL,
	"level" text NOT NULL,
	"event_type" text NOT NULL,
	"message" text NOT NULL,
	"attrs" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."dump" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"kind" "control"."dump_kind" NOT NULL,
	"run_id" uuid NOT NULL,
	"streamline_id" uuid,
	"cycle_id" uuid,
	"revision_id" uuid,
	"landed_seq" bigint DEFAULT nextval('control.landed_seq') NOT NULL,
	"uri_prefix" text NOT NULL,
	"files" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"row_count" bigint DEFAULT 0 NOT NULL,
	"schema_fingerprint" text,
	"function_version" text,
	"lease_token" uuid,
	"published_at" timestamp with time zone,
	"quarantined_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."load" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"dump_id" uuid NOT NULL,
	"warehouse_id" uuid NOT NULL,
	"target_table" text NOT NULL,
	"op" "control"."load_op" DEFAULT 'load' NOT NULL,
	"generation" integer DEFAULT 1 NOT NULL,
	"status" "control"."load_status" DEFAULT 'pending' NOT NULL,
	"repair_requested" boolean DEFAULT false NOT NULL,
	"claim_token" uuid,
	"claim_expires_at" timestamp with time zone,
	"rows_inserted" bigint DEFAULT 0 NOT NULL,
	"loaded_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "load_dump_warehouse_table_unique" UNIQUE("dump_id","warehouse_id","target_table")
);
--> statement-breakpoint
CREATE TABLE "control"."workbench_run" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"session_id" uuid NOT NULL,
	"kind" "control"."workbench_run_kind" NOT NULL,
	"input" jsonb NOT NULL,
	"compiled_sql" text,
	"status" "control"."run_status" DEFAULT 'queued' NOT NULL,
	"rows" bigint,
	"duration_ms" integer,
	"artifact_ref" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."workbench_session" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" text NOT NULL,
	"scratch_schema" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_used_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "workbench_session_scratch_schema_unique" UNIQUE("scratch_schema")
);
--> statement-breakpoint
ALTER TABLE "control"."llm_step" ADD CONSTRAINT "llm_step_prompt_id_prompt_id_fk" FOREIGN KEY ("prompt_id") REFERENCES "control"."prompt"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."llm_step" ADD CONSTRAINT "llm_step_budget_id_budget_id_fk" FOREIGN KEY ("budget_id") REFERENCES "control"."budget"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."streamline" ADD CONSTRAINT "streamline_llm_step_id_llm_step_id_fk" FOREIGN KEY ("llm_step_id") REFERENCES "control"."llm_step"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."target" ADD CONSTRAINT "target_target_set_id_target_set_id_fk" FOREIGN KEY ("target_set_id") REFERENCES "control"."target_set"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."target_set" ADD CONSTRAINT "target_set_tenant_id_tenant_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "control"."tenant"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cycle_attempt" ADD CONSTRAINT "cycle_attempt_cycle_id_cycle_id_fk" FOREIGN KEY ("cycle_id") REFERENCES "control"."cycle"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cycle_input" ADD CONSTRAINT "cycle_input_cycle_id_cycle_id_fk" FOREIGN KEY ("cycle_id") REFERENCES "control"."cycle"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cycle_input" ADD CONSTRAINT "cycle_input_dump_id_dump_id_fk" FOREIGN KEY ("dump_id") REFERENCES "control"."dump"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."target_export" ADD CONSTRAINT "target_export_cycle_id_cycle_id_fk" FOREIGN KEY ("cycle_id") REFERENCES "control"."cycle"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."target_export" ADD CONSTRAINT "target_export_target_set_id_target_set_id_fk" FOREIGN KEY ("target_set_id") REFERENCES "control"."target_set"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."target_export_member" ADD CONSTRAINT "target_export_member_revision_id_target_export_id_fk" FOREIGN KEY ("revision_id") REFERENCES "control"."target_export"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."target_export_member" ADD CONSTRAINT "target_export_member_target_id_target_id_fk" FOREIGN KEY ("target_id") REFERENCES "control"."target"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."alert" ADD CONSTRAINT "alert_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."alert" ADD CONSTRAINT "alert_runbook_slug_runbook_slug_fk" FOREIGN KEY ("runbook_slug") REFERENCES "control"."runbook"("slug") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."batch" ADD CONSTRAINT "batch_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."batch" ADD CONSTRAINT "batch_attempt_id_run_attempt_id_fk" FOREIGN KEY ("attempt_id") REFERENCES "control"."run_attempt"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."batch" ADD CONSTRAINT "batch_dump_id_dump_id_fk" FOREIGN KEY ("dump_id") REFERENCES "control"."dump"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."budget_reservation" ADD CONSTRAINT "budget_reservation_budget_id_budget_id_fk" FOREIGN KEY ("budget_id") REFERENCES "control"."budget"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."budget_reservation" ADD CONSTRAINT "budget_reservation_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."budget_reservation" ADD CONSTRAINT "budget_reservation_parent_reservation_id_budget_reservation_id_fk" FOREIGN KEY ("parent_reservation_id") REFERENCES "control"."budget_reservation"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."call_ledger" ADD CONSTRAINT "call_ledger_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."call_ledger" ADD CONSTRAINT "call_ledger_target_id_target_id_fk" FOREIGN KEY ("target_id") REFERENCES "control"."target"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cost_ledger" ADD CONSTRAINT "cost_ledger_tenant_id_tenant_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "control"."tenant"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cost_ledger" ADD CONSTRAINT "cost_ledger_streamline_id_streamline_id_fk" FOREIGN KEY ("streamline_id") REFERENCES "control"."streamline"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cost_ledger" ADD CONSTRAINT "cost_ledger_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cost_ledger" ADD CONSTRAINT "cost_ledger_llm_step_id_llm_step_id_fk" FOREIGN KEY ("llm_step_id") REFERENCES "control"."llm_step"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cursor" ADD CONSTRAINT "cursor_streamline_id_streamline_id_fk" FOREIGN KEY ("streamline_id") REFERENCES "control"."streamline"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cursor" ADD CONSTRAINT "cursor_target_id_target_id_fk" FOREIGN KEY ("target_id") REFERENCES "control"."target"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."cursor" ADD CONSTRAINT "cursor_dump_id_dump_id_fk" FOREIGN KEY ("dump_id") REFERENCES "control"."dump"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."dead_letter" ADD CONSTRAINT "dead_letter_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."dead_letter" ADD CONSTRAINT "dead_letter_streamline_id_streamline_id_fk" FOREIGN KEY ("streamline_id") REFERENCES "control"."streamline"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."dead_letter" ADD CONSTRAINT "dead_letter_target_id_target_id_fk" FOREIGN KEY ("target_id") REFERENCES "control"."target"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_cycle_id_cycle_id_fk" FOREIGN KEY ("cycle_id") REFERENCES "control"."cycle"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_streamline_id_streamline_id_fk" FOREIGN KEY ("streamline_id") REFERENCES "control"."streamline"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_target_set_id_target_set_id_fk" FOREIGN KEY ("target_set_id") REFERENCES "control"."target_set"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_revision_id_target_export_id_fk" FOREIGN KEY ("revision_id") REFERENCES "control"."target_export"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_tenant_id_tenant_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "control"."tenant"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_warehouse_id_warehouse_id_fk" FOREIGN KEY ("warehouse_id") REFERENCES "control"."warehouse"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_parent_run_id_run_id_fk" FOREIGN KEY ("parent_run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run" ADD CONSTRAINT "run_input_dump_id_dump_id_fk" FOREIGN KEY ("input_dump_id") REFERENCES "control"."dump"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run_attempt" ADD CONSTRAINT "run_attempt_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."run_event" ADD CONSTRAINT "run_event_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."dump" ADD CONSTRAINT "dump_run_id_run_id_fk" FOREIGN KEY ("run_id") REFERENCES "control"."run"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."dump" ADD CONSTRAINT "dump_streamline_id_streamline_id_fk" FOREIGN KEY ("streamline_id") REFERENCES "control"."streamline"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."dump" ADD CONSTRAINT "dump_cycle_id_cycle_id_fk" FOREIGN KEY ("cycle_id") REFERENCES "control"."cycle"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."dump" ADD CONSTRAINT "dump_revision_id_target_export_id_fk" FOREIGN KEY ("revision_id") REFERENCES "control"."target_export"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."load" ADD CONSTRAINT "load_dump_id_dump_id_fk" FOREIGN KEY ("dump_id") REFERENCES "control"."dump"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."load" ADD CONSTRAINT "load_warehouse_id_warehouse_id_fk" FOREIGN KEY ("warehouse_id") REFERENCES "control"."warehouse"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."workbench_run" ADD CONSTRAINT "workbench_run_session_id_workbench_session_id_fk" FOREIGN KEY ("session_id") REFERENCES "control"."workbench_session"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "target_resolved_identity_unique" ON "control"."target" USING btree ("target_set_id","platform","platform_account_id") WHERE "control"."target"."resolution_status" = 'resolved';--> statement-breakpoint
CREATE UNIQUE INDEX "warehouse_one_production" ON "control"."warehouse" USING btree ("is_production") WHERE "control"."warehouse"."is_production";--> statement-breakpoint
CREATE INDEX "cycle_cadence_scope_status_idx" ON "control"."cycle" USING btree ("cadence","scope","status");--> statement-breakpoint
CREATE INDEX "cycle_input_cycle_idx" ON "control"."cycle_input" USING btree ("cycle_id");--> statement-breakpoint
CREATE INDEX "alert_subject_resolved_idx" ON "control"."alert" USING btree ("subject_type","subject_id","resolved_at");--> statement-breakpoint
CREATE INDEX "budget_reservation_run_idx" ON "control"."budget_reservation" USING btree ("run_id");--> statement-breakpoint
CREATE UNIQUE INDEX "run_work_key_unique" ON "control"."run" USING btree ("work_key");--> statement-breakpoint
CREATE INDEX "run_cycle_idx" ON "control"."run" USING btree ("cycle_id");--> statement-breakpoint
CREATE INDEX "run_streamline_status_idx" ON "control"."run" USING btree ("streamline_id","status");--> statement-breakpoint
CREATE INDEX "run_event_run_at_idx" ON "control"."run_event" USING btree ("run_id","at");--> statement-breakpoint
CREATE INDEX "dump_run_idx" ON "control"."dump" USING btree ("run_id");--> statement-breakpoint
CREATE INDEX "dump_cycle_idx" ON "control"."dump" USING btree ("cycle_id");