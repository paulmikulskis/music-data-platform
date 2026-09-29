"""Tenant selection fails during compilation, before even a constant model can be built."""

import os
import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("command", ["run", "build"])
def test_tenant_scope_guard(tmp_path, command):
    (tmp_path / "models").mkdir()
    (tmp_path / "macros").mkdir()
    config = yaml.safe_load((ROOT / "dbt/dbt_project.yml").read_text())
    # Use the project's real hook, without bootstrap or cycle binding in this tiny project.
    assert config["on-run-start"][0] == "{{ mdp_scope_guard() }}"
    (tmp_path / "dbt_project.yml").write_text(yaml.safe_dump({
        "name": "scope_test", "version": "1.0.0", "config-version": 2,
        "profile": "scope_test", "on-run-start": config["on-run-start"][:1],
    }))
    shutil.copy(ROOT / "dbt/macros/mdp_scope_guard.sql", tmp_path / "macros")
    (tmp_path / "models/tenant_rows.sql").write_text(
        "{{ config(tags=['scope:tenant'], materialized='table') }} select 'synthetic' as tenant_id"
    )
    (tmp_path / "models/global_rows.sql").write_text("select 1 as n")
    database = tmp_path / "test.duckdb"
    (tmp_path / "profiles.yml").write_text(yaml.safe_dump({
        "scope_test": {"target": "ci", "outputs": {
            "ci": {"type": "duckdb", "path": str(database), "threads": 1},
        }},
    }))

    def run(scope, selection, action=command):
        return subprocess.run(
            ["uv", "run", "--project", str(ROOT / "dbt"), "dbt", *action.split(),
             "--project-dir", str(tmp_path), "--profiles-dir", str(tmp_path),
             "--select", selection],
            env={**os.environ, "DBT_MDP_SCOPE": scope}, capture_output=True, text=True, check=False,
        )

    for scope in ("global", "tenant", "tenant:"):
        result = run(scope, "tenant_rows")
        assert result.returncode != 0, result.stdout
        assert "tenant_scope_required: tenant_rows" in result.stdout
        assert "DBT_MDP_SCOPE=tenant:<tenant-id>" in result.stdout
        with duckdb.connect(str(database)) as conn:
            assert not conn.execute("select * from information_schema.tables").fetchall()
    # Parsing an unselected tenant model never breaks an ordinary global build.
    result = run("global", "global_rows")
    assert result.returncode == 0, result.stdout + result.stderr
    result = run("tenant:fixture", "tenant_rows")
    assert result.returncode == 0, result.stdout + result.stderr
    # Metadata commands may inspect tenant models without building them.
    for action in ("compile", "docs generate"):
        result = run("global", "tenant_rows", action)
        assert result.returncode == 0, result.stdout + result.stderr
