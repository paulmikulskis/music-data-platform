"""Tests for served materialization."""


def test_ci_refuses_a_served_view(tmp_path) -> None:
    """A served mart (one declaring meta.grain) must be a table: lint fails a view."""
    import os
    import shutil
    import subprocess

    from mdp_functions.settings import REPO

    root = tmp_path / "repo"
    shutil.copytree(
        REPO / "dbt",
        root / "dbt",
        ignore=shutil.ignore_patterns("target", "logs", ".venv", "dbt_packages"),
    )
    model = root / "dbt/models/marts/global/mart_track_daily_streams.sql"
    model.write_text(
        model.read_text().replace(
            "{{ config(tags=['cadence:daily']) }}",
            "{{ config(materialized='view', tags=['cadence:daily']) }}",
        )
    )
    refused = subprocess.run(
        ["bash", str(REPO / "ops/ci/lint-dbt.sh")],
        env=os.environ
        | {"MDP_LINT_DBT_ROOT": str(root / "dbt"), "MDP_LINT_RULES_ONLY": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert refused.returncode != 0
    assert (
        "mart_track_daily_streams: served marts must be tables, not view"
        in refused.stdout + refused.stderr
    )
