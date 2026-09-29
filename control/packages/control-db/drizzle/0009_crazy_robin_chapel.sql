ALTER TYPE "control"."budget_scope" ADD VALUE 'provider';--> statement-breakpoint
CREATE TABLE "control"."host_health" (
	"host" text PRIMARY KEY NOT NULL,
	"blocked_until" timestamp with time zone,
	"last_signature" text,
	"host_rps" numeric DEFAULT '1' NOT NULL,
	"http_version" text DEFAULT 'auto' NOT NULL,
	"user_agent" text,
	"robots_policy" text DEFAULT 'respect' NOT NULL,
	"robots_override_by" text,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "host_health_rate_positive" CHECK ("control"."host_health"."host_rps" > 0),
	CONSTRAINT "host_health_http_version" CHECK ("control"."host_health"."http_version" IN ('auto','1.1')),
	CONSTRAINT "host_health_robots_policy" CHECK ("control"."host_health"."robots_policy" IN ('respect','override')),
	CONSTRAINT "host_health_override_reason" CHECK ("control"."host_health"."robots_policy" <> 'override' OR length(trim("control"."host_health"."robots_override_by")) > 0 AND "control"."host_health"."robots_override_by" IS NOT NULL)
);
--> statement-breakpoint
CREATE TABLE "control"."target_spec" (
	"target_id" uuid PRIMARY KEY NOT NULL,
	"resource_kind" text NOT NULL,
	"canonical_key" text NOT NULL,
	"params_json" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"discovered_from_target_id" uuid,
	"depth" integer DEFAULT 0 NOT NULL,
	"promotion_reason" text,
	CONSTRAINT "target_spec_resource_kind" CHECK ("control"."target_spec"."resource_kind" IN ('account','curated','discovered','panel','hashtag','track','query','location','media','comment','playlist','curator','artist_page','hub','audio_asset'))
);
--> statement-breakpoint
ALTER TABLE "control"."streamline" ADD COLUMN "transport_override" text;--> statement-breakpoint
ALTER TABLE "control"."streamline" ADD COLUMN "proxy_provider" text;--> statement-breakpoint
ALTER TABLE "control"."streamline" ADD COLUMN "proxy_country" text;--> statement-breakpoint
ALTER TABLE "control"."target_export_member" ADD COLUMN "resource_kind" text;--> statement-breakpoint
ALTER TABLE "control"."target_export_member" ADD COLUMN "canonical_key" text;--> statement-breakpoint
ALTER TABLE "control"."target_export_member" ADD COLUMN "params_json" jsonb DEFAULT '{}'::jsonb NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."target_export_member" ADD COLUMN "target_json" jsonb DEFAULT '{}'::jsonb NOT NULL;--> statement-breakpoint
ALTER TABLE control.target_export_member ADD COLUMN spec_recovered boolean NOT NULL DEFAULT true;--> statement-breakpoint
ALTER TABLE control.budget ADD COLUMN cap_bytes bigint CHECK (cap_bytes >= 0);--> statement-breakpoint
ALTER TABLE "control"."call_ledger" ADD COLUMN "tier" text DEFAULT 'direct' NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."call_ledger" ADD COLUMN "provider" text;--> statement-breakpoint
ALTER TABLE "control"."call_ledger" ADD COLUMN "bytes_in" bigint DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."call_ledger" ADD COLUMN "bytes_out" bigint DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."call_ledger" ADD COLUMN "block_signature" text;--> statement-breakpoint
ALTER TABLE "control"."cost_ledger" ADD COLUMN "cost_microcents" bigint DEFAULT 0 NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."target_spec" ADD CONSTRAINT "target_spec_target_id_target_id_fk" FOREIGN KEY ("target_id") REFERENCES "control"."target"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "control"."target_spec" ADD CONSTRAINT "target_spec_discovered_from_target_id_target_id_fk" FOREIGN KEY ("discovered_from_target_id") REFERENCES "control"."target"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
GRANT SELECT ON control.host_health, control.target_spec TO functions_rt, control_rt;
GRANT INSERT(host,blocked_until,last_signature,updated_at), UPDATE(blocked_until,last_signature,updated_at) ON control.host_health TO functions_rt;
GRANT INSERT(host,host_rps,http_version,user_agent,robots_policy,robots_override_by,updated_at), UPDATE(host_rps,http_version,user_agent,robots_policy,robots_override_by,updated_at) ON control.host_health TO control_rt;
GRANT INSERT,UPDATE,DELETE ON control.target_spec TO control_rt;
GRANT UPDATE(transport_override,proxy_provider,proxy_country) ON control.streamline TO control_rt;
--> statement-breakpoint
CREATE FUNCTION control.audit_robots_override() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,control AS $$
BEGIN
  IF NEW.robots_policy='override' OR (TG_OP='UPDATE' AND OLD.robots_policy='override') THEN
    INSERT INTO control.audit_log(actor,action,subject,"before","after") VALUES
      (coalesce(nullif(NEW.robots_override_by,''),session_user),'robots_override',NEW.host,
       CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END,to_jsonb(NEW));
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER audit_robots_override AFTER INSERT OR UPDATE OF robots_policy,robots_override_by ON control.host_health FOR EACH ROW EXECUTE FUNCTION control.audit_robots_override();
REVOKE ALL ON FUNCTION control.audit_robots_override() FROM PUBLIC;
--> statement-breakpoint
CREATE FUNCTION control.pause_surface_drift() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,control AS $$
BEGIN
  IF NEW.class='surface_drift' AND NEW.severity='critical' THEN
    UPDATE control.streamline SET enabled=false,updated_at=now()
      WHERE id=(SELECT streamline_id FROM control.run WHERE id=NEW.run_id);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER pause_surface_drift AFTER INSERT ON control.alert FOR EACH ROW EXECUTE FUNCTION control.pause_surface_drift();
REVOKE ALL ON FUNCTION control.pause_surface_drift() FROM PUBLIC;
--> statement-breakpoint
INSERT INTO control.host_health(host,robots_policy,robots_override_by,http_version) VALUES
 ('open.spotify.com','respect',NULL,'auto'),
 ('music.apple.com','respect',NULL,'auto'), ('soundcloud.com','respect',NULL,'auto'),
 ('api-v2.soundcloud.com','respect',NULL,'auto'),
 ('bandcamp.com','respect',NULL,'auto'),
 ('musicbrainz.org','respect',NULL,'auto');
--> statement-breakpoint
-- Only stable identity is available locally. Mutable historical fields must come
-- from the immutable warehouse export; these placeholders are NEVER executable.
UPDATE control.target_export_member m SET target_json=jsonb_build_object(
  'id',m.target_id,'target_set_id',e.target_set_id,'platform',t.platform,
  'platform_account_id',t.platform_account_id,'handle',NULL), spec_recovered=false
FROM control.target t, control.target_export e WHERE m.target_id=t.id AND m.revision_id=e.id;
--> statement-breakpoint
-- Old exporters omit target_json. INSERT time is the export moment, so freezing
-- the live row here is correct (unlike reconstructing a historical revision).
CREATE FUNCTION control.freeze_export_member() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,control AS $$
BEGIN
  IF NEW.target_json='{}'::jsonb THEN
    SELECT to_jsonb(t),s.resource_kind,s.canonical_key,coalesce(s.params_json,'{}'::jsonb)
      INTO NEW.target_json,NEW.resource_kind,NEW.canonical_key,NEW.params_json
      FROM control.target t LEFT JOIN control.target_spec s ON s.target_id=t.id
      WHERE t.id=NEW.target_id;
    NEW.spec_recovered=true;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER freeze_export_member BEFORE INSERT ON control.target_export_member
  FOR EACH ROW EXECUTE FUNCTION control.freeze_export_member();
REVOKE ALL ON FUNCTION control.freeze_export_member() FROM PUBLIC;
--> statement-breakpoint
INSERT INTO control.runbook(slug,title,body_md) VALUES
 ('export-spec-missing','Export spec missing','Recover the frozen historical specification from immutable raw.targets using ops/fly/backfill-export-specs.py; never use live targets.'),
 ('proxy-unavailable','Proxy unavailable','Restore the configured Webshare credentials or provider service; never switch routes after a block.'),
 ('proxy-market-required','Proxy market required','Set the frozen target market or storefront before explicitly selecting residential transport.'),
 ('invalid-transport','Invalid transport','Use direct or residential in the manifest or transport override.'),
 ('scrape-blocked','Scrape blocked','Stop this host. Inspect the signature and approved public surface. Never retry through a different proxy, tier or user agent.'),
 ('surface-drift','Surface drift','Three targets missed an envelope. The streamline is paused; rerun reconnaissance and review the parser before enabling it.'),
 ('envelope-mismatch','Envelope mismatch','Inspect the frozen target and expected envelope path; retain the rejection and repair the parser.'),
 ('proxy-quota-exhausted','Proxy quota exhausted','Review the provider budget or subscription. Market requests stay paused; direct requests may continue.'),
 ('stale-target','Stale target','The public resource returned 404 or 410. Review or deactivate this target; do not retry automatically.')
 ON CONFLICT(slug) DO NOTHING;
