CREATE TABLE "control"."showcase_call" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"author" text NOT NULL,
	"author_kind" text NOT NULL,
	"idempotency_key" text NOT NULL,
	"song_key" text NOT NULL,
	"anchors" jsonb NOT NULL,
	"snapshot" text NOT NULL,
	"facts" jsonb NOT NULL,
	"submitted_at" timestamp with time zone DEFAULT now() NOT NULL,
	"facts_day" date NOT NULL,
	"close_no" bigint NOT NULL,
	"week_start" date NOT NULL,
	"undone_at" timestamp with time zone,
	"hidden_at" timestamp with time zone,
	"hidden_by" text,
	CONSTRAINT "showcase_call_author_idempotency_key_unique" UNIQUE("author","idempotency_key"),
	CONSTRAINT "showcase_call_author_kind_check" CHECK ("control"."showcase_call"."author_kind" IN ('ear', 'rule')),
	CONSTRAINT "showcase_call_rules_author_check" CHECK (("control"."showcase_call"."author_kind" = 'rule') = ("control"."showcase_call"."author" = 'rules'))
);
--> statement-breakpoint
CREATE INDEX "showcase_call_week_idx" ON "control"."showcase_call" USING btree ("week_start","author_kind");--> statement-breakpoint
REVOKE ALL ON control.showcase_call FROM PUBLIC, functions_rt, control_rt;
--> statement-breakpoint
GRANT SELECT, INSERT ON control.showcase_call TO control_rt;
--> statement-breakpoint
GRANT UPDATE (undone_at, hidden_at, hidden_by) ON control.showcase_call TO control_rt;
