-- The mdp-functions promoters' key (role 'promoter') reaches only the target commands they call;
-- every other control route needs an admin identity, and the data API refuses it.
ALTER TABLE control.api_key DROP CONSTRAINT api_key_role;--> statement-breakpoint
ALTER TABLE control.api_key ADD CONSTRAINT api_key_role CHECK (role IN ('admin','reader','promoter'));
