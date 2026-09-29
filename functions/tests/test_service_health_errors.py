"""Tests for service health errors."""


from typing import Any
from uuid import uuid4

import duckdb
import httpx
import psycopg
import pytest
import tomllib
from conftest import bound
from mdp_functions.api import create_app
from mdp_functions.errors import ServiceError
from mdp_functions.runs import Runtime
from mdp_functions.settings import REPO
from mdp_functions.warehouse.postgres import PostgresWarehouse
from psycopg_pool import PoolTimeout


@pytest.mark.parametrize("subsystem", [None, "control_db", "warehouse", "object_store"])
async def test_fly_public_health_and_authenticated_detail(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, subsystem: str | None
) -> None:
    if subsystem:

        def broken(*args: Any) -> None:
            raise OSError("unavailable")

        target, method = {
            "control_db": (rt.db, "one"),
            "warehouse": (rt.warehouse, "health"),
            "object_store": (rt.store, "health"),
        }[subsystem]
        monkeypatch.setattr(target, method, broken)
    config = tomllib.loads((REPO / "functions/fly.toml").read_text())
    path = config.get("http_service", {"checks": [{"path": "/v1/health"}]})["checks"][0]["path"]
    assert path == "/v1/health"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
    ) as client:
        response = await client.get(path)
        assert response.status_code == (503 if subsystem else 200)
        assert response.json() == {"status": "degraded" if subsystem else "ok"}
        assert (await client.get(path + "/detail")).status_code == 401
        client.headers["Authorization"] = "Bearer " + rt.settings.service_token
        detail = await client.get(path + "/detail")
        assert detail.status_code == response.status_code
        assert detail.json() == {
            name: "unavailable" if name == subsystem else "ok"
            for name in ("control_db", "warehouse", "object_store")
        }
        schema = (await client.get("/v1/openapi.json")).json()
        assert schema["paths"][path]["get"]["security"] == []
        assert schema["paths"][path + "/detail"]["get"]["security"]


@pytest.mark.parametrize("route", ["bind", "invoke", "manual"])
@pytest.mark.parametrize(
    "error",
    [
        psycopg.OperationalError("offline"),
        PoolTimeout("busy"),
        OSError("offline"),
        duckdb.IOException("offline"),
    ],
)
async def test_api_warehouse_mirror_failure_envelope(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, route: str, error: Exception
) -> None:
    dbt_id, _ = await bound(rt, "billboard_hot100")

    def broken(*args: Any, **kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(PostgresWarehouse, "mirror", broken)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        if route == "bind":
            response = await client.post(
                "/v1/bind_cycle",
                json={
                    "cadence": "weekly",
                    "scope": "global",
                    "dbt_run_id": str(uuid4()),
                    "reason_category": "scheduled",
                    "job_id": "local:weekly",
                    "runner": "core",
                    "global_inputs": [],
                },
            )
        elif route == "manual":
            response = await client.post(
                "/v1/functions/billboard_hot100/run", params={"dbt_run_id": dbt_id}
            )
        else:
            response = await client.post(
                "/v1/invoke",
                json={"source_key": "billboard_hot100", "dbt_run_id": dbt_id},
            )
        assert response.status_code == 503
        assert response.json() == {
            "error_class": "warehouse_unavailable",
            "message": "Warehouse mirror is unavailable",
            "next_step": ServiceError("warehouse_unavailable", "").next_step,
            "runbook": "/runbooks/warehouse-unavailable",
        }
        assert not rt.tasks


def test_api_vm_is_dedicated_cpu() -> None:
    """The api machine keeps a dedicated core: a shared CPU spends its burst credit in the daily bronze
    burst and throttles to its baseline, which slows landing, admission and run polls alike."""
    config = tomllib.loads((REPO / "functions/fly.toml").read_text())
    api = next(vm for vm in config["vm"] if vm["processes"] == ["api"])
    assert api["cpu_kind"] == "performance"
    # Fly sizes performance machines at 2 GB or more for each CPU.
    assert int(api["memory"].removesuffix("gb")) >= 2 * api["cpus"]
