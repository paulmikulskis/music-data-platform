CREATE TYPE "control"."manifest_mode" AS ENUM('list', 'stamp');--> statement-breakpoint
CREATE TABLE "control"."runner_restore" (
	"lock_key" text PRIMARY KEY NOT NULL,
	"cycle_id" uuid NOT NULL,
	"target" text NOT NULL,
	"vars" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"requested_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "control"."scope_close" (
	"scope" text PRIMARY KEY NOT NULL,
	"last_close_no" bigint DEFAULT 0 NOT NULL,
	"mirrored_close_no" bigint DEFAULT -1 NOT NULL
);
--> statement-breakpoint
ALTER TABLE "control"."llm_step" ALTER COLUMN "config_version" DROP NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."dbt_job" ADD COLUMN "global_inputs" text[] DEFAULT '{}' NOT NULL;--> statement-breakpoint
-- Expand and contract: step_version is added beside config_version, never renamed from it, so a
-- service still on the previous image keeps reading and writing config_version until it is replaced.
-- The trigger fills whichever name a writer leaves null. A later release drops config_version, the
-- llm_step_source_config_unique index, the trigger and control.llm_step_version_sync().
ALTER TABLE "control"."llm_step" ADD COLUMN "step_version" text;--> statement-breakpoint
UPDATE control.llm_step SET step_version=config_version;--> statement-breakpoint
ALTER TABLE "control"."llm_step" ALTER COLUMN "step_version" SET NOT NULL;--> statement-breakpoint
CREATE FUNCTION control.llm_step_version_sync() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,control AS $$
BEGIN
  NEW.step_version := coalesce(NEW.step_version, NEW.config_version);
  NEW.config_version := coalesce(NEW.config_version, NEW.step_version);
  RETURN NEW;
END $$;--> statement-breakpoint
CREATE TRIGGER llm_step_version_sync BEFORE INSERT ON control.llm_step FOR EACH ROW EXECUTE FUNCTION control.llm_step_version_sync();--> statement-breakpoint
-- The default is list: a cycle the previous image opens during the deploy window closes with its full
-- list. The current image's bind inserts stamp explicitly.
ALTER TABLE "control"."cycle" ADD COLUMN "manifest_mode" "control"."manifest_mode" DEFAULT 'list' NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."cycle" ADD COLUMN "close_no" bigint;--> statement-breakpoint
ALTER TABLE "control"."cycle" ADD COLUMN "global_close_no" bigint;--> statement-breakpoint
ALTER TABLE "control"."cycle" ADD COLUMN "global_inputs" text[] DEFAULT '{}' NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."cycle_attempt" ADD COLUMN "stamp_protocol" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "control"."cycle_input" ADD COLUMN "mirrored_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "control"."dump" ADD COLUMN "scope" text;--> statement-breakpoint
ALTER TABLE "control"."dump" ADD COLUMN "close_no" bigint;--> statement-breakpoint
ALTER TABLE "control"."dump" ADD COLUMN "rejected_at" timestamp with time zone;--> statement-breakpoint
ALTER TABLE "control"."runner_restore" ADD CONSTRAINT "runner_restore_cycle_id_cycle_id_fk" FOREIGN KEY ("cycle_id") REFERENCES "control"."cycle"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE UNIQUE INDEX "llm_step_source_step_unique" ON "control"."llm_step" USING btree ("source_key","step_version");--> statement-breakpoint
CREATE UNIQUE INDEX "cycle_scope_close_no_unique" ON "control"."cycle" USING btree ("scope","close_no") WHERE "control"."cycle"."manifest_mode" = 'stamp';--> statement-breakpoint
CREATE INDEX "cycle_input_dump_idx" ON "control"."cycle_input" USING btree ("dump_id");--> statement-breakpoint
CREATE INDEX "cycle_input_unmirrored_idx" ON "control"."cycle_input" USING btree ("cycle_id") WHERE "control"."cycle_input"."mirrored_at" IS NULL;--> statement-breakpoint
CREATE INDEX "dump_unstamped_idx" ON "control"."dump" USING btree ("scope") WHERE "control"."dump"."close_no" IS NULL AND "control"."dump"."kind" = 'output' AND "control"."dump"."quarantined_at" IS NULL AND "control"."dump"."rejected_at" IS NULL;--> statement-breakpoint
CREATE INDEX "dump_scope_close_idx" ON "control"."dump" USING btree ("scope","close_no") WHERE "control"."dump"."close_no" IS NOT NULL;--> statement-breakpoint
-- Every dump carries its run's scope, so a close finds the scope's unstamped dumps by index.
UPDATE control.dump d SET scope=r.scope FROM control.run r WHERE r.id=d.run_id AND d.scope IS NULL;--> statement-breakpoint
CREATE FUNCTION control.dump_scope() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,control AS $$
BEGIN
  IF NEW.scope IS NULL THEN
    SELECT r.scope INTO NEW.scope FROM control.run r WHERE r.id=NEW.run_id;
  END IF;
  RETURN NEW;
END $$;--> statement-breakpoint
CREATE TRIGGER dump_scope BEFORE INSERT ON control.dump FOR EACH ROW EXECUTE FUNCTION control.dump_scope();--> statement-breakpoint
-- Cycles closed before close stamps keep their per-cycle lists, which the old mirror already carried,
-- and take close_no 0 so stamp-mode revision reads count them.
UPDATE control.cycle SET manifest_mode='list',close_no=0 WHERE status='closed';--> statement-breakpoint
UPDATE control.cycle_input i SET mirrored_at=i.added_at FROM control.cycle c
WHERE c.id=i.cycle_id AND c.close_no=0;--> statement-breakpoint
-- A dump whose pinned-warehouse load is terminally rejected leaves the unstamped index until a
-- repair lands it.
UPDATE control.dump d SET rejected_at=now() FROM control.run r
WHERE r.id=d.run_id AND d.kind='output'
AND EXISTS (SELECT 1 FROM control.load l WHERE l.dump_id=d.id AND l.warehouse_id=r.warehouse_id AND l.status='rejected');--> statement-breakpoint
-- Every dump whose load to its run's pinned warehouse committed before close stamps is in every
-- stamp-mode manifest.
UPDATE control.dump d SET close_no=0 FROM control.run r
WHERE r.id=d.run_id AND d.kind='output' AND d.close_no IS NULL AND d.quarantined_at IS NULL
AND EXISTS (SELECT 1 FROM control.load l WHERE l.dump_id=d.id AND l.warehouse_id=r.warehouse_id AND l.status='loaded');--> statement-breakpoint
-- mirrored_close_no starts at -1, so the first catch-up writes the close-0 stamps and raw.cycles rows
-- before it reaches 0.
INSERT INTO control.scope_close(scope)
SELECT scope FROM control.dump WHERE scope IS NOT NULL UNION SELECT scope FROM control.cycle
ON CONFLICT(scope) DO NOTHING;--> statement-breakpoint
-- D6 manifest. list: the cycle's rows. stamp: the scope's dumps stamped through close_no, a tenant
-- cycle's declared global tables stamped through global_close_no, and the cycle's derived rows.
CREATE FUNCTION control.cycle_manifest(cycle_ref uuid) RETURNS TABLE(dump_id uuid)
LANGUAGE sql STABLE SET search_path=pg_catalog,control AS $$
  SELECT i.dump_id FROM control.cycle_input i WHERE i.cycle_id=cycle_ref
  UNION
  SELECT d.id FROM control.cycle c JOIN control.dump d ON d.scope=c.scope AND d.close_no<=c.close_no
  WHERE c.id=cycle_ref AND c.manifest_mode='stamp'
  UNION
  SELECT d.id FROM control.cycle c JOIN control.dump d ON d.scope='global' AND d.close_no<=c.global_close_no
  WHERE c.id=cycle_ref AND c.manifest_mode='stamp' AND c.scope<>'global'
  AND EXISTS (SELECT 1 FROM control.load l WHERE l.dump_id=d.id AND l.target_table=ANY(c.global_inputs))
$$;--> statement-breakpoint
GRANT INSERT, UPDATE ON control.scope_close TO functions_rt;--> statement-breakpoint
GRANT INSERT, UPDATE, DELETE ON control.runner_restore TO control_rt;
