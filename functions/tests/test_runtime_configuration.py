"""Tests for runtime configuration."""


from pathlib import Path

import psycopg
import pytest
from conftest import bound
from mdp_functions.registry import sync
from mdp_functions.runs import Runtime
from mdp_functions.settings import Settings
from runtime_fixture import admin


def test_local_settings_require_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("MDP_CONTROL_URL", "MDP_WAREHOUSE_URL", "MDP_SERVICE_READ_URL"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="Explicit environment"):
        Settings().local()


@pytest.mark.parametrize("adapter", ["postgres", "duckdb"])
async def test_handoff_streamline_mirror_startup_sync_duplicate_and_paused(
    rt: Runtime, databases: dict[str, str], tmp_path: Path, adapter: str
) -> None:
    import duckdb
    from mdp_functions.warehouse.duckdb import DuckDBWarehouse

    if adapter == "duckdb":
        rt.warehouse = DuckDBWarehouse(str(tmp_path / "mirrors.duckdb"))
        rt.override_warehouse = True
        rt.cycles.warehouse = rt.warehouse
    rt.initialize()

    def knobs() -> tuple:
        if adapter == "duckdb":
            with duckdb.connect(rt.warehouse.path) as conn:
                return conn.execute(
                    "SELECT enabled,allow_partial,timeout_s FROM raw.streamlines WHERE source_key='billboard_hot100'"
                ).fetchone()
        with rt.warehouse.connect() as conn:
            row = conn.execute(
                "SELECT enabled,allow_partial,timeout_s FROM raw.streamlines WHERE source_key='billboard_hot100'"
            ).fetchone()
            return row["enabled"], row["allow_partial"], row["timeout_s"]

    assert knobs()[0] is True
    dbt_id, run = await bound(rt, "billboard_hot100")
    admin(
        databases,
        "UPDATE control.streamline SET enabled=false,allow_partial=true,timeout_s=123 WHERE source_key='billboard_hot100'",
    )
    assert rt.admit("billboard_hot100", dbt_run_id=dbt_id)["id"] == run["id"]
    assert knobs() == (False, True, 123)
    paused = rt.admit("billboard_hot100", dbt_run_id=dbt_id, manual=True)
    assert rt.receipts(paused["id"])["receipts"][0]["status"] == "paused"
    assert rt.receipts(paused["id"])["receipts"][0]["allow_partial"] is True
    admin(
        databases,
        "UPDATE control.streamline SET timeout_s=124 WHERE source_key='billboard_hot100'",
    )
    sync(rt.db, rt.warehouse)
    assert knobs() == (False, True, 124)


async def test_runbook_configuration_is_idempotent_and_alerts_reference_it(
    rt: Runtime, databases: dict[str, str]
) -> None:
    from mdp_functions.control_db import alert
    from mdp_functions.runbooks import RUNBOOKS, seed_runbooks

    with psycopg.connect(databases["admin_control"]) as conn:
        seed_runbooks(conn)
        seed_runbooks(conn)
        assert conn.execute("SELECT count(*) FROM control.runbook").fetchone()[
            0
        ] == len(RUNBOOKS)
    _, run = await bound(rt, "billboard_hot100")
    with rt.db.transaction() as conn:
        alert(conn, run["id"], "schema_breaking", str(run["id"]))
    assert (
        rt.db.one(
            "SELECT runbook_slug FROM control.alert WHERE run_id=%s", (run["id"],)
        )["runbook_slug"]
        == "schema-breaking"
    )
