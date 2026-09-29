"""Cycle binding checks read the warehouse even while dbt's relation cache is incomplete."""

import os
import subprocess
from datetime import UTC, datetime

import pytest
from identity_harness import REPO, Warehouse

pytestmark = pytest.mark.docker


@pytest.mark.parametrize("state", ["bound", "unbound", "missing_table", "open"])
def test_cycle_context_with_partial_relation_cache(tmp_path, state):
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    wh = Warehouse(admin, tmp_path)
    try:
        cycle = wh.cycle("daily", 1, datetime(2026, 9, 24, tzinfo=UTC))
        if state == "unbound":
            wh.query("DELETE FROM raw.cycle_attempts RETURNING dbt_run_id")
        elif state == "missing_table":
            with wh.connect() as conn:
                conn.execute("DROP TABLE raw.cycle_attempts")
        elif state == "open":
            wh.query("UPDATE raw.cycles SET close_no = NULL, status = 'open' RETURNING id")

        # A small dbt project runs the real macro and adapter. cache_added reproduces the window
        # in list_relations: the first raw relation marks the schema cached before the rest arrive.
        project = tmp_path / "project"
        macros = project / "macros"
        macros.mkdir(parents=True)
        (project / "dbt_project.yml").write_text(
            "name: context_test\nversion: '1.0'\nconfig-version: 2\nprofile: music_data_platform\n"
        )
        for name in ("mdp_context.sql", "mdp_local_week.sql"):
            (macros / name).write_text((REPO / "dbt/macros" / name).read_text())
        (macros / "check_context.sql").write_text("""
{% macro check_context() %}
  {% do adapter.cache_added(api.Relation.create(database=target.database, schema='raw', identifier='mb_generation')) %}
  {% if adapter.get_relation(database=target.database, schema='raw', identifier='cycle_attempts') is not none %}
    {{ exceptions.raise_compiler_error('test requires an incomplete cache') }}
  {% endif %}
  {% set context = mdp_context() %}
  {% do log('bound cycle: ' ~ context.cycle_id, info=true) %}
{% endmacro %}
""")
        result = subprocess.run(
            ["uv", "run", "--project", "dbt", "dbt", "run-operation", "check_context",
             "--project-dir", str(project), "--profiles-dir", str(wh.profiles), "--target", "pg_local"],
            cwd=REPO, env={**os.environ, "DBT_CLOUD_RUN_ID": cycle["run_id"], "DBT_MDP_CADENCE": "daily"},
            capture_output=True, text=True, check=False,
        )
        output = result.stdout + result.stderr
        if state == "bound":
            assert result.returncode == 0, output
            assert "bound cycle: " + cycle["id"] in output
        else:
            assert result.returncode != 0, output
            error = "cycle_not_closed" if state == "open" else "no_cycle_binding"
            assert error in output, output
    finally:
        wh.drop()
