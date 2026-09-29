CREATE TABLE "control"."showcase_relation_count" (
	"warehouse_id" uuid NOT NULL,
	"relation" text NOT NULL,
	"build_key" text NOT NULL,
	"captured_at" timestamp with time zone NOT NULL,
	"row_count" bigint NOT NULL,
	"latest_at" timestamp with time zone,
	"basis" text NOT NULL,
	"input_hash" text NOT NULL,
	CONSTRAINT "showcase_relation_count_warehouse_id_relation_build_key_pk" PRIMARY KEY("warehouse_id","relation","build_key"),
	CONSTRAINT "showcase_count_nonnegative" CHECK ("control"."showcase_relation_count"."row_count" >= 0),
	CONSTRAINT "showcase_count_global" CHECK ("control"."showcase_relation_count"."relation" ~ '^(marts|intermediate|staging)\.[a-z][a-z0-9_]*$'),
	CONSTRAINT "showcase_count_exact" CHECK ("control"."showcase_relation_count"."basis" = 'exact'),
	CONSTRAINT "showcase_count_identity" CHECK ("control"."showcase_relation_count"."input_hash" ~ '^[a-f0-9]{64}$' AND length("control"."showcase_relation_count"."build_key") BETWEEN 1 AND 2048),
	CONSTRAINT "showcase_count_time" CHECK ("control"."showcase_relation_count"."latest_at" IS NULL OR "control"."showcase_relation_count"."latest_at" <= "control"."showcase_relation_count"."captured_at")
);
--> statement-breakpoint
ALTER TABLE "control"."showcase_relation_count" ADD CONSTRAINT "showcase_relation_count_warehouse_id_warehouse_id_fk" FOREIGN KEY ("warehouse_id") REFERENCES "control"."warehouse"("id") ON DELETE no action ON UPDATE no action;