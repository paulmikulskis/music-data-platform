CREATE INDEX "cycle_opened_event_idx" ON "control"."cycle" USING btree ("opened_at",('cycle_opened:' || "id"::text) COLLATE "C");--> statement-breakpoint
CREATE INDEX "cycle_closed_event_idx" ON "control"."cycle" USING btree ("closed_at",('cycle_closed:' || "id"::text) COLLATE "C") WHERE "control"."cycle"."closed_at" IS NOT NULL;--> statement-breakpoint
CREATE INDEX "alert_opened_event_idx" ON "control"."alert" USING btree ("opened_at",('alert_opened:' || "id"::text) COLLATE "C");--> statement-breakpoint
CREATE INDEX "alert_resolved_event_idx" ON "control"."alert" USING btree ("resolved_at",('alert_resolved:' || "id"::text) COLLATE "C") WHERE "control"."alert"."resolved_at" IS NOT NULL;--> statement-breakpoint
CREATE INDEX "call_ledger_request_idx" ON "control"."call_ledger" USING btree ("request_id","id");--> statement-breakpoint
CREATE INDEX "call_ledger_run_idx" ON "control"."call_ledger" USING btree ("run_id","id");--> statement-breakpoint
CREATE INDEX "cost_ledger_current_time_idx" ON "control"."cost_ledger" USING btree ("occurred_at") WHERE "control"."cost_ledger"."is_current";--> statement-breakpoint
CREATE INDEX "run_admitted_event_idx" ON "control"."run" USING btree ("created_at",('run_admitted:' || "id"::text) COLLATE "C");--> statement-breakpoint
CREATE INDEX "run_settled_event_idx" ON "control"."run" USING btree ("updated_at",('run_settled:' || "id"::text) COLLATE "C") WHERE "control"."run"."status" IN ('succeeded','partial','failed','superseded');--> statement-breakpoint
CREATE INDEX "load_loaded_at_idx" ON "control"."load" USING btree ("loaded_at") WHERE "control"."load"."status" = 'loaded';--> statement-breakpoint
CREATE INDEX "showcase_inventory_warehouse_day_idx" ON "control"."showcase_inventory" USING btree ("warehouse","day","layer");