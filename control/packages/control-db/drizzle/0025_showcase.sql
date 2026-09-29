CREATE TABLE "control"."showcase_actor" (
	"api_key_id" uuid PRIMARY KEY NOT NULL,
	"handle" text NOT NULL,
	"display_name" text NOT NULL,
	"first_seen_at" timestamp with time zone DEFAULT now() NOT NULL,
	"retired_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "control"."showcase_inventory" (
	"day" date NOT NULL,
	"warehouse" text NOT NULL,
	"layer" text NOT NULL,
	"relations" integer NOT NULL,
	"rows_est" bigint,
	"bytes" bigint NOT NULL,
	"captured_at" timestamp with time zone NOT NULL,
	"complete" boolean NOT NULL,
	CONSTRAINT "showcase_inventory_day_warehouse_layer_pk" PRIMARY KEY("day","warehouse","layer")
);
--> statement-breakpoint
CREATE TABLE "control"."showcase_link" (
	"nonce" text PRIMARY KEY NOT NULL,
	"handle" text NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"used_at" timestamp with time zone,
	"revoked_at" timestamp with time zone,
	"created_by" text NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."showcase_seen" (
	"handle" text NOT NULL,
	"scope" text NOT NULL,
	"close_no" bigint NOT NULL,
	"seen_at" timestamp with time zone NOT NULL,
	CONSTRAINT "showcase_seen_handle_scope_pk" PRIMARY KEY("handle","scope")
);
--> statement-breakpoint
CREATE TABLE "control"."showcase_session" (
	"id_hash" text PRIMARY KEY NOT NULL,
	"handle" text NOT NULL,
	"csrf_token" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_seen_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"revoked_at" timestamp with time zone,
	"user_agent" text
);
--> statement-breakpoint
CREATE TABLE "control"."showcase_share" (
	"slug" text PRIMARY KEY NOT NULL,
	"handle" text NOT NULL,
	"query_id" text NOT NULL,
	"class" text NOT NULL,
	"params" jsonb NOT NULL,
	"payload" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"revoked_at" timestamp with time zone,
	"views" integer DEFAULT 0 NOT NULL
);
