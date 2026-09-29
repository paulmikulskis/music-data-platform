"""Tests for cycle binding."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import httpx
import pytest
from conftest import bound
from mdp_functions.api import create_app
from mdp_functions.errors import ServiceError
from mdp_functions.runs import Runtime
from runtime_fixture import admin


async def test_mirror_snapshot_lock_prevents_stale_cycle_status(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mdp_functions.cycles import mirror_bindings

    _, run = await bound(rt, "billboard_hot100")
    entered, release = threading.Event(), threading.Event()
    mirror = rt.warehouse.mirror
    first = True

    def delayed(tables: dict) -> None:
        nonlocal first
        if first:
            first = False
            entered.set()
            assert release.wait(5)
        mirror(tables)

    # Write-through mirrors re-read their rows under one lock, so the newest state wins.
    def write_through() -> None:
        mirror_bindings(rt.db, rt.warehouse, [run["cycle_id"]])

    monkeypatch.setattr(rt.warehouse, "mirror", delayed)
    with ThreadPoolExecutor(2) as pool:
        older = pool.submit(write_through)
        assert entered.wait(5)
        rt.db.execute(
            "UPDATE control.cycle SET status='closed',closed_at=now() WHERE id=%s",
            (run["cycle_id"],),
        )
        newer = pool.submit(write_through)
        await asyncio.sleep(0.05)
        assert not newer.done()
        release.set()
        older.result(5)
        newer.result(5)
    with rt.warehouse.connect() as conn:
        assert (
            conn.execute(
                "SELECT status FROM raw.cycles WHERE id=%s", (run["cycle_id"],)
            ).fetchone()["status"]
            == "closed"
        )


async def test_handoff_runner_binding_provenance_switch_spoof_and_concurrency(
    rt: Runtime, databases: dict[str, str]
) -> None:
    class NoCloud:
        async def get_run(self, *args: Any) -> dict:
            raise AssertionError("Core must not call Cloud Admin API")

    rt.cycles.admin = NoCloud()
    dbt_id = uuid4().hex
    args = ("daily", "global", dbt_id, "scheduled", "local:daily")
    first, second = await asyncio.gather(
        rt.cycles.bind_cycle(*args, runner="core"),
        rt.cycles.bind_cycle(*args, runner="core"),
    )
    assert (
        first == second
        and first["runner"] == "core"
        and first["job_id"] == "local:daily"
    )
    admin(databases, "UPDATE control.runner_mode SET runner='cloud'")
    assert await rt.cycles.bind_cycle(*args, runner="core") == first
    with pytest.raises(ServiceError, match="active configured runner"):
        await rt.cycles.bind_cycle(
            "daily", "global", uuid4().hex, "other", "local:daily", runner="core"
        )
    with pytest.raises(ServiceError, match="runner"):
        await rt.cycles.bind_cycle(*args, runner="cloud")
    admin(
        databases,
        "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('cloud-job','cloud','daily','global')",
    )
    with pytest.raises(ServiceError, match="different runner"):
        await rt.cycles.bind_cycle(
            "daily", "global", dbt_id, "scheduled", "cloud-job", runner="cloud"
        )
    admin(databases, "DELETE FROM control.runner_mode")
    with pytest.raises(ServiceError, match="active configured runner"):
        await rt.cycles.bind_cycle(
            "daily", "global", uuid4().hex, "scheduled", "local:daily", runner="core"
        )


async def test_handoff_cloud_verifies_identity_and_replay_stays_closed_only(
    rt: Runtime, databases: dict[str, str]
) -> None:
    from mdp_functions.cycles import FakeAdminApi

    admin(databases, "UPDATE control.runner_mode SET runner='cloud'")
    admin(
        databases,
        "INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('cloud-job','cloud','daily','global')",
    )
    rt.cycles.admin = FakeAdminApi(
        {
            "cloud-run": {
                "id": "cloud-run",
                "job_definition_id": "cloud-job",
                "in_progress": True,
                "git_sha": "fixture-revision",
            }
        }
    )
    first = await rt.cycles.bind_cycle(
        "daily", "global", "cloud-run", "scheduled", "cloud-job"
    )
    assert first["runner"] == "cloud" and first["git_sha"] == "fixture-revision"
    with pytest.raises(ServiceError, match="Admin API run identity"):
        await rt.cycles.bind_cycle("daily", "global", "bad", "scheduled", "cloud-job")
    rt.cycles.admin = FakeAdminApi()
    with pytest.raises(ServiceError, match="closed cycle"):
        await rt.cycles.bind_cycle(
            "daily", "global", "replay", "other", "cloud-job", str(first["cycle_id"])
        )
    rt.cycles.close(first["cycle_id"])
    replay = await rt.cycles.bind_cycle(
        "daily", "global", "replay", "other", "cloud-job", str(first["cycle_id"])
    )
    assert replay["cycle_id"] == first["cycle_id"]


async def test_transport_hash_resend_and_new_binding_reuse_completed_canonical_work(
    rt: Runtime,
) -> None:
    import hashlib

    first_id, run = await bound(rt, "billboard_hot100")
    app = create_app(rt.settings, rt, recover=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        for index in range(3):
            dbt_id = first_id if index < 2 else uuid4().hex
            if index == 2:
                await rt.cycles.bind_cycle(
                    "weekly", "global", dbt_id, "other", "local:weekly", runner="core"
                )
            body = {
                "source_key": "billboard_hot100",
                "dbt_run_id": dbt_id,
                "model": "model.fixture.source",
                "cadence": "weekly",
            }
            key = hashlib.sha256(
                f"billboard_hot100|{dbt_id}|model.fixture.source||".encode()
            ).hexdigest()
            response = await client.post(
                "/v1/invoke", json=body, headers={"Idempotency-Key": key}
            )
            assert response.status_code == 202 and response.json()["run_id"] == str(
                run["id"]
            )
            await rt.tasks[str(run["id"])]
            assert (
                rt.db.one(
                    "SELECT count(*) AS n FROM control.call_ledger WHERE run_id=%s",
                    (run["id"],),
                )["n"]
                == 1
            )
            poll = (await client.get("/v1/runs/" + str(run["id"]))).json()
            assert (
                poll["run"]["status"] == "succeeded"
                and poll["receipts"][0]["rows_written"] == "3"
            )
        bad = await client.post(
            "/v1/invoke", json=body, headers={"Idempotency-Key": "0" * 64}
        )
        assert bad.status_code == 400
