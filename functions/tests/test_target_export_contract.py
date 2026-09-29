"""Tests for target export contract."""


from pathlib import Path

import pytest
from conftest import bound, copy_source_inputs
from mdp_functions.runs import Runtime
from mdp_functions.settings import Settings
from runtime_fixture import admin


def test_close_template_depends_on_export_every_cadence(tmp_path: Path) -> None:
    from mdp_functions.exporter import export_sources
    from mdp_functions.settings import REPO

    copy_source_inputs(tmp_path)
    export_sources(Settings(), tmp_path)
    for cadence in ("hourly", "daily", "weekly"):
        for scope, suffix in (("global", ""), ("tenant", "_tenant")):
            relative = f"dbt/models/bronze/bronze_close__{cadence}{suffix}.sql"
            if scope == "tenant":
                # Only the daily and weekly tenant jobs have tenant work (the weekly call record), so only
                # they get an export and a close.
                assert not (tmp_path / relative).exists() and not (REPO / relative).exists()
                continue
            close = (tmp_path / relative).read_text()
            assert f"ref('bronze_export__targets_{cadence}{suffix}')" in close
            assert f"'scope:{scope}'" in close
            assert f"'cadence:{cadence}'" in close
            assert "__EXPORT__" not in close
            assert close == (REPO / relative).read_text()


@pytest.mark.parametrize("adapter", ["postgres", "duckdb"])
async def test_handoff_frozen_target_presentation_per_revision(
    rt: Runtime, databases: dict[str, str], tmp_path: Path, adapter: str
) -> None:
    import duckdb
    from mdp_functions.targets import export_targets
    from mdp_functions.warehouse.duckdb import DuckDBWarehouse

    if adapter == "duckdb":
        rt.warehouse = DuckDBWarehouse(str(tmp_path / "targets.duckdb"))
        rt.override_warehouse = True
        rt.cycles.warehouse = rt.warehouse
    _, first = await bound(rt)
    admin(databases, "UPDATE control.target SET display_name='changed',role='fixture'")
    export_targets(rt.db, rt.warehouse, first["cycle_id"])
    _, second = await bound(rt)
    if adapter == "duckdb":
        with duckdb.connect(rt.warehouse.path) as conn:
            old = conn.execute(
                "SELECT display_name,role FROM raw.targets WHERE _revision_id=?",
                [str(first["revision_id"])],
            ).fetchall()
            new = conn.execute(
                "SELECT display_name,role FROM raw.targets WHERE _revision_id=?",
                [str(second["revision_id"])],
            ).fetchall()
    else:
        with rt.warehouse.connect() as conn:
            old = [
                tuple(r.values())
                for r in conn.execute(
                    "SELECT display_name,role FROM raw.targets WHERE _revision_id=%s",
                    (first["revision_id"],),
                ).fetchall()
            ]
            new = [
                tuple(r.values())
                for r in conn.execute(
                    "SELECT display_name,role FROM raw.targets WHERE _revision_id=%s",
                    (second["revision_id"],),
                ).fetchall()
            ]
    assert all(r != ("changed", "fixture") for r in old)
    assert new and all(r == ("changed", "fixture") for r in new)


def test_handoff_exporter_deterministic_dependencies_and_bootstrap(
    tmp_path: Path,
) -> None:
    import duckdb
    import yaml
    from mdp_functions.exporter import export_sources

    copy_source_inputs(tmp_path)
    files = export_sources(Settings(), tmp_path)
    before = {p: p.read_bytes() for p in files}
    assert before == {p: p.read_bytes() for p in export_sources(Settings(), tmp_path)}
    for cadence in ("hourly", "daily", "weekly"):
        close = (
            tmp_path / f"dbt/models/bronze/bronze_close__{cadence}.sql"
        ).read_text()
        assert "'close'" in close and "'invoke'" not in close
        assert f"ref('bronze_export__targets_{cadence}')" in close
        assert "cadence=" not in close
    for source in ("sp_playlist", "billboard_hot100"):
        invoke = (
            tmp_path / f"dbt/models/bronze/bronze_invoke__{source}.sql"
        ).read_text()
        assert "depends_on:" in invoke and "mdp_statement_timeout" in invoke
    raw = yaml.safe_load(
        (tmp_path / "dbt/models/sources/_raw__sources.yml").read_text()
    )["sources"][0]
    mirrors = {"cycles", "cycle_attempts", "cycle_inputs", "dump_stamps", "targets", "streamlines"}
    assert all(
        t.get("freshness") is None and t.get("loaded_at_field") is None
        for t in raw["tables"]
        if t["name"] in mirrors
    )
    bootstrap = (tmp_path / "dbt/macros/bootstrap_raw.sql").read_text()
    with duckdb.connect() as conn:
        conn.execute("CREATE SCHEMA raw")
        for line in bootstrap.splitlines():
            if 'run_query("create table' in line:
                statement = (
                    line.split('run_query("', 1)[1]
                    .rsplit('")', 1)[0]
                    .replace('\\"', '"')
                )
                conn.execute(statement)
        assert {r[0] for r in conn.execute("DESCRIBE raw.targets").fetchall()} >= {
            "display_name",
            "role",
        }
        assert conn.execute("SELECT count(*) FROM raw.streamlines").fetchone()[0] == 0


def test_handoff_exporter_matches_final_sql_stubs(tmp_path: Path) -> None:
    import shutil

    from mdp_functions.exporter import export_sources
    from mdp_functions.settings import REPO

    # The models too: tenant models' reads and meta target kinds shape the generated macros.
    shutil.copytree(REPO / "dbt/models", tmp_path / "dbt/models")
    copy_source_inputs(tmp_path)
    paths = export_sources(Settings(schema_root=REPO / "functions/schemas"), tmp_path)
    differences = [
        str(p.relative_to(tmp_path))
        for p in paths
        if p.read_bytes() != (REPO / p.relative_to(tmp_path)).read_bytes()
    ]
    assert differences == []
