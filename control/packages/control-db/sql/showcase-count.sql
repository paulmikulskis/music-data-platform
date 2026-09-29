-- Bootstrap runs this after the additive table migration.
REVOKE ALL ON control.showcase_relation_count FROM PUBLIC, functions_rt, control_rt;
GRANT SELECT, INSERT ON control.showcase_relation_count TO control_rt;
-- A published capture keeps its warehouse, relation, build and input identity.
GRANT UPDATE (captured_at, row_count, latest_at) ON control.showcase_relation_count TO control_rt;
