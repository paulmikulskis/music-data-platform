"""The deployed bootstrap's raw step on a warehouse in the 81368e9 shape: every declared raw table and
column, the gold runtime columns, the operator-loaded subject and the control mirrors exist before a
transform reads them, with no landing or export on the new image."""

from uuid import uuid4

import psycopg
from conftest import url_database
from mdp_functions.exporter import ensure_raw
from mdp_functions.settings import Settings
from psycopg import sql


def test_bootstrap_ensures_declared_tables_gold_columns_and_mirrors(databases):
    name = "mdp_test_ensure_" + uuid4().hex[:12]
    admin = databases["admin_warehouse"]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(url_database(admin, name)) as conn:
            conn.execute("CREATE SCHEMA raw AUTHORIZATION loader_wh")
            conn.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO loader_wh").format(sql.Identifier(name)))
            # Mirrors as 81368e9 left them, before the target specs and close stamps.
            conn.execute(
                "CREATE TABLE raw.targets (id uuid, platform text, platform_account_id text, handle text, "
                "display_name text, role text, target_set_id uuid, _cycle_id uuid, _revision_id uuid)"
            )
            conn.execute(
                "CREATE TABLE raw.cycles (id uuid, cadence text, scope text, opened_at timestamptz, "
                "opened_by_dbt_run_id text, closed_at timestamptz, status text, git_sha text, image_digest text)"
            )
            conn.execute("ALTER TABLE raw.targets OWNER TO loader_wh")
            conn.execute("ALTER TABLE raw.cycles OWNER TO loader_wh")
        created = ensure_raw(url_database(databases["warehouse_url"], name), Settings().schema_root)
        assert any(line.startswith("raw.targets (+") for line in created), created
        with psycopg.connect(url_database(admin, name)) as conn:
            columns: dict[str, set[str]] = {}
            for table, column in conn.execute(
                "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='raw'"
            ):
                columns.setdefault(table, set()).add(column)
            indexes = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname='raw'")}
        assert {"resource_kind", "canonical_key", "params_json", "taken_at"} <= columns["targets"]
        assert {"manifest_mode", "close_no", "global_close_no", "global_inputs"} <= columns["cycles"]
        assert {"dump_stamps", "cycle_inputs", "cycle_attempts", "streamlines"} <= set(columns)
        assert "dump_stamps_scope_target_table_close_no_idx" in indexes
        assert {"run_id", "outputs", "_dump_id"} <= columns["_run_completion"]
        assert "required_outputs" in columns["_enrichment_completion"]
        # A second bootstrap changes nothing.
        assert ensure_raw(url_database(databases["warehouse_url"], name), Settings().schema_root) == []
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
