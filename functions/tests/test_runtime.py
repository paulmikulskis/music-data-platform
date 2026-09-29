import asyncio
import hashlib
import json
from contextlib import suppress
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions.admission import work_key
from mdp_functions.api import create_app
from mdp_functions.cycles import FakeAdminApi
from mdp_functions.errors import ServiceError
from mdp_functions.http import FixtureTransport
from mdp_functions.layers import Ctx, bronze
from mdp_functions.recovery import Recovery
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.runs import Runtime
from mdp_functions.settings import PACKAGE
from mdp_functions.targets import export_targets

pytestmark = pytest.mark.docker


@pytest.fixture(autouse=True)
def restore_registry() -> Any:
    before = set(REGISTRY)
    yield
    for key in set(REGISTRY) - before:
        del REGISTRY[key]


def admin_update(databases: dict[str, str], query: str, params: tuple = ()) -> None:
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(query, params)


async def test_record_accounting_mismatch(rt: Runtime) -> None:
    @bronze(
        source_key="test_accounting", writes=["raw.test_accounting"], cadence="daily"
    )
    async def function(ctx: Ctx) -> Any:
        ctx.observed(2)
        yield {"value": 1}

    sync(rt.db)
    _, run = await bound(rt, "test_accounting")
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])
    assert result["run"]["status"] == "partial"
    assert result["run"]["error_class"] == "accounting_mismatch"
    assert result["receipts"][0]["message"] == (
        "observed=2, yielded=1, rejected=0, excluded=0. "
        "Open /runbooks/accounting-mismatch to check the page counts."
    )


async def test_undeclared_write(rt: Runtime) -> None:
    @bronze(source_key="test_routing", writes=["raw.test_routing"], cadence="daily")
    async def function(ctx: Ctx) -> None:
        ctx.observed(1)
        ctx.emit("raw.forbidden", {"value": 1})

    sync(rt.db)
    _, run = await bound(rt, "test_routing")
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    assert result["status"] == "failed" and result["error_class"] == "undeclared_write"


async def test_all_null_unknown_then_additive_drift(rt: Runtime) -> None:
    value = None

    @bronze(source_key="test_unknown", writes=["raw.test_unknown"], cadence="daily")
    async def function(ctx: Ctx) -> Any:
        ctx.observed(1)
        yield {"value": value}

    sync(rt.db)
    _, first = await bound(rt, "test_unknown")
    await rt.execute(first["id"])
    history = list((rt.settings.schema_root / "test_unknown").glob("*.json"))
    assert json.loads(history[0].read_text())["columns"]["value"] == "unknown"
    value = 42
    _, second = await bound(rt, "test_unknown")
    await rt.execute(second["id"])
    assert rt.receipts(second["id"])["run"]["status"] == "succeeded"
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.alert WHERE run_id=%s AND class='schema_drift'",
            (second["id"],),
        )["n"]
        == 1
    )
    assert len(list((rt.settings.schema_root / "test_unknown").glob("*.json"))) == 2


async def test_lost_post_response_idempotency(rt: Runtime) -> None:
    dbt_id, run = await bound(rt, "billboard_hot100")
    key = work_key("billboard_hot100", "global", None, None, run["cycle_id"])
    app = create_app(rt.settings, rt, recover=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + rt.settings.service_token},
    ) as client:
        body = {
            "source_key": "billboard_hot100",
            "cadence": "weekly",
            "dbt_run_id": dbt_id,
        }
        one = await client.post(
            "/v1/invoke",
            json=body,
            headers={
                "Idempotency-Key": hashlib.sha256(
                    f"billboard_hot100|{dbt_id}|||".encode()
                ).hexdigest()
            },
        )
        two = await client.post(
            "/v1/invoke",
            json=body,
            headers={
                "Idempotency-Key": hashlib.sha256(
                    f"billboard_hot100|{dbt_id}|||".encode()
                ).hexdigest()
            },
        )
        assert one.status_code == two.status_code == 202
        assert one.json()["run_id"] == two.json()["run_id"]
        await rt.tasks[str(run["id"])]
        result = (await client.get("/v1/runs/" + str(run["id"]))).json()
        assert result["run"]["status"] == "succeeded"
        assert len(result["receipts"]) == 1
        assert result["receipts"][0]["trace_url"].startswith("local://trace/")
        assert (await client.get("/v1/health")).status_code == 200
        paths = (await client.get("/v1/openapi.json")).json()["paths"]
        for route in (
            "/v1/invoke",
            "/v1/runs",
            "/v1/runs/{run_id}",
            "/v1/runs/{run_id}/cancel",
            "/v1/bind_cycle",
            "/v1/cycles/{cycle_id}/close",
            "/v1/repair",
            "/v1/backfill",
            "/v1/migrate",
            "/v1/functions",
            "/v1/functions/{source_key}",
            "/v1/functions/{source_key}/run",
            "/v1/health",
        ):
            assert route in paths
        assert (await client.post("/v1/backfill")).status_code == 422
    assert (
        rt.db.one("SELECT count(*) AS n FROM control.run WHERE work_key=%s", (key,))[
            "n"
        ]
        == 1
    )


def paged_source(source: str) -> None:
    @bronze(source_key=source, writes=["raw." + source], cadence="daily")
    async def function(ctx: Ctx) -> Any:
        start = ctx.cursor(None) or 0
        for index in range(start, 2):
            await ctx.http.get(f"https://fixture.invalid/page/{index}")
            ctx.observed(1)
            yield {"page": index}
            ctx.set_cursor(None, index + 1)


async def test_restart_draining_resumes_last_part_uploaded(rt: Runtime, monkeypatch) -> None:
    paged_source("test_restart")
    sync(rt.db)
    waiting = asyncio.Event()
    requests = []
    block = True

    async def parked_heartbeat(_batch: dict[str, Any]) -> None:
        await asyncio.Event().wait()

    # This test expires the old worker's lease by hand. Park its heartbeat so a
    # cancelled asyncio.to_thread(beat) cannot renew that lease after the crash.
    # The restarted runtime keeps its real heartbeat.
    monkeypatch.setattr(rt, "heartbeat", parked_heartbeat)

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/1") and block:
            waiting.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={})

    rt.transport = httpx.MockTransport(handler)
    _, run = await bound(rt, "test_restart")
    rt.start(run["id"])
    await asyncio.wait_for(waiting.wait(), 10)
    before = rt.db.one(
        "SELECT last_part_uploaded FROM control.batch WHERE run_id=%s", (run["id"],)
    )["last_part_uploaded"]
    assert before == 1
    task = rt.tasks[str(run["id"])]
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    rt.db.execute(
        "UPDATE control.batch SET lease_expires_at=now()-interval '1 second' WHERE run_id=%s",
        (run["id"],),
    )
    block = False
    restarted = Runtime(rt.settings, transport=httpx.MockTransport(handler))
    try:
        await Recovery(restarted).once()
        await asyncio.gather(*restarted.tasks.values())
        result = restarted.receipts(run["id"])
        assert result["run"]["status"] == "succeeded"
        assert result["run"]["rows_written"] == 2
        assert requests.count("/page/0") == 1 and requests.count("/page/1") == 2
        with psycopg.connect(rt.settings.warehouse_url) as conn:
            rows = conn.execute(
                "SELECT page,count(*) FROM raw.test_restart WHERE _run_id=%s GROUP BY page ORDER BY page",
                (run["id"],),
            ).fetchall()
            assert rows == [(0, 1), (1, 1)]
    finally:
        await restarted.close()


async def test_supersession_drains_inflight_and_admits_new_cycle(rt: Runtime) -> None:
    waiting, release = asyncio.Event(), asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        waiting.set()
        await release.wait()
        return httpx.Response(200, json={"record": {"stats": {"followers": 1}}})

    rt.transport = httpx.MockTransport(handler)
    _, old = await bound(rt)
    rt.start(old["id"])
    await asyncio.wait_for(waiting.wait(), 10)
    binding = await rt.cycles.bind_cycle(
        "hourly",
        "global",
        "local:" + uuid4().hex,
        "scheduled",
        "local:hourly",
        runner="core",
    )
    statuses = rt.db.all(
        "SELECT status FROM control.batch WHERE run_id=%s ORDER BY index", (old["id"],)
    )
    assert sorted(r["status"] for r in statuses) == ["draining", "failed"]
    assert (
        rt.db.one("SELECT status FROM control.cycle WHERE id=%s", (old["cycle_id"],))[
            "status"
        ]
        == "superseded"
    )
    release.set()
    await rt.tasks[str(old["id"])]
    result = rt.receipts(old["id"])
    assert (
        result["run"]["status"] == "superseded" and result["run"]["rows_written"] == 1
    )
    export_targets(rt.db, rt.warehouse, binding["cycle_id"])
    new = rt.admit("fixture_accounts", dbt_run_id=binding["dbt_run_id"])
    await rt.execute(new["id"])
    assert rt.receipts(new["id"])["run"]["status"] == "succeeded"


async def test_attempt_deadline_then_fresh_attempt(
    rt: Runtime, databases: dict[str, str]
) -> None:
    paged_source("test_deadline")
    # Exercise the attempt deadline, independently of the production 1 rps default.
    admin_update(
        databases,
        "INSERT INTO control.host_health(host,host_rps) VALUES ('fixture.invalid',100) "
        "ON CONFLICT(host) DO UPDATE SET host_rps=100",
    )
    sync(rt.db)
    admin_update(
        databases,
        "UPDATE control.streamline SET timeout_s=1 WHERE source_key='test_deadline'",
    )
    block = True

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/1") and block:
            await asyncio.sleep(10)
        return httpx.Response(200, json={})

    rt.transport = httpx.MockTransport(handler)
    _, run = await bound(rt, "test_deadline")
    await rt.execute(run["id"])
    first = rt.db.one("SELECT * FROM control.run_attempt WHERE run_id=%s", (run["id"],))
    assert first["status"] == "failed"
    assert rt.receipts(run["id"])["run"]["error_class"] == "invoke_timeout"
    block = False
    # Resume with headroom for durable publication under parallel test load.
    admin_update(databases, "UPDATE control.streamline SET timeout_s=30 WHERE source_key='test_deadline'")
    await rt.execute(run["id"])
    attempts = rt.db.all(
        "SELECT * FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no",
        (run["id"],),
    )
    assert len(attempts) == 2 and attempts[1]["deadline_at"] > first["deadline_at"]
    assert rt.receipts(run["id"])["run"]["status"] == "succeeded", rt.receipts(run["id"])
    assert rt.receipts(run["id"])["run"]["rows_written"] == 2
    admin_update(databases, "DELETE FROM control.host_health WHERE host='fixture.invalid'")


async def test_targets_export_write_once_activation_between_export_and_invoke(
    rt: Runtime, databases: dict[str, str]
) -> None:
    binding = await rt.cycles.bind_cycle(
        "hourly",
        "global",
        "local:" + uuid4().hex,
        "scheduled",
        "local:hourly",
        runner="core",
    )
    first = export_targets(rt.db, rt.warehouse, binding["cycle_id"])
    admin_update(
        databases,
        "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) SELECT id,'fixture','later','later','resolved',now() FROM control.target_set WHERE kind='account'",
    )
    second = export_targets(rt.db, rt.warehouse, binding["cycle_id"])
    assert first["id"] == second["id"] and second["member_count"] == 2
    run = rt.admit("fixture_accounts", dbt_run_id=binding["dbt_run_id"])
    assert (
        sum(
            len(r["target_ids"])
            for r in rt.db.all(
                "SELECT target_ids FROM control.batch WHERE run_id=%s", (run["id"],)
            )
        )
        == 2
    )


async def test_cycle_close_excludes_late_commit_despite_earlier_sequence(
    rt: Runtime,
) -> None:
    from test_landing import pending

    late = await pending(rt)
    rt.db.execute(
        "UPDATE control.batch SET status='succeeded' WHERE run_id=%s", (late["run_id"],)
    )
    _, later = await bound(rt, "billboard_hot100")
    await rt.execute(later["id"])
    rt.cycles.close(later["cycle_id"])
    inputs = rt.db.all(
        "SELECT dump_id FROM control.cycle_manifest(%s) ORDER BY dump_id",
        (later["cycle_id"],),
    )
    assert inputs and late["dump_id"] not in {r["dump_id"] for r in inputs}
    first_seq = rt.db.one(
        "SELECT landed_seq FROM control.dump WHERE id=%s", (late["dump_id"],)
    )["landed_seq"]
    assert (
        first_seq
        < rt.db.one(
            "SELECT min(landed_seq) AS n FROM control.dump WHERE run_id=%s",
            (later["id"],),
        )["n"]
    )
    rt.landing(late["warehouse_id"]).process(late["id"])
    rt.cycles.close(later["cycle_id"])
    assert (
        rt.db.all(
            "SELECT dump_id FROM control.cycle_manifest(%s) ORDER BY dump_id",
            (later["cycle_id"],),
        )
        == inputs
    )


async def test_html_json_response_rejected_accounting_balances(
    rt: Runtime, databases: dict[str, str]
) -> None:
    admin_update(
        databases,
        "UPDATE control.target SET deactivated_at=now() WHERE handle='acceptance_account_002'",
    )
    rt.transport = FixtureTransport([PACKAGE / "sources/fixture_accounts/fixtures/html.jsonl"])
    _, run = await bound(rt)
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    assert result["status"] == "failed" and result["rows_rejected"] == 1
    assert result["rows_written"] == 0 and result["error_class"] == "partial_coverage"
    assert rt.receipts(run["id"])["receipts"][0]["target_coverage"] == 0
    assert result["coverage"] == "partial"
    assert rt.db.one("SELECT count(*) AS n FROM control.alert WHERE run_id=%s AND class='partial_coverage' AND resolved_at IS NULL", (run["id"],))["n"] == 1
    with psycopg.connect(rt.settings.service_read_url) as conn:
        reason = conn.execute(
            "SELECT reason FROM raw._rejected WHERE _run_id=%s", (run["id"],)
        ).fetchone()[0]
        assert "Expected JSON" in reason


async def test_html_fixture_26_rejected_partial_coverage(rt: Runtime, databases: dict[str, str], monkeypatch) -> None:
    with psycopg.connect(databases["admin_control"]) as conn:
        target_set = conn.execute("SELECT id FROM control.target_set WHERE kind='account'").fetchone()[0]
        for n in range(3, 27):
            conn.execute("INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) VALUES (%s,'fixture',%s,%s,'resolved',now())", (target_set, f"acceptance-{n:03}", f"acceptance_account_{n:03}"))
    monkeypatch.setenv("MDP_FIXTURE_MODE", "1")
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>Unavailable</html>", headers={"content-type": "text/html"}, request=request))
    binding, _ = await bound(rt)
    app = create_app(rt.settings, runtime=rt, recover=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.post(f"/v1/functions/fixture_accounts/run?dbt_run_id={binding}", headers={"authorization": "Bearer " + rt.settings.service_token})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        await rt.tasks[run_id]
    result = rt.receipts(run_id)["run"]
    assert result["rows_written"] == 0
    assert result["rows_rejected"] == 26
    assert result["coverage"] == "partial"
    assert result["status"] == "failed"
    assert rt.db.one("SELECT count(*) AS n FROM control.alert WHERE run_id=%s AND class='partial_coverage' AND resolved_at IS NULL", (run_id,))["n"] == 1


async def test_one_rejected_batch_keeps_mixed_delivery_partial_coverage(rt: Runtime) -> None:
    fixture = json.loads((PACKAGE / "sources/fixture_accounts/fixtures/html.jsonl").read_text().splitlines()[0])["response"]
    async def respond(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("username") == "acceptance_account_001":
            return httpx.Response(fixture["status"], headers=fixture.get("headers", {}), content=fixture["body"], request=request)
        return httpx.Response(200, json={"record": {"identity": {"id": "acceptance-002"}, "stats": {"followers": 1, "following": 1, "likes": 1, "posts": 1}}}, request=request)
    rt.transport = httpx.MockTransport(respond)
    _, run = await bound(rt)
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    assert result["rows_written"] == 1 and result["rows_rejected"] == 1
    assert result["coverage"] == "partial"


@pytest.mark.parametrize(
    "source,table,expected",
    [("fixture_accounts", "account_snapshots", 2), ("billboard_hot100", "chart_entries", 3)],
)
async def test_fixture_functions_full_lineage(
    rt: Runtime, source: str, table: str, expected: int
) -> None:
    _, run = await bound(rt, source)
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    assert result["status"] == "succeeded" and result["coverage"] == "full"
    assert result["rows_written"] == expected
    with psycopg.connect(rt.settings.service_read_url) as conn:
        from psycopg import sql

        rows = conn.execute(
            sql.SQL("SELECT * FROM raw.{} WHERE _run_id=%s").format(
                sql.Identifier(table)
            ),
            (run["id"],),
        )
        columns = {d.name for d in rows.description}
        from mdp_functions.schemas import LINEAGE

        assert set(LINEAGE) <= columns
        assert len(rows.fetchall()) == expected
        nulls = conn.execute(
            sql.SQL(
                "SELECT count(*) FROM raw.{} WHERE _run_id=%s AND (_dump_id IS NULL OR _landed_seq IS NULL OR _cycle_id IS NULL OR _request_id IS NULL OR _source_key IS NULL OR _ingested_at IS NULL)"
            ).format(sql.Identifier(table)),
            (run["id"],),
        ).fetchone()[0]
        assert nulls == 0


async def test_bind_cycle_scope_mismatch(rt: Runtime) -> None:
    with pytest.raises(ServiceError, match="job_id, cadence and scope"):
        await rt.cycles.bind_cycle(
            "daily", "global", "local:bad", "scheduled", "local:hourly", runner="core"
        )


async def test_bind_cycle_replay_refused_open(rt: Runtime) -> None:
    binding = await rt.cycles.bind_cycle(
        "daily", "global", "local:first", "scheduled", "local:daily", runner="core"
    )
    with pytest.raises(ServiceError, match="Replay requires a closed cycle"):
        await rt.cycles.bind_cycle(
            "daily",
            "global",
            "local:replay",
            "other",
            "local:daily",
            str(binding["cycle_id"]),
            runner="core",
        )


async def test_bind_cycle_noop_on_rebind(rt: Runtime) -> None:
    one = await rt.cycles.bind_cycle(
        "daily", "global", "local:once", "scheduled", "local:daily", runner="core"
    )
    two = await rt.cycles.bind_cycle(
        "daily", "global", "local:once", "scheduled", "local:daily", runner="core"
    )
    assert one == two
    assert rt.db.one("SELECT count(*) AS n FROM control.cycle")["n"] == 1


async def test_bind_cycle_fake_admin_identity_mismatch(
    rt: Runtime, databases: dict[str, str]
) -> None:
    admin_update(
        databases,
        "UPDATE control.dbt_job SET runner='cloud' WHERE job_id='local:daily'",
    )
    rt.cycles.admin = FakeAdminApi(
        {
            "local:bad": {
                "id": "local:bad",
                "job_definition_id": "wrong",
                "in_progress": True,
            }
        }
    )
    with pytest.raises(ServiceError, match="Admin API run identity"):
        await rt.cycles.bind_cycle(
            "daily", "global", "local:bad", "scheduled", "local:daily", runner="cloud"
        )


@pytest.mark.parametrize(
    "case,expected_status,expected_calls",
    [
        ("retry", "succeeded", 2),
        ("not_found", "failed", 1),
        ("all_null", "succeeded", 1),
    ],
)
async def test_vendor_fixtures(
    rt: Runtime,
    databases: dict[str, str],
    case: str,
    expected_status: str,
    expected_calls: int,
) -> None:
    admin_update(
        databases,
        "UPDATE control.target SET deactivated_at=now() WHERE handle='acceptance_account_002'",
    )
    rt.transport = FixtureTransport([PACKAGE / f"sources/fixture_accounts/fixtures/{case}.jsonl"])
    _, run = await bound(rt)
    await rt.execute(run["id"])
    assert rt.receipts(run["id"])["run"]["status"] == expected_status
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.call_ledger WHERE run_id=%s",
            (run["id"],),
        )["n"]
        == expected_calls
    )


async def test_lease_fences_cursor_and_dump_registration(
    rt: Runtime, databases: dict[str, str]
) -> None:
    from mdp_functions.admission import acquire, attempt
    from mdp_functions.errors import LeaseLost

    _, run = await bound(rt, "billboard_hot100")
    active = attempt(rt.db, run["id"])
    batch = acquire(rt.db, run, active, 30)
    ctx = Ctx(REGISTRY["billboard_hot100"], run)
    ctx.set_cursor(None, {"page": 1})
    rt.db.execute(
        "UPDATE control.batch SET lease_token=%s WHERE id=%s", (uuid4(), batch["id"])
    )
    with pytest.raises(LeaseLost):
        rt.dumps.publish(ctx, run, batch, [])
    assert rt.db.one("SELECT count(*) AS n FROM control.cursor")["n"] == 0
    assert rt.db.one("SELECT count(*) AS n FROM control.dump")["n"] == 0


async def test_registry_sync_preserves_knobs_and_control_owned_columns(
    rt: Runtime, databases: dict[str, str]
) -> None:
    admin_update(
        databases,
        "UPDATE control.streamline SET enabled=false,max_concurrency=7,storage='iceberg' WHERE source_key='fixture_accounts'",
    )
    sync(rt.db)
    row = rt.db.one(
        "SELECT enabled,max_concurrency,storage FROM control.streamline WHERE source_key='fixture_accounts'"
    )
    assert row == {"enabled": False, "max_concurrency": 7, "storage": "iceberg"}
    for table, column in [
        ("cursor", "reset_generation"),
        ("alert", "acknowledged_by"),
        ("alert", "resolved_at"),
    ]:
        assert not rt.db.one(
            "SELECT has_column_privilege(current_user,%s,%s,'UPDATE') AS allowed",
            ("control." + table, column),
        )["allowed"]


async def test_completed_receipts_remain_stable_after_cycle_supersession(
    rt: Runtime,
) -> None:
    dbt_id, run = await bound(rt, "billboard_hot100")
    await rt.execute(run["id"])
    before = rt.receipts(run["id"])["receipts"]
    assert before[0]["status"] == "succeeded"
    await rt.cycles.bind_cycle(
        "weekly",
        "global",
        "local:" + uuid4().hex,
        "scheduled",
        "local:weekly",
        runner="core",
    )
    duplicate = rt.admit("billboard_hot100", dbt_run_id=dbt_id)
    await rt.execute(duplicate["id"])
    assert duplicate["id"] == run["id"]
    assert rt.receipts(run["id"])["receipts"] == before
