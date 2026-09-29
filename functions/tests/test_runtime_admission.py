"""Tests for runtime admission."""


import hashlib
from uuid import uuid4

import httpx
import pytest
from conftest import bound
from mdp_functions import admission
from mdp_functions.api import create_app
from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.runs import Runtime
from runtime_fixture import admin


async def test_paused_receipt_without_attempt_or_vendor_work(
    rt: Runtime, databases: dict[str, str]
) -> None:
    admin(
        databases,
        "UPDATE control.streamline SET enabled=false WHERE source_key='billboard_hot100'",
    )
    _, run = await bound(rt, "billboard_hot100")
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])
    assert result["run"]["error_class"] == "paused"
    assert result["receipts"][0]["status"] == "paused"
    assert result["receipts"][0]["coverage"] == "empty"
    for table in ("batch", "run_attempt", "call_ledger"):
        assert rt.db.one(f"SELECT count(*) AS n FROM control.{table}")["n"] == 0


async def test_manual_idempotency_conflicts_across_source_scope_and_inputs(
    rt: Runtime,
) -> None:
    dbt_id, run = await bound(rt, "billboard_hot100")
    key = uuid4().hex
    first = rt.admit("billboard_hot100", manual=True, dbt_run_id=dbt_id, key=key)
    assert (
        rt.admit("billboard_hot100", manual=True, dbt_run_id=dbt_id, key=key)["id"]
        == first["id"]
    )
    with pytest.raises(ServiceError, match="another function or scope"):
        rt.admit("cycle_close", manual=True, dbt_run_id=dbt_id, key=key)
    cycle = rt.db.one("SELECT * FROM control.cycle WHERE id=%s", (run["cycle_id"],))
    with pytest.raises(ServiceError, match="another function or scope"):
        admission.admit(
            rt.db,
            REGISTRY["billboard_hot100"],
            {**cycle, "scope": "tenant:" + str(uuid4())},
            None,
            key,
            True,
        )
    assert first["work_key"].startswith("manual:[")


async def test_builtins_tenant_scope_and_declared_global_target_reuse(
    rt: Runtime, databases: dict[str, str]
) -> None:
    tenant = uuid4()
    # Scope bindings are authorized through registered jobs; no tenant FK is needed until admission.
    admin(
        databases,
        "INSERT INTO control.tenant(id,name,slug) VALUES (%s,'fixture','fixture')",
        (tenant,),
    )
    scope = "tenant:" + str(tenant)
    admin(
        databases,
        "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('tenant-job','core','hourly',%s)",
        (scope,),
    )

    @bronze(
        source_key="tenant_global",
        writes=["raw.tenant_global"],
        cadence="hourly",
        tenant_bound=True,
        targets=Targets("account", scope="global"),
    )
    async def source(ctx: Ctx, targets: list) -> None:
        pass

    try:
        sync(rt.db)
        dbt_id = uuid4().hex
        await rt.cycles.bind_cycle(
            "hourly", scope, dbt_id, "scheduled", "tenant-job", runner="core"
        )
        export = rt.admit("targets_export", dbt_run_id=dbt_id)
        await rt.execute(export["id"])
        tenant_run = rt.admit("tenant_global", dbt_run_id=dbt_id)
        assert tenant_run["revision_id"] is not None and tenant_run["scope"] == scope
        close = rt.admit("cycle_close", dbt_run_id=dbt_id)
        await rt.execute(close["id"])
        assert rt.receipts(close["id"])["run"]["status"] == "succeeded"
    finally:
        REGISTRY.pop("tenant_global", None)


async def test_handoff_allow_partial_and_zero_target_receipt(
    rt: Runtime, databases: dict[str, str]
) -> None:
    admin(databases, "UPDATE control.target SET deactivated_at=now()")
    _, run = await bound(rt)
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])
    assert len(result["receipts"]) == 1 and result["receipts"][0]["coverage"] == "empty"
    assert result["receipts"][0]["allow_partial"] is False
    admin(
        databases,
        "UPDATE control.streamline SET allow_partial=true WHERE source_key='fixture_accounts'",
    )
    assert rt.receipts(run["id"])["receipts"][0]["allow_partial"] is True


@pytest.mark.parametrize(("timeout_s", "deadline_s", "seconds"), [
    (300, None, 300), (300, 270, 250), (900, 270, 250), (300, 30, 15), (60, 870, 60)])
def test_attempt_ends_inside_the_callers_wait(timeout_s: int, deadline_s: float | None, seconds: float) -> None:
    assert admission.attempt_seconds(timeout_s, deadline_s) == seconds


async def test_admitted_attempt_ends_before_the_invoke_deadline(
    rt: Runtime, databases: dict[str, str]
) -> None:
    """The UDF waits timeout_s - 30 seconds; the attempt ends 20 s inside that, so the UDF reads the
    terminal receipt instead of giving up first."""
    admin(databases, "UPDATE control.streamline SET timeout_s=300 WHERE source_key='billboard_hot100'")
    dbt_id, run = await bound(rt, "billboard_hot100")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(rt.settings, rt, recover=False)),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        admitted = await client.post(
            "/v1/invoke",
            json={"source_key": "billboard_hot100", "cadence": "weekly", "dbt_run_id": dbt_id, "deadline_s": 270},
            headers={"Idempotency-Key": hashlib.sha256(f"billboard_hot100|{dbt_id}|||".encode()).hexdigest()},
        )
        assert admitted.status_code == 202, admitted.text
        await rt.tasks[str(run["id"])]
        receipt = (await client.get("/v1/runs/" + str(run["id"]))).json()["receipts"][0]
    attempt = rt.db.one(
        "SELECT extract(epoch FROM deadline_at - started_at) AS seconds FROM control.run_attempt WHERE run_id=%s",
        (run["id"],),
    )
    assert 249 <= attempt["seconds"] <= 250.5
    assert receipt["blocks_cycle"] is True
