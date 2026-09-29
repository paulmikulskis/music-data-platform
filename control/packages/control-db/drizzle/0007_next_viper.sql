CREATE TABLE "control"."api_key" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"key_hash" text NOT NULL,
	"label" text NOT NULL,
	"tenant_id" uuid,
	"role" text DEFAULT 'reader' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"expires_at" timestamp with time zone,
	"revoked_at" timestamp with time zone,
	CONSTRAINT "api_key_key_hash_unique" UNIQUE("key_hash")
);
--> statement-breakpoint
ALTER TABLE "control"."api_key" ADD CONSTRAINT "api_key_tenant_id_tenant_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "control"."tenant"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE control.api_key ADD CONSTRAINT api_key_hash_format CHECK (key_hash ~ '^[0-9a-f]{64}$');
--> statement-breakpoint
ALTER TABLE control.api_key ADD CONSTRAINT api_key_role CHECK (role IN ('admin','reader'));
--> statement-breakpoint
REVOKE ALL ON control.api_key FROM PUBLIC, functions_rt;
--> statement-breakpoint
GRANT SELECT, INSERT, UPDATE, DELETE ON control.api_key TO control_rt;
