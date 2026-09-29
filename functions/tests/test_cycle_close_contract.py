"""Close errors retain their subsystem through HTTP and built-in execution."""

from uuid import uuid4

import duckdb
import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.api import create_app
from mdp_functions.errors import ServiceError
from mdp_functions.runs import Runtime
from mdp_functions.warehouse.postgres import PostgresWarehouse
from psycopg_pool import PoolTimeout


async def post_close(rt: Runtime, cycle_id: object) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            create_app(rt.settings, rt, recover=False), raise_app_exceptions=False
        ),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        return await client.post(f"/v1/cycles/{cycle_id}/close")


@pytest.mark.parametrize("path", ["http", "builtin"])
@pytest.mark.parametrize("missing", [True, False], ids=["missing", "superseded"])
async def test_close_identity_errors(rt: Runtime, path: str, missing: bool) -> None:
    _, run = await bound(rt, "billboard_hot100")
    if missing:
        run = {**run, "cycle_id": uuid4()}
        expected = {"error_class": "cycle_not_found", "message": "Unknown cycle"}
        status = 404
    else:
        rt.db.execute(
            "UPDATE control.cycle SET status='superseded' WHERE id=%s",
            (run["cycle_id"],),
        )
        expected = {
            "error_class": "scope_mismatch",
            "message": "Cannot close a superseded cycle",
        }
        status = 409
    if path == "http":
        response = await post_close(rt, run["cycle_id"])
        assert response.status_code == status
        assert response.json() == {**expected, "next_step": ServiceError(expected["error_class"], expected["message"]).next_step, "runbook": ServiceError(expected["error_class"], expected["message"]).runbook}
    else:
        with pytest.raises(ServiceError) as caught:
            rt.close_cycle(run)
        assert caught.value.status_code == status
        assert {
            "error_class": caught.value.error_class,
            "message": caught.value.message,
        } == expected


@pytest.mark.parametrize("path", ["http", "builtin"])
@pytest.mark.parametrize(
    "error_type", [psycopg.OperationalError, PoolTimeout, OSError, duckdb.IOException]
)
async def test_close_warehouse_errors(
    rt: Runtime,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    error_type: type[Exception],
) -> None:
    dbt_id, source_run = await bound(rt, "billboard_hot100")
    run = rt.admit("cycle_close", dbt_run_id=dbt_id)

    def unavailable(*args: object, **kwargs: object) -> None:
        raise error_type("backend failure")

    # Close reads only control; receipts are never consulted, so their outage cannot block it.
    monkeypatch.setattr(PostgresWarehouse, "receipts", unavailable)
    original_mirror = PostgresWarehouse.mirror
    monkeypatch.setattr(PostgresWarehouse, "mirror", unavailable)
    message = "Warehouse mirror is unavailable"
    if path == "http":
        response = await post_close(rt, source_run["cycle_id"])
        assert response.status_code == 503
        assert response.json() == {
            "error_class": "warehouse_unavailable",
            "message": message,
            "next_step": ServiceError("warehouse_unavailable", message).next_step,
            "runbook": "/runbooks/warehouse-unavailable",
        }
    else:
        with pytest.raises(ServiceError) as caught:
            rt.close_cycle(run)
        assert caught.value.status_code == 503
        assert caught.value.error_class == "warehouse_unavailable"
        assert caught.value.message == message
        # Settlement reads the run's own receipts, and their outage stays a typed warehouse error.
        with pytest.raises(ServiceError) as settling:
            await rt.execute(run["id"])
        assert settling.value.status_code == 503
        assert settling.value.message == "Committed receipts are unavailable"
        saved = rt.db.one(
            "SELECT error_class,error_message FROM control.run WHERE id=%s",
            (run["id"],),
        )
        assert saved == {
            "error_class": "warehouse_unavailable",
            "error_message": message,
        }
    # The close committed in control; it is terminal only once the warehouse holds the cycle.
    cycle = rt.db.one(
        "SELECT status,close_no FROM control.cycle WHERE id=%s", (run["cycle_id"],)
    )
    assert cycle["status"] == "closed"
    monkeypatch.undo()
    monkeypatch.setattr(PostgresWarehouse, "mirror", original_mirror)
    if path == "builtin":
        rt.settle(run["id"])
        result = rt.receipts(run["id"])
        assert result["run"]["status"] == "partial"
        assert result["run"]["error_class"] == "warehouse_unavailable"
        assert (
            rt.db.one(
                "SELECT runbook_slug FROM control.alert WHERE run_id=%s",
                (run["id"],),
            )["runbook_slug"]
            == "warehouse-unavailable"
        )
        retry = rt.admit("cycle_close", dbt_run_id=dbt_id)
        assert retry["id"] == run["id"]
        await rt.execute(retry["id"])
        assert rt.receipts(retry["id"])["run"]["status"] == "succeeded"
    assert rt.cycles.close(run["cycle_id"])["close_no"] == cycle["close_no"]
    assert (
        rt.db.one("SELECT mirrored_close_no FROM control.scope_close WHERE scope='global'")[
            "mirrored_close_no"
        ]
        == cycle["close_no"]
    )


async def test_close_control_failure_keeps_attribution(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> None:
        raise psycopg.OperationalError("control failure")

    monkeypatch.setattr(rt.db, "transaction", unavailable)
    response = await post_close(rt, uuid4())
    assert response.status_code == 503
    assert response.json()["error_class"] == "control_db_unavailable"


async def test_close_success_is_idempotent(rt: Runtime) -> None:
    _, run = await bound(rt, "billboard_hot100")
    first = await post_close(rt, run["cycle_id"])
    second = await post_close(rt, run["cycle_id"])
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "closed"


@pytest.mark.parametrize(
    "error_class",
    [
        "cycle_not_found",
        "scope_mismatch",
        "warehouse_unavailable",
        "control_db_unavailable",
    ],
)
async def test_close_error_runbooks_support_alerts(
    rt: Runtime, error_class: str
) -> None:
    from mdp_functions.control_db import alert
    from mdp_functions.settings import REPO

    _, run = await bound(rt, "billboard_hot100")
    with rt.db.transaction() as conn:
        alert(conn, run["id"], error_class, str(run["id"]))
    row = rt.db.one(
        """SELECT a.class, a.runbook_slug, b.body_md FROM control.alert a
        JOIN control.runbook b ON b.slug=a.runbook_slug WHERE a.run_id=%s""",
        (run["id"],),
    )
    assert row["class"] == error_class
    assert row["runbook_slug"] == error_class.replace("_", "-")
    document = f"ops/runbooks/{error_class}.md"
    assert document in row["body_md"]
    assert (REPO / document).is_file()
