"""tenant jobs' global inputs come from the generated mdp_global_inputs(); bind and CI enforce it."""

import os
import shutil
import subprocess
from uuid import uuid4

import psycopg
import pytest
from conftest import copy_source_inputs
from mdp_functions.errors import ServiceError
from mdp_functions.exporter import (
    declared_export_kinds,
    declared_global_inputs,
    export_sources,
)
from mdp_functions.settings import REPO, Settings

TENANT_MODEL = """{{ config(tags=['cadence:daily']) }}
select * from {{ source('raw', 'playlist_items') }}
where {{ mdp_context().manifest_filter('_dump_id', 'raw.playlist_items') }}
"""


def project(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(
        REPO / "dbt", root / "dbt", ignore=shutil.ignore_patterns("target", "logs", ".venv", "dbt_packages")
    )
    copy_source_inputs(root)
    model = root / "dbt/models/staging/tenant/stg_tenant__playlist_items.sql"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text(TENANT_MODEL)
    # A stale generated declaration: nothing is declared until the export regenerates it.
    generated = root / "dbt/macros/mdp_global_inputs.sql"
    text = generated.read_text()
    start = text.index("{% set declared = ")
    end = text.index(" %}", start)
    generated.write_text(text[:start] + '{% set declared = {"daily": [], "hourly": [], "weekly": []}' + text[end:])
    return root


def lint(root):
    return subprocess.run(
        ["bash", str(REPO / "ops/ci/lint-dbt.sh"), "-v"],
        env=os.environ | {"MDP_LINT_DBT_ROOT": str(root / "dbt"), "MDP_LINT_RULES_ONLY": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_export_generates_declarations_and_ci_refuses_undeclared_reads(tmp_path):
    root = project(tmp_path)
    refused = lint(root)
    assert refused.returncode != 0
    # The lint stops at the first tenant model whose global reads the stale macro leaves undeclared.
    assert "tenant model reads undeclared global tables ['raw." in refused.stderr
    assert "run mdp sources export" in refused.stderr
    export_sources(Settings(), root)
    # The subject staging reads the shared raw.account_snapshots (fixture_accounts beside fixture_accounts).
    daily = declared_global_inputs(root)["daily"]
    assert "raw.playlist_items" in daily
    assert declared_global_inputs(root)["hourly"] == []
    accepted = lint(root)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "PASS tenant models read only declared global inputs" in accepted.stdout


async def test_bind_refuses_a_job_row_that_differs_from_the_generated_list(rt, databases):
    scope, job = "tenant:" + str(uuid4()), "tenant-job:" + uuid4().hex
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,global_inputs) VALUES (%s,'core','daily',%s,%s)",
            (job, scope, ["raw.playlist_items"]),
        )
    with pytest.raises(ServiceError) as caught:
        await rt.cycles.bind_cycle(
            "daily", scope, "tenant:" + uuid4().hex, "scheduled", job, runner="core", global_inputs=[]
        )
    assert caught.value.error_class == "global_inputs_mismatch"
    bound = await rt.cycles.bind_cycle(
        "daily", scope, "tenant:" + uuid4().hex, "scheduled", job, runner="core",
        global_inputs=["raw.playlist_items"],
    )
    assert bound["cycle_id"]


def test_export_kinds_add_model_meta_target_kinds(tmp_path):
    root = project(tmp_path)
    model = root / "dbt/models/intermediate/tenant/int_probe_subject.sql"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text(
        "{{ config(tags=['cadence:daily', 'scope:tenant'], meta={'target_kinds': ['artist_page']}) }}\nselect 1 as x\n"
    )
    export_sources(Settings(), root)
    assert declared_export_kinds("daily", "tenant:" + str(uuid4()), root) == ["artist_page"]
    assert declared_export_kinds("daily", "global", root) == ["artist_page", "chart", "curator", "playlist", "track"]
    stub = (root / "dbt/models/bronze/bronze_export__targets_daily_tenant.sql").read_text()
    assert "mdp_export_kinds('daily', 'tenant')" in stub
