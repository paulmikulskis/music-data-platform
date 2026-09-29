-- Custom migration: the role check is owned by the earlier custom key migrations.
ALTER TABLE control.api_key DROP CONSTRAINT api_key_role;--> statement-breakpoint
ALTER TABLE control.api_key ADD CONSTRAINT api_key_role CHECK (role IN ('admin','reader','promoter','staff'));--> statement-breakpoint
ALTER TABLE control.api_key ADD CONSTRAINT api_key_staff_scope CHECK (role <> 'staff' OR tenant_id IS NULL);--> statement-breakpoint
ALTER TABLE control.api_key ADD CONSTRAINT api_key_warehouse_role CHECK (warehouse_role IS NULL OR (role = 'staff' AND warehouse_role ~ '^analyst_[a-z][a-z0-9_]{0,47}$' AND warehouse_role <> 'analyst_ro'));
