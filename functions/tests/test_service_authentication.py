"""Tests for service authentication."""

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.api import create_app
from mdp_functions.runs import Runtime
from psycopg_pool import PoolTimeout


async def test_health_bearer_openapi_and_binding_required(rt: Runtime) -> None:
    app = create_app(rt.settings, rt, recover=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/health")).json() == {"status": "ok"}
        assert (await client.get("/v1/health/detail")).status_code == 401
        client.headers["Authorization"] = "Bearer " + rt.settings.service_token
        assert (await client.get("/v1/health")).status_code == 200
        schema = (await client.get("/v1/openapi.json")).json()
        assert (
            schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
        )
        assert schema["paths"]["/v1/runs/{run_id}"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["$ref"].endswith("PollResponse")
        assert {"rows_written", "rows_rejected", "loads", "message"} <= set(
            schema["components"]["schemas"]["ReceiptResponse"]["properties"]
        )
        for path, payload in (
            (
                "/v1/invoke",
                {"source_key": "billboard_hot100", "manual": True, "scope": "global"},
            ),
            ("/v1/functions/billboard_hot100/run", {}),
        ):
            response = await client.post(path, json=payload)
            assert (
                response.status_code == 409
                and response.json()["error_class"] == "scope_mismatch"
            )


@pytest.mark.parametrize(
    "error,kind",
    [
        (PoolTimeout("busy"), "control_db_unavailable"),
        (OSError("store offline"), "object_store_unavailable"),
    ],
)
async def test_api_subsystem_error_envelopes(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, error: Exception, kind: str
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise error

    monkeypatch.setattr(rt, "receipts", broken)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        response = await client.get("/v1/runs/" + str(uuid4()))
        assert response.status_code == 503 and response.json()["error_class"] == kind


async def test_warehouse_preview_failure_is_not_control_failure(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise psycopg.OperationalError("warehouse unavailable")

    monkeypatch.setattr("mdp_functions.api.psycopg.connect", broken)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        metadata = await client.get("/v1/functions/fixture_accounts?metadata_only=true")
        assert metadata.status_code == 200
        assert metadata.json()["output_preview"] == []
        assert metadata.json()["rejected_sample"] == []
        response = await client.get("/v1/functions/fixture_accounts")
        assert (
            response.status_code == 503
            and response.json()["error_class"] == "warehouse_unavailable"
        )


async def test_function_metadata_never_builds_receipts(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = await bound(rt)
    receipt_runs = []
    build_receipts = rt.receipts

    def tracked_receipts(run_id: Any) -> dict[str, Any]:
        receipt_runs.append(run_id)
        return build_receipts(run_id)

    monkeypatch.setattr(rt, "receipts", tracked_receipts)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        metadata = await client.get("/v1/functions/fixture_accounts?metadata_only=true")
        assert metadata.status_code == 200
        assert metadata.json()["last_runs"][0]["id"] == str(run["id"])
        assert metadata.json()["receipts"] == []
        assert receipt_runs == []

        full = await client.get("/v1/functions/fixture_accounts")
        assert full.status_code == 200
        assert receipt_runs == [run["id"]]
        assert full.json()["receipts"][0][0]["run_id"] == str(run["id"])


async def test_docs_openapi_health_detail_share_bearer_authentication(rt: Runtime) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
    ) as client:
        for path in ("/v1/health/detail", "/v1/docs", "/v1/openapi.json"):
            assert (await client.get(path)).status_code == 401
            assert (
                await client.get(
                    path,
                    headers={"Authorization": "Bearer " + rt.settings.service_token},
                )
            ).status_code == 200


async def test_live_health_binding_invoke_and_restricted_grants(rt: Runtime) -> None:
    import hashlib

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(rt.settings, rt, recover=False),
            host="127.0.0.1",
            port=0,
            log_level="error",
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": "Bearer " + rt.settings.service_token},
        ) as client:
            assert (
                await client.get("/v1/health", headers={"Authorization": ""})
            ).json() == {"status": "ok"}
            assert (await client.get("/v1/health/detail")).json() == {
                "control_db": "ok",
                "warehouse": "ok",
                "object_store": "ok",
            }
            dbt_id = uuid4().hex
            bound_response = await client.post(
                "/v1/bind_cycle",
                json={
                    "cadence": "weekly",
                    "scope": "global",
                    "dbt_run_id": dbt_id,
                    "reason_category": "scheduled",
                    "job_id": "local:weekly",
                    "runner": "core",
                    "global_inputs": [],
                },
            )
            assert bound_response.status_code == 200
            key = hashlib.sha256(f"billboard_hot100|{dbt_id}|||".encode()).hexdigest()
            admitted = await client.post(
                "/v1/invoke",
                json={"source_key": "billboard_hot100", "dbt_run_id": dbt_id},
                headers={"Idempotency-Key": key},
            )
            assert admitted.status_code == 202
            run_id = admitted.json()["run_id"]
            async with asyncio.timeout(10):
                while True:
                    response = (await client.get("/v1/runs/" + run_id)).json()
                    if response["run"]["status"] == "succeeded":
                        break
                    await asyncio.sleep(0.02)
            assert response["receipts"][0]["rows_written"] == "3"
        grants = rt.db.one(
            "SELECT has_table_privilege(current_user,'control.run','INSERT') AS runs, has_table_privilege(current_user,'control.runner_mode','UPDATE') AS config, has_table_privilege(current_user,'control.runbook','INSERT') AS runbooks, has_column_privilege(current_user,'control.alert','runbook_slug','INSERT') AS alerts"
        )
        assert grants == {
            "runs": True,
            "config": False,
            "runbooks": False,
            "alerts": True,
        }
        with psycopg.connect(rt.settings.service_read_url) as conn:
            assert conn.execute(
                "SELECT has_table_privilege(current_user,'raw._load_receipts','SELECT'),has_table_privilege(current_user,'raw._load_receipts','INSERT')"
            ).fetchone() == (True, False)
    finally:
        server.should_exit = True
        await task
