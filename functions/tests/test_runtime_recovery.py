"""Tests for runtime recovery."""

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import bound
from mdp_functions import admission
from mdp_functions.errors import LeaseLost, ServiceError
from mdp_functions.layers import Ctx, bronze
from mdp_functions.recovery import Recovery
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.runs import Runtime
from runtime_fixture import admin


async def test_old_timeout_and_batch_writes_cannot_touch_new_attempt(
    rt: Runtime,
) -> None:
    _, run = await bound(rt, "billboard_hot100")
    old = admission.attempt(rt.db, run["id"])
    stale = admission.acquire(rt.db, run, old, 30)
    rt.timeout_attempt(run["id"], old)
    new = admission.attempt(rt.db, run["id"])
    fresh = admission.acquire(rt.db, run, new, 30)
    rt.timeout_attempt(run["id"], old)
    for operation in (
        lambda: rt.complete_batch(stale, "failed"),
        lambda: rt.record_error(run, stale, ServiceError("function_failed", "stale")),
    ):
        with pytest.raises(LeaseLost):
            operation()
    rt.settle(run["id"], old["id"])
    assert rt.db.one(
        "SELECT status,lease_token FROM control.batch WHERE id=%s", (fresh["id"],)
    ) == {"status": "running", "lease_token": fresh["lease_token"]}
    assert (
        rt.db.one("SELECT error_class FROM control.run WHERE id=%s", (run["id"],))[
            "error_class"
        ]
        is None
    )


async def test_schema_breaking_preserves_page_payload_and_accounting(
    rt: Runtime,
) -> None:
    @bronze(
        source_key="bad_shape",
        writes=["raw.bad_shape"],
        cadence="daily",
        keep_payload=True,
    )
    async def source(ctx: Ctx) -> Any:
        ctx.payloads.append({"value": 1})
        ctx.observed(1)
        yield {"value": 1}
        await ctx.http.get("https://fixture.invalid/next")
        ctx.payloads.append({"value": "invalid"})
        ctx.observed(1)
        yield {"value": "invalid"}

    rt.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    try:
        sync(rt.db)
        _, run = await bound(rt, "bad_shape")
        await rt.execute(run["id"])
        result = rt.receipts(run["id"])
        assert result["run"]["error_class"] == "schema_breaking"
        assert result["run"]["rows_written"] == 1
        assert result["run"]["rows_rejected"] == 1
        assert result["receipts"][0]["rows_rejected"] == "1"
        letter = rt.db.one(
            "SELECT payload_ref FROM control.dead_letter WHERE run_id=%s", (run["id"],)
        )
        saved = json.loads(
            Path(letter["payload_ref"].removeprefix("file://")).read_bytes()
        )
        assert (
            saved["observed"] == 1
            and saved["outputs"]["raw.bad_shape"][0]["value"] == "invalid"
        )
    finally:
        REGISTRY.pop("bad_shape", None)


async def test_unexpected_source_exception_is_durable_terminal_failure(
    rt: Runtime,
) -> None:
    @bronze(source_key="unexpected", writes=["raw.unexpected"], cadence="daily")
    async def source(ctx: Ctx) -> Any:
        ctx.observed(1)
        ctx.payloads.append({"offending": True})
        yield {"value": 1}
        raise AttributeError("malformed source record")

    try:
        sync(rt.db)
        _, run = await bound(rt, "unexpected")
        await rt.execute(run["id"])
        result = rt.receipts(run["id"])
        assert (
            result["run"]["status"] == "failed"
            and result["run"]["error_class"] == "function_failed"
        )
        assert result["run"]["rows_rejected"] == 1
        assert result["receipts"][0]["rows_rejected"] == "1"
        assert rt.db.one(
            "SELECT payload_ref FROM control.dead_letter WHERE run_id=%s", (run["id"],)
        )["payload_ref"]
        await Recovery(rt).once()
        assert (
            rt.db.one(
                "SELECT count(*) AS n FROM control.run_attempt WHERE run_id=%s",
                (run["id"],),
            )["n"]
            == 1
        )
    finally:
        REGISTRY.pop("unexpected", None)


async def test_slow_object_store_does_not_stop_heartbeats(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run = await bound(rt, "billboard_hot100")
    entered, release = threading.Event(), threading.Event()
    original = rt.store.put

    def slow(key: str, data: bytes) -> None:
        entered.set()
        assert release.wait(5)
        original(key, data)

    monkeypatch.setattr(rt.store, "put", slow)
    task = asyncio.create_task(rt.execute(run["id"]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        first = rt.db.one(
            "SELECT heartbeat_at FROM control.batch WHERE run_id=%s", (run["id"],)
        )["heartbeat_at"]
        await asyncio.sleep(rt.settings.lease_s * 2)
        second = rt.db.one(
            "SELECT heartbeat_at FROM control.batch WHERE run_id=%s", (run["id"],)
        )["heartbeat_at"]
        assert second > first
    finally:
        release.set()
        await task


async def test_recovery_never_restarts_invoke_timeout(rt: Runtime) -> None:
    _, run = await bound(rt, "billboard_hot100")
    active = admission.attempt(rt.db, run["id"])
    rt.timeout_attempt(run["id"], active)
    for _ in range(2):
        await Recovery(rt).once()
    assert str(run["id"]) not in rt.tasks
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.run_attempt WHERE run_id=%s",
            (run["id"],),
        )["n"]
        == 1
    )


async def test_invalid_manifest_quarantine_does_not_stop_recovery(rt: Runtime) -> None:
    rt.store.put("dumps/bad/manifest.json", b"{")
    rt.store.put("dumps/other/manifest.json", b"[]")
    await Recovery(rt).once(resume=False)
    for name in ("bad", "other"):
        assert (
            json.loads(rt.store.get(f"dumps/{name}/quarantine.json"))["error_class"]
            == "dump_unreadable"
        )
    await Recovery(rt).once(resume=False)


async def test_multitable_registration_interruption_rolls_back_cursor_and_recovers(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    @bronze(
        source_key="atomic_page",
        writes=["raw.atomic_a", "raw.atomic_b"],
        cadence="daily",
    )
    async def source(ctx: Ctx) -> None:
        pass

    try:
        sync(rt.db)
        _, run = await bound(rt, "atomic_page")
        active = admission.attempt(rt.db, run["id"])
        batch = admission.acquire(rt.db, run, active, 30)
        ctx = Ctx(REGISTRY["atomic_page"], run)
        ctx.observed(2)
        ctx.emit("raw.atomic_a", {"value": 1})
        ctx.emit("raw.atomic_b", {"value": 2})
        ctx.set_cursor(None, {"page": 1})
        transaction = rt.db.transaction

        class Interrupted:
            def __init__(self, conn: Any) -> None:
                self.conn = conn
                self.count = 0

            def execute(self, query: Any, params: Any = ()) -> Any:
                if "INSERT INTO control.dump(" in str(query):
                    self.count += 1
                    if self.count == 2:
                        raise RuntimeError("interrupt between outputs")
                return self.conn.execute(query, params)

        @contextmanager
        def interrupted() -> Any:
            with transaction() as conn:
                yield Interrupted(conn)

        monkeypatch.setattr(rt.db, "transaction", interrupted)
        with pytest.raises(RuntimeError, match="between outputs"):
            rt.dumps.publish(ctx, run, batch, [""], True)
        monkeypatch.setattr(rt.db, "transaction", transaction)
        assert rt.db.one("SELECT count(*) AS n FROM control.dump")["n"] == 0
        assert rt.db.one("SELECT count(*) AS n FROM control.cursor")["n"] == 0
        assert (
            rt.db.one("SELECT dump_ids FROM control.batch WHERE id=%s", (batch["id"],))[
                "dump_ids"
            ]
            == []
        )
        manifests = [
            key for key in rt.store.list("dumps") if key.endswith("manifest.json")
        ]
        assert len(manifests) == 2
        for key in manifests:
            rt.dumps.recover_manifest(key)
        assert rt.db.one("SELECT count(*) AS n FROM control.dump")["n"] == 2
        assert rt.db.one("SELECT cursor_value FROM control.cursor")["cursor_value"] == {
            "page": 1
        }
        rt.drain(run["warehouse_id"], run["id"])
        assert rt.receipts(run["id"])["receipts"][0]["rows_written"] == "2"
    finally:
        REGISTRY.pop("atomic_page", None)


async def test_committed_rows_only_and_pending_repair_polling(rt: Runtime) -> None:
    from test_landing import pending

    load = await pending(rt)
    before = rt.receipts(load["run_id"])
    assert (
        before["run"]["rows_written"] == 0
        and before["receipts"][0]["rows_written"] == "0"
    )
    assert before["receipts"][0]["loads"][0]["status"] == "pending"
    loader = rt.landing(load["warehouse_id"])
    loader.process(load["id"])
    batch = rt.db.one("SELECT * FROM control.batch WHERE run_id=%s", (load["run_id"],))
    rt.complete_batch(batch, "succeeded")
    rt.settle(load["run_id"])
    assert rt.receipts(load["run_id"])["run"]["rows_written"] == 1
    loader.repair(load["dump_id"], load["warehouse_id"], load["target_table"])
    result = rt.receipts(load["run_id"])
    assert result["run"]["status"] == "succeeded" and result["repairs_pending"] == 1
    assert result["receipts"][0]["loads"][0]["status"] == "pending"
    loader.process(load["id"])
    assert rt.receipts(load["run_id"])["repairs_pending"] == 0


def test_core_runner_job_registration_and_registry_freshness_contract() -> None:
    from mdp_functions.registry import discover
    from mdp_functions.settings import REPO

    runner = (REPO / "ops/run.sh").read_text()
    assert "ON CONFLICT(job_id) DO UPDATE SET global_inputs=EXCLUDED.global_inputs" in runner
    assert "row != ('core', job[1], job[2])" in runner
    assert "reads - landed" in runner
    catalog = [
        m for m in discover().values() if m.cadence == "hourly" and m.kind == "invoke"
    ]
    # Freshness covers raw reads only; the hourly identity invokes read dbt relations of this job.
    reads = {r for m in catalog for r in m.reads if r.startswith("raw.")}
    writes = {r for m in catalog if m.layer == "bronze" for r in m.writes}
    assert reads - writes == set()


async def test_deadline_cancels_slow_publication_and_fences_late_registration(
    rt: Runtime, databases: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    admin(
        databases,
        "UPDATE control.streamline SET timeout_s=1 WHERE source_key='billboard_hot100'",
    )
    _, run = await bound(rt, "billboard_hot100")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    put = rt.store.put
    register = rt.dumps.register

    def slow(key: str, data: bytes) -> None:
        entered.set()
        assert release.wait(5)
        put(key, data)

    def observe_registration(*args: Any, **kwargs: Any) -> None:
        try:
            register(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(rt.store, "put", slow)
    monkeypatch.setattr(rt.dumps, "register", observe_registration)
    task = asyncio.create_task(rt.execute(run["id"]))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await asyncio.wait_for(task, 3)
        assert rt.receipts(run["id"])["run"]["error_class"] == "invoke_timeout"
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
    assert (
        rt.db.one(
            "SELECT count(*) AS n FROM control.dump WHERE run_id=%s", (run["id"],)
        )["n"]
        == 0
    )
    await Recovery(rt).once(resume=False)
    assert any(key.endswith("quarantine.json") for key in rt.store.list("dumps"))
