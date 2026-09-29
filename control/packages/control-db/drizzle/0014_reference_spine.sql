CREATE TABLE "control"."reference_source" (
	"source" text PRIMARY KEY NOT NULL,
	"imported_generation" text,
	"export_date" timestamp with time zone,
	"replication_sequence" bigint,
	"imported_at" timestamp with time zone,
	"import_generation" text,
	"import_state" text,
	"import_phase" text,
	"import_started_at" timestamp with time zone,
	"import_finished_at" timestamp with time zone,
	"import_message" text,
	"landed_generation" text,
	"landed_at" timestamp with time zone,
	"landed_reconciled" boolean,
	"mirror_counts" jsonb,
	"landed_counts" jsonb,
	"disk_used_bytes" bigint,
	"disk_total_bytes" bigint,
	"probed_at" timestamp with time zone,
	"probe_error" text,
	"reimport_requested_at" timestamp with time zone,
	"reimport_requested_by" text,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "control"."cycle" ADD COLUMN "tenant_close_nos" jsonb DEFAULT '{}'::jsonb NOT NULL;--> statement-breakpoint
-- The reference probe (functions_rt) owns the mirror and landing state; control_rt only records a
-- re-import request, which the refresh path reads.
GRANT INSERT, UPDATE ON control.reference_source TO functions_rt;--> statement-breakpoint
GRANT UPDATE(reimport_requested_at,reimport_requested_by,updated_at) ON control.reference_source TO control_rt;--> statement-breakpoint
INSERT INTO control.reference_source(source) VALUES ('musicbrainz') ON CONFLICT DO NOTHING;
--> statement-breakpoint
-- Alert resolution is control-owned; the one exception is the reference probe clearing a reference
-- class once its design clear condition holds, through this definer function and nothing wider.
CREATE FUNCTION control.resolve_reference_alert(p_class text, p_source text) RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, control AS $$
  WITH resolved AS (
    UPDATE control.alert SET resolved_at = now(), updated_at = now()
    WHERE class = p_class AND class IN ('reference_generation_incomplete', 'reference_disk_high', 'reference_dump_stale', 'reference_import_failed')
      AND subject_type = 'reference_source' AND subject_id = p_source AND resolved_at IS NULL
    RETURNING 1)
  SELECT count(*)::integer FROM resolved
$$;--> statement-breakpoint
REVOKE ALL ON FUNCTION control.resolve_reference_alert(text, text) FROM PUBLIC;--> statement-breakpoint
GRANT EXECUTE ON FUNCTION control.resolve_reference_alert(text, text) TO functions_rt;
