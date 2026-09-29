-- The data API's one control credential: key checks read api_key and tenant, nothing else.
-- roles.sql creates the role; run it before this migration on an existing cluster.
GRANT USAGE ON SCHEMA control TO api_key_reader;--> statement-breakpoint
GRANT SELECT ON control.api_key, control.tenant TO api_key_reader;
