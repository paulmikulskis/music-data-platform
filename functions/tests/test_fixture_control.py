"""Fixture routes never exist outside explicit fixture mode and retain auth."""

import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from mdp_functions.api import create_app
from mdp_functions.fixture_control import PLANS, ControlledTransport, FixturePlan
from mdp_functions.settings import Settings


@pytest.mark.parametrize("enabled", [False, True])
async def test_fixture_endpoint_guard_and_auth(monkeypatch, enabled):
    monkeypatch.setenv("MDP_FIXTURE_MODE", "1" if enabled else "0")
    runtime = SimpleNamespace(transport=None, db=None)
    settings = Settings(service_token=uuid4().hex)
    app = create_app(settings, runtime=runtime, recover=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        body = {"source_key": "lifecycle_daily_probe", "pages": 2}
        assert (await client.post("/v1/_fixture/plan", json=body)).status_code == 401
        response = await client.post(
            "/v1/_fixture/plan",
            json=body,
            headers={"Authorization": "Bearer " + settings.service_token},
        )
        assert response.status_code == (200 if enabled else 404)
        # The barrier release exists only in fixture mode too: the production app answers 404.
        released = await client.post(
            "/v1/_fixture/release",
            json={"source_key": "lifecycle_daily_probe"},
            headers={"Authorization": "Bearer " + settings.service_token},
        )
        assert released.status_code == (200 if enabled else 404)
        assert (await client.post("/v1/_fixture/release", json={"source_key": "fixture_accounts"})).status_code == 401
        if enabled:
            assert isinstance(runtime.transport, ControlledTransport)
            invalid = await client.post(
                "/v1/_fixture/plan",
                json=body | {"pages": 0},
                headers={"Authorization": "Bearer " + settings.service_token},
            )
            assert invalid.status_code == 422
    PLANS.clear()


async def test_plan_failure_boundary_and_reset():
    transport = ControlledTransport(None)
    PLANS["lifecycle_daily_probe"] = FixturePlan(
        source_key="lifecycle_daily_probe", pages=5, fail_at=3
    )
    async with httpx.AsyncClient(transport=transport) as client:
        for page in (1, 2):
            response = await client.get(
                "https://fixture.invalid/lifecycle_daily_probe", params={"page": page}
            )
            assert response.status_code == 200
        # A forced failure is an outage the client retries, never a terminal 4xx.
        for page in (3, 4):
            with pytest.raises(httpx.ConnectError):
                await client.get(
                    "https://fixture.invalid/lifecycle_daily_probe", params={"page": page}
                )
        assert (
            await client.get(
                "https://fixture.invalid/lifecycle_daily_probe", params={"page": 6}
            )
        ).status_code == 400
        PLANS["lifecycle_daily_probe"] = FixturePlan(
            source_key="lifecycle_daily_probe", pages=5
        )
        assert (
            await client.get(
                "https://fixture.invalid/lifecycle_daily_probe", params={"page": 3}
            )
        ).status_code == 200
    PLANS.clear()


async def test_a_holding_plan_keeps_its_page_in_flight_until_the_harness_releases_it(monkeypatch):
    import asyncio

    from mdp_functions import fixture_control

    monkeypatch.setenv("MDP_FIXTURE_MODE", "1")
    runtime = SimpleNamespace(transport=None, db=None)
    settings = Settings(service_token=uuid4().hex)
    app = create_app(settings, runtime=runtime, recover=False)
    auth = {"Authorization": "Bearer " + settings.service_token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as control:
        body = {"source_key": "lifecycle_daily_probe", "pages": 2, "hold": True}
        assert (await control.post("/v1/_fixture/plan", json=body, headers=auth)).status_code == 200
        async with httpx.AsyncClient(transport=runtime.transport) as vendor:
            page = asyncio.create_task(vendor.get("https://fixture.invalid/lifecycle_daily_probe", params={"page": 1}))
            await asyncio.sleep(0.2)
            # Started and held: a re-plan without hold (the next cycle's pages) leaves it held.
            assert not page.done() and runtime.transport.started["lifecycle_daily_probe"] == 1
            await control.post("/v1/_fixture/plan", json=body | {"hold": False}, headers=auth)
            assert (await vendor.get("https://fixture.invalid/lifecycle_daily_probe", params={"page": 2})).status_code == 200
            assert not page.done()
            released = await control.post("/v1/_fixture/release", json={"source_key": "lifecycle_daily_probe"}, headers=auth)
            assert released.json() == {"source_key": "lifecycle_daily_probe", "released": True}
            assert (await asyncio.wait_for(page, timeout=5)).status_code == 200
        again = await control.post("/v1/_fixture/release", json={"source_key": "lifecycle_daily_probe"}, headers=auth)
        assert again.json()["released"] is False
    assert not fixture_control.BARRIERS
    PLANS.clear()


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_probe_registration_is_opt_in(enabled):
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from mdp_functions.registry import discover; "
                'print(json.dumps(sorted(k for k in discover() if k.startswith("lifecycle_"))))'
            ),
        ],
        env=dict(os.environ, MDP_FIXTURE_MODE=enabled),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == (
        ["lifecycle_daily_probe", "lifecycle_tenant_probe"] if enabled == "1" else []
    )
