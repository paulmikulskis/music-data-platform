ALTER TABLE control.llm_step ALTER COLUMN config_version TYPE text USING config_version::text;
--> statement-breakpoint
ALTER TABLE control.run ADD COLUMN config_version text, ADD COLUMN input_relation text;
--> statement-breakpoint
ALTER TABLE control.workbench_run ADD COLUMN result jsonb, ADD COLUMN error jsonb;
--> statement-breakpoint
GRANT INSERT, UPDATE, DELETE ON control.workbench_session TO control_rt;
--> statement-breakpoint
CREATE UNIQUE INDEX prompt_name_version_unique ON control.prompt(name, version);
--> statement-breakpoint
CREATE UNIQUE INDEX llm_step_source_config_unique ON control.llm_step(source_key, config_version);
