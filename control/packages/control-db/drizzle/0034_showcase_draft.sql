SET LOCAL lock_timeout = '2s';
--> statement-breakpoint
CREATE TABLE "control"."showcase_call_rule" (
	"call_id" uuid NOT NULL,
	"rule_id" text NOT NULL,
	CONSTRAINT "showcase_call_rule_call_id_rule_id_pk" PRIMARY KEY("call_id","rule_id")
);
--> statement-breakpoint
CREATE TABLE "control"."showcase_draft" (
	"week_start" date PRIMARY KEY NOT NULL,
	"opens_at" timestamp with time zone NOT NULL,
	"closes_at" timestamp with time zone NOT NULL,
	"candidates" jsonb NOT NULL,
	"rules" jsonb NOT NULL,
	"closed_at" timestamp with time zone,
	"closed_by" text,
	"close_key" text
);
--> statement-breakpoint
CREATE TABLE "control"."showcase_rule" (
	"id" text PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"conditions" jsonb NOT NULL,
	"limit_per_draft" integer NOT NULL,
	"created_by" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"backed_by" text,
	"backed_at" timestamp with time zone,
	"retired_at" timestamp with time zone,
	"drafted_from_call" uuid
);
--> statement-breakpoint
ALTER TABLE "control"."showcase_call" ADD COLUMN "draft_week" date;--> statement-breakpoint
ALTER TABLE "control"."showcase_call_rule" ADD CONSTRAINT "showcase_call_rule_call_id_showcase_call_id_fk" FOREIGN KEY ("call_id") REFERENCES "control"."showcase_call"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."showcase_call_rule" ADD CONSTRAINT "showcase_call_rule_rule_id_showcase_rule_id_fk" FOREIGN KEY ("rule_id") REFERENCES "control"."showcase_rule"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."showcase_rule" ADD CONSTRAINT "showcase_rule_drafted_from_call_showcase_call_id_fk" FOREIGN KEY ("drafted_from_call") REFERENCES "control"."showcase_call"("id") ON DELETE no action ON UPDATE no action;
--> statement-breakpoint
REVOKE ALL ON control.showcase_rule, control.showcase_draft, control.showcase_call_rule FROM PUBLIC, functions_rt, control_rt;
--> statement-breakpoint
GRANT SELECT, INSERT ON control.showcase_rule, control.showcase_draft, control.showcase_call_rule TO control_rt;
--> statement-breakpoint
GRANT UPDATE (rules, closed_at, closed_by, close_key) ON control.showcase_draft TO control_rt;
--> statement-breakpoint
GRANT UPDATE (backed_by, backed_at) ON control.showcase_rule TO control_rt;
