"""Deadlines survive process loss, fresh heartbeats and blocked warehouse writes."""

import asyncio
import threading
import time
from contextlib import suppress
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from mdp_functions import admission
from mdp_functions.errors import LeaseLost
from mdp_functions.layers import Ctx, bronze
from mdp_functions.recovery import Recovery
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.runs import Runtime

pytestmark = pytest.mark.docker


@pytest.fixture(autouse=True)
def restore_registry(rt):
    before = set(REGISTRY)
    yield
    for key in set(REGISTRY) - before:
        del REGISTRY[key]


def source(key):
    @bronze(source_key=key, writes=["raw." + key], cadence="daily")
    async def function(ctx: Ctx):
        for page in range(ctx.cursor(None) or 0, 2):
            await ctx.http.get(f"https://fixture.invalid/page/{page}")
            ctx.observed(1)
            yield {"page": page}
            ctx.set_cursor(None, page + 1)


def configure(databases, key, seconds):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.streamline SET timeout_s=%s WHERE source_key=%s",
            (seconds, key),
        )


async def test_restart_expires_attempt_despite_live_lease_and_retry_resumes(
    rt, databases
):
    source("test_deadline_restart")
    sync(rt.db)
    configure(databases, "test_deadline_restart", 2)
    waiting = asyncio.Event()
    calls = []
    block = True

    async def handler(request):
        calls.append(request.url.path)
        if block and request.url.path.endswith("/1"):
            waiting.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={})

    rt.transport = httpx.MockTransport(handler)
    _, run = await bound(rt, "test_deadline_restart")
    rt.start(run["id"])
    await asyncio.wait_for(waiting.wait(), 10)
    before = rt.db.one("SELECT * FROM control.batch WHERE run_id=%s", (run["id"],))
    assert before["last_part_uploaded"] == 1
    first = rt.db.one("SELECT * FROM control.run_attempt WHERE run_id=%s", (run["id"],))
    rt.beat(before)
    assert (
        rt.db.one(
            "SELECT lease_expires_at FROM control.batch WHERE id=%s", (before["id"],)
        )["lease_expires_at"]
        <= first["deadline_at"]
    )
    task = rt.tasks[str(run["id"])]
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    # Model an old worker that kept renewing its lease. Deadline still wins.
    rt.db.execute(
        "UPDATE control.batch SET lease_expires_at=now()+interval '1 hour' WHERE run_id=%s",
        (run["id"],),
    )
    await asyncio.sleep(
        max(0, (first["deadline_at"] - datetime.now(timezone.utc)).total_seconds())
        + 0.05
    )
    restarted = Runtime(rt.settings, transport=httpx.MockTransport(handler))
    try:
        await Recovery(restarted).once()
        assert not restarted.tasks
        await restarted.execute(run["id"], resume_only=True)
        expired = restarted.receipts(run["id"])["run"]
        assert expired["status"] == "failed"
        assert expired["error_class"] == "invoke_timeout"
        assert "deadline" in expired["error_message"].lower()
        batch = restarted.db.one(
            "SELECT * FROM control.batch WHERE run_id=%s", (run["id"],)
        )
        assert batch["status"] == "failed" and batch["lease_token"] is None
        assert batch["last_part_uploaded"] == before["last_part_uploaded"]
        assert batch["dump_ids"] == before["dump_ids"]
        assert calls == ["/page/0", "/page/1"]
        assert (
            restarted.db.one(
                "SELECT count(*) AS n FROM control.dump WHERE run_id=%s AND published_at>%s",
                (run["id"], first["deadline_at"]),
            )["n"]
            == 0
        )
        with pytest.raises(LeaseLost):
            restarted.beat(before)
        configure(databases, "test_deadline_restart", 10)
        block = False
        await restarted.execute(run["id"])
        attempts = restarted.db.all(
            "SELECT * FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no",
            (run["id"],),
        )
        assert [a["status"] for a in attempts] == ["failed", "succeeded"]
        assert attempts[1]["deadline_at"] > first["deadline_at"]
        assert restarted.receipts(run["id"])["run"]["status"] == "succeeded"
        assert calls == ["/page/0", "/page/1", "/page/1"]
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            assert conn.execute(
                "SELECT page,count(*) FROM raw.test_deadline_restart WHERE _run_id=%s GROUP BY page ORDER BY page",
                (run["id"],),
            ).fetchall() == [(0, 1), (1, 1)]
    finally:
        await restarted.close()


async def test_stale_reaper_cannot_expire_a_new_attempt(rt, databases):
    source("test_reaper_fence")
    sync(rt.db)
    _, run = await bound(rt, "test_reaper_fence")
    old = admission.attempt(rt.db, run["id"])
    rt.db.execute(
        "UPDATE control.run_attempt SET deadline_at=now()-interval '1 second' WHERE id=%s",
        (old["id"],),
    )
    fresh = admission.attempt(rt.db, run["id"])
    rt.timeout_attempt(run["id"], old)
    await asyncio.to_thread(Recovery(rt).reap)
    assert (
        rt.db.one("SELECT status FROM control.run_attempt WHERE id=%s", (fresh["id"],))[
            "status"
        ]
        == "running"
    )
    assert (
        rt.db.one("SELECT status FROM control.run WHERE id=%s", (run["id"],))["status"]
        == "running"
    )


async def test_reaper_unbound_orphans_preserves_fresh_events_and_live_leases(
    rt, monkeypatch
):
    monkeypatch.setenv("MDP_ORPHAN_RUN_TIMEOUT_S", "900")
    warehouse = rt.db.one("SELECT id FROM control.warehouse WHERE is_production")["id"]

    def record(age):
        return rt.db.one(
            "INSERT INTO control.run(kind,work_key,scope,warehouse_id,status,updated_at) VALUES ('dbt',%s,'global',%s,'running',now()-(%s * interval '1 second')) RETURNING id",
            (str(uuid4()), warehouse, age),
        )["id"]

    stale, fresh, recent_event, leased = (
        record(1800),
        record(0),
        record(1800),
        record(1800),
    )
    rt.db.execute(
        "INSERT INTO control.run_event(run_id,level,event_type,message) VALUES (%s,'info','dbt_webhook','progress')",
        (recent_event,),
    )
    rt.db.execute(
        "INSERT INTO control.batch(run_id,index,status,lease_expires_at,lease_token) VALUES (%s,0,'running',now()+interval '1 hour',%s)",
        (leased, uuid4()),
    )
    rt.db.execute(
        "INSERT INTO control.batch(run_id,index,status,lease_expires_at,lease_token) VALUES (%s,0,'running',now()-interval '1 hour',%s)",
        (stale, uuid4()),
    )
    await asyncio.to_thread(Recovery(rt).reap)
    rows = {
        r["id"]: r for r in rt.db.all("SELECT id,status,error_class FROM control.run")
    }
    assert (
        rows[stale]["status"] == "failed"
        and rows[stale]["error_class"] == "invoke_timeout"
    )
    assert all(
        rows[key]["status"] == "running" for key in (fresh, recent_event, leased)
    )
    assert rt.db.one(
        "SELECT status,lease_token FROM control.batch WHERE run_id=%s", (stale,)
    ) == {"status": "failed", "lease_token": None}


async def test_landing_rolls_back_if_blocked_past_deadline(rt, databases, monkeypatch):
    source("test_deadline_landing")
    sync(rt.db)
    configure(databases, "test_deadline_landing", 2)
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    # Use the same adapter instance so the deterministic commit barrier affects
    # worker threads without changing production landing/adapter code.
    rt.override_warehouse = True
    original = rt.warehouse.land
    done = threading.Event()
    blocked = False
    _, run = await bound(rt, "test_deadline_landing")

    def land(manifest, claim, parts, hook=None):
        nonlocal blocked

        def barrier(stage, conn):
            nonlocal blocked
            if stage == "before_commit" and not blocked:
                blocked = True
                attempt = rt.db.one(
                    "SELECT deadline_at FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
                    (run["id"],),
                )
                time.sleep(
                    max(
                        0,
                        (
                            attempt["deadline_at"] - datetime.now(timezone.utc)
                        ).total_seconds(),
                    )
                    + 0.1
                )
            if hook:
                hook(stage, conn)

        try:
            return original(manifest, claim, parts, barrier)
        finally:
            done.set()

    monkeypatch.setattr(rt.warehouse, "land", land)
    await rt.execute(run["id"])
    assert await asyncio.to_thread(done.wait, 5)
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM raw.test_deadline_landing WHERE _run_id=%s",
                (run["id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM raw._load_receipts WHERE committed_at IS NOT NULL AND target_table='raw.test_deadline_landing'"
            ).fetchone()[0]
            == 0
        )
    assert rt.db.one(
        "SELECT status,error_class FROM control.run WHERE id=%s", (run["id"],)
    ) == {"status": "failed", "error_class": "invoke_timeout"}
    configure(databases, "test_deadline_landing", 10)
    await asyncio.sleep(rt.settings.load_timeout_s)
    await rt.execute(run["id"])
    assert rt.receipts(run["id"])["run"]["status"] == "succeeded"
    assert rt.receipts(run["id"])["run"]["rows_written"] == 2


async def test_expired_invoke_does_not_block_explicit_retained_dump_repair(rt):
    source("test_deadline_repair")
    sync(rt.db)
    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    _, run = await bound(rt, "test_deadline_repair")
    await rt.execute(run["id"])
    load = rt.db.one(
        "SELECT l.* FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s ORDER BY d.published_at LIMIT 1",
        (run["id"],),
    )
    calls = rt.db.one(
        "SELECT count(*) AS n FROM control.call_ledger WHERE run_id=%s", (run["id"],)
    )["n"]
    rt.db.execute(
        "UPDATE control.run_attempt SET deadline_at=now()-interval '1 second' WHERE run_id=%s",
        (run["id"],),
    )
    rt.landing(load["warehouse_id"]).repair(
        load["dump_id"], load["warehouse_id"], load["target_table"]
    )
    await Recovery(rt).once(resume=False)
    repaired = rt.db.one(
        "SELECT status,generation FROM control.load WHERE id=%s", (load["id"],)
    )
    assert repaired == {"status": "loaded", "generation": load["generation"] + 1}
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.call_ledger WHERE run_id=%s",
            (run["id"],),
        )["n"]
        == calls
    )
    assert rt.receipts(run["id"])["run"]["status"] == "succeeded"
