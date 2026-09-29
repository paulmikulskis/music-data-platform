"""Mixed-version deploy windows: a pre-stamp dbt hook never binds or closes against the stamp service,
and a cycle the previous image closed without a stamp keeps its list on its next close."""

from uuid import uuid4

import httpx
import psycopg
import pytest
from mdp_functions.api import create_app
from mdp_functions.errors import ServiceError
from mdp_functions.runs import Runtime


async def post_bind(rt: Runtime, body: dict) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(rt.settings, rt, recover=False), raise_app_exceptions=False),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        return await client.post("/v1/bind_cycle", json=body)


def old_bind(dbt_id: str) -> dict:
    """The 81368e9 mdp.bind_cycle UDF payload: seven fields, no global_inputs, no lock_runner."""
    return {
        "cadence": "hourly", "scope": "global", "dbt_run_id": dbt_id, "reason_category": "scheduled",
        "job_id": "local:hourly", "cycle_id": None, "runner": "core",
    }


async def test_a_pre_stamp_hook_bind_is_refused_and_binds_nothing(rt: Runtime) -> None:
    dbt_id = "core:" + uuid4().hex
    response = await post_bind(rt, old_bind(dbt_id))
    assert response.status_code == 409
    assert response.json()["error_class"] == "runner_outdated"
    assert not rt.db.one("SELECT 1 AS x FROM control.cycle_attempt WHERE dbt_run_id=%s", (dbt_id,))
    assert not rt.db.one("SELECT 1 AS x FROM control.cycle WHERE opened_by_dbt_run_id=%s", (dbt_id,))
    # The current hook sends its generated global inputs and binds, marked as the stamp protocol.
    current = await post_bind(rt, {**old_bind(dbt_id), "global_inputs": []})
    assert current.status_code == 200, current.text
    attempt = rt.db.one("SELECT stamp_protocol FROM control.cycle_attempt WHERE dbt_run_id=%s", (dbt_id,))
    assert attempt["stamp_protocol"] is True
    # Run Now binds without global inputs, under the runner lock.
    run_now = await post_bind(
        rt, {**old_bind("manual:" + uuid4().hex), "lock_runner": True}
    )
    assert run_now.status_code == 200, run_now.text


async def test_a_run_bound_before_the_deploy_never_closes_its_cycle(rt: Runtime) -> None:
    dbt_id = "core:" + uuid4().hex
    binding = await rt.cycles.bind_cycle("hourly", "global", dbt_id, "scheduled", "local:hourly", runner="core")
    # 0011 leaves attempts bound by the previous image unmarked.
    rt.db.execute("UPDATE control.cycle_attempt SET stamp_protocol=false WHERE dbt_run_id=%s", (dbt_id,))
    with pytest.raises(ServiceError) as caught:
        rt.admit("cycle_close", dbt_run_id=dbt_id)
    assert caught.value.error_class == "runner_outdated" and caught.value.status_code == 409
    cycle = rt.db.one("SELECT status,close_no FROM control.cycle WHERE id=%s", (binding["cycle_id"],))
    assert cycle == {"status": "open", "close_no": None}
    # A Retry on the current image attaches to the same cycle and closes it with a stamp.
    retry = "core:" + uuid4().hex
    attached = await rt.cycles.bind_cycle("hourly", "global", retry, "other", "local:hourly", runner="core")
    assert attached["cycle_id"] == binding["cycle_id"]
    closed = rt.cycles.close(attached["cycle_id"])
    assert closed["status"] == "closed" and closed["close_no"] is not None


async def test_close_keeps_the_list_of_a_cycle_the_previous_image_closed(rt: Runtime, databases) -> None:
    dbt_id = "core:" + uuid4().hex
    binding = await rt.cycles.bind_cycle("daily", "global", dbt_id, "scheduled", "local:daily", runner="core")
    # The 81368e9 close of a cycle this image bound (stamp): status closed, no close_no.
    rt.db.execute(
        "UPDATE control.cycle SET status='closed',closed_at=now()-interval '1 hour' WHERE id=%s",
        (binding["cycle_id"],),
    )
    before = rt.db.one("SELECT closed_at FROM control.cycle WHERE id=%s", (binding["cycle_id"],))
    last = rt.db.one("SELECT last_close_no FROM control.scope_close WHERE scope='global'")
    closed = rt.cycles.close(binding["cycle_id"])
    # It holds the old close's full list: list/0, its closed_at kept, and no close number taken.
    assert (closed["manifest_mode"], closed["close_no"]) == ("list", 0)
    assert closed["closed_at"] == before["closed_at"]
    assert rt.db.one("SELECT last_close_no FROM control.scope_close WHERE scope='global'") == (last or {"last_close_no": 0})
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        mirrored = conn.execute(
            "SELECT status,manifest_mode,close_no FROM raw.cycles WHERE id=%s", (binding["cycle_id"],)
        ).fetchall()
    assert mirrored == [("closed", "list", 0)]
