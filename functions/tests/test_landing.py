"""Appendix C cases exercise real locks, independent connections and committed receipts."""

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import duckdb
import psycopg
import pytest
from conftest import bound
from mdp_functions.admission import acquire, attempt
from mdp_functions.layers import Ctx
from mdp_functions.registry import REGISTRY
from mdp_functions.runs import Runtime
from mdp_functions.warehouse.duckdb import DuckDBWarehouse

pytestmark = pytest.mark.docker


@pytest.fixture(params=["postgres", "duckdb"], autouse=True)
def adapter(request: pytest.FixtureRequest, rt: Runtime, tmp_path: Path) -> str:
    if request.param == "duckdb":
        rt.warehouse = DuckDBWarehouse(str(tmp_path / "conformance.duckdb"))
        rt.override_warehouse = True
        rt.cycles.warehouse = rt.warehouse
    return request.param


async def pending(rt: Runtime) -> dict[str, Any]:
    _, run = await bound(rt, "billboard_hot100")
    active = attempt(rt.db, run["id"])
    batch = acquire(rt.db, run, active, 30)
    ctx = Ctx(REGISTRY["billboard_hot100"], run)
    ctx.observed(1)
    ctx.yield_row(
        {
            "chart": "hot-100",
            "week": "2026-09-12",
            "position": 1,
            "title": "Fixture Track",
            "artist": "Fixture Ensemble",
        }
    )
    rt.dumps.publish(ctx, run, batch, [""], True)
    return rt.db.one(
        "SELECT l.*,d.run_id FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s",
        (run["id"],),
    )


def expire(rt: Runtime, load: dict[str, Any]) -> None:
    rt.db.execute(
        "UPDATE control.load SET claim_expires_at=now()-interval '1 second' WHERE id=%s",
        (load["id"],),
    )


def assertion(rt: Runtime, load: dict[str, Any], evidence: Path, case: str) -> None:
    if isinstance(rt.warehouse, DuckDBWarehouse):
        with duckdb.connect(rt.warehouse.path) as conn:
            count = conn.execute(
                "SELECT count(*) FROM raw.chart_entries WHERE _dump_id=?",
                [str(load["dump_id"])],
            ).fetchone()[0]
            receipts = conn.execute(
                "SELECT dump_id,generation,rows,committed_at FROM raw._load_receipts WHERE dump_id=?",
                [str(load["dump_id"])],
            ).fetchall()
        case += "-duckdb"
    else:
        with psycopg.connect(rt.settings.warehouse_url) as conn:
            count = conn.execute(
                "SELECT count(*) FROM raw.chart_entries WHERE _dump_id=%s",
                (load["dump_id"],),
            ).fetchone()[0]
            receipts = conn.execute(
                "SELECT dump_id,generation,rows,committed_at FROM raw._load_receipts WHERE dump_id=%s",
                (load["dump_id"],),
            ).fetchall()
    assert count == 1
    assert len(receipts) == 1 and receipts[0][2] == 1 and receipts[0][3] is not None
    state = rt.db.one(
        "SELECT status,repair_requested FROM control.load WHERE id=%s", (load["id"],)
    )
    assert state == {"status": "loaded", "repair_requested": False}
    (evidence / f"appendix-c-{case}.txt").write_text(
        json.dumps(
            {
                "case": case,
                "row_set_count": count,
                "receipt_rows": receipts,
                "control": state,
            },
            default=str,
            indent=2,
        )
        + "\n"
    )


class SimulatedCrash(RuntimeError):
    pass


async def test_crash_after_warehouse_commit(rt: Runtime, evidence_dir: Path) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])

    def crash(stage: str, conn: Any) -> None:
        if stage == "after_commit":
            raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        loader.process(load["id"], crash)
    expire(rt, load)
    loader.process(load["id"])
    assertion(rt, load, evidence_dir, "crash-after-warehouse-commit")


async def test_claim_expiry_mid_transaction(rt: Runtime, evidence_dir: Path) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])
    entered, release, contender = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def hold(stage: str, conn: Any) -> None:
        if stage == "after_fence":
            entered.set()
            assert release.wait(10)

    def observe(stage: str, conn: Any) -> None:
        if stage == "before_fence":
            contender.set()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(loader.process, load["id"], hold)
        assert entered.wait(10)
        original_token = rt.db.one(
            "SELECT claim_token FROM control.load WHERE id=%s", (load["id"],)
        )["claim_token"]
        expire(rt, load)
        second = pool.submit(loader.process, load["id"], observe)
        if isinstance(rt.warehouse, DuckDBWarehouse):
            # DuckDB serializes at the process lock, before its receipt fence.
            # This proves serialization only; direct rule-7 tests cover aborted/null receipts.
            deadline = time.monotonic() + 5
            while (
                rt.db.one(
                    "SELECT claim_token FROM control.load WHERE id=%s", (load["id"],)
                )["claim_token"]
                == original_token
            ):
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
        else:
            assert contender.wait(10)
        assert not second.done()
        release.set()
        first.result(10)
        second.result(10)
    assertion(rt, load, evidence_dir, "claim-expiry-mid-transaction")


async def test_concurrent_initial_loads(rt: Runtime, evidence_dir: Path) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])
    barrier = threading.Barrier(2)

    def worker() -> bool:
        barrier.wait(10)
        return loader.process(load["id"])

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        assert sum(f.result(10) for f in futures) == 1
    assertion(rt, load, evidence_dir, "concurrent-initial-loads")


async def test_repair_during_claimed_load(rt: Runtime, evidence_dir: Path) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])

    def request(stage: str, conn: Any) -> None:
        if stage == "after_fence":
            result = loader.repair(
                load["dump_id"], load["warehouse_id"], load["target_table"]
            )
            assert result["status"] == "claimed"

    loader.process(load["id"], request)
    state = rt.db.one(
        "SELECT generation,status,op FROM control.load WHERE id=%s", (load["id"],)
    )
    assert state == {"generation": 2, "status": "pending", "op": "repair"}
    loader.process(load["id"])
    assertion(rt, load, evidence_dir, "repair-during-claimed-load")


async def test_two_repairs_racing(rt: Runtime, evidence_dir: Path) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])
    loader.process(load["id"])
    barrier = threading.Barrier(2)

    def request() -> dict[str, Any]:
        barrier.wait(10)
        return loader.repair(
            load["dump_id"], load["warehouse_id"], load["target_table"]
        )

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(request) for _ in range(2)]
        messages = [f.result(10)["message"] for f in futures]
    assert sorted(messages) == ["repair already pending", "repair queued"]
    assert (
        rt.db.one("SELECT generation FROM control.load WHERE id=%s", (load["id"],))[
            "generation"
        ]
        == 2
    )
    loader.process(load["id"])
    assertion(rt, load, evidence_dir, "two-repairs-racing")


async def test_queued_repair_then_reaping(rt: Runtime, evidence_dir: Path) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])
    claim = loader.claim(load["id"])
    loader.repair(load["dump_id"], load["warehouse_id"], load["target_table"])
    expire(rt, load)
    loader.process(load["id"])
    assert (
        rt.db.one("SELECT generation FROM control.load WHERE id=%s", (load["id"],))[
            "generation"
        ]
        == 2
    )
    # A stale exiting worker cannot clear the queued repair or current claim.
    loader.finish(claim, "loaded", 999)
    loader.process(load["id"])
    assertion(rt, load, evidence_dir, "queued-repair-then-reaping")


async def test_loss_of_control_session_mid_transaction(
    rt: Runtime, databases: dict[str, str], evidence_dir: Path
) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])

    def disconnect(stage: str, conn: Any) -> None:
        if stage == "before_ack":
            # Terminate precisely the connection that finish() is about to UPDATE through.
            with psycopg.connect(databases["admin_control"]) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(%s)", (conn.info.backend_pid,)
                )

    with pytest.raises(psycopg.OperationalError):
        loader.process(load["id"], disconnect)
    expire(rt, load)
    loader.process(load["id"])
    assertion(rt, load, evidence_dir, "loss-of-control-session-mid-transaction")


async def test_rule_7_uncommitted_receipt_never_counts_as_done(rt: Runtime) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])
    claim = loader.claim(load["id"])
    dump = rt.db.one("SELECT * FROM control.dump WHERE id=%s", (load["dump_id"],))
    manifest, parts = loader.verify(dump, claim)
    rt.warehouse.ensure(manifest["target_table"], manifest["columns"])
    values = (
        str(load["dump_id"]),
        str(load["warehouse_id"]),
        load["target_table"],
        str(claim["claim_token"]),
    )
    if isinstance(rt.warehouse, DuckDBWarehouse):
        with duckdb.connect(rt.warehouse.path) as conn:
            conn.execute(
                "INSERT INTO raw._load_receipts VALUES (?,?,?,1,?,NULL,NULL)", values
            )
    else:
        with rt.warehouse.connect() as conn:
            conn.execute(
                "INSERT INTO raw._load_receipts VALUES (%s,%s,%s,1,%s,NULL,NULL)",
                values,
            )
    assert not any(
        str(r["dump_id"]) == str(load["dump_id"]) for r in rt.warehouse.receipts()
    )
    with pytest.raises(RuntimeError, match="no committed receipt"):
        rt.warehouse.land(manifest, claim, parts)
    assert (
        rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
            "status"
        ]
        == "claimed"
    )


async def test_rule_7_aborted_fence_leaves_no_trace(
    rt: Runtime, evidence_dir: Path
) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])

    def crash(stage: str, conn: Any) -> None:
        if stage == "before_commit":
            raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        loader.process(load["id"], crash)
    assert not any(
        str(r["dump_id"]) == str(load["dump_id"]) for r in rt.warehouse.receipts()
    )
    expire(rt, load)
    loader.process(load["id"])
    receipts = [
        r for r in rt.warehouse.receipts() if str(r["dump_id"]) == str(load["dump_id"])
    ]
    assert len(receipts) == 1 and receipts[0]["rows"] == 1


@pytest.mark.parametrize("stage", ["verify", "verify_shape", "ensure", "land"])
async def test_permanent_schema_and_decoding_failures_rejected(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    import pyarrow as pa

    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])

    def broken(*args: Any, **kwargs: Any) -> None:
        if stage == "verify_shape":
            raise TypeError("manifest must contain objects")
        if stage == "ensure":
            raise psycopg.errors.DatatypeMismatch("incompatible schema")
        raise pa.ArrowInvalid("corrupt page")

    monkeypatch.setattr(
        loader if stage.startswith("verify") else loader.warehouse,
        "verify" if stage.startswith("verify") else stage,
        broken,
    )
    loader.process(load["id"])
    assert (
        rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
            "status"
        ]
        == "rejected"
    )
    letter = rt.db.one(
        "SELECT * FROM control.dead_letter WHERE run_id=%s", (load["run_id"],)
    )
    assert letter["payload_ref"] and letter["reason"]
    assert rt.receipts(load["run_id"])["receipts"][0]["rows_written"] == "0"
    assert rt.receipts(load["run_id"])["receipts"][0]["rows_rejected"] == "1"
    assert rt.receipts(load["run_id"])["run"]["rows_rejected"] == 1
    assert rt.receipts(load["run_id"])["receipts"][0]["message"]
    assert rt.db.one(
        "SELECT runbook_slug FROM control.alert WHERE run_id=%s ORDER BY opened_at DESC LIMIT 1",
        (load["run_id"],),
    )["runbook_slug"]


async def test_infrastructure_failure_remains_retryable(
    rt: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    load = await pending(rt)
    loader = rt.landing(load["warehouse_id"])

    def unavailable(*args: Any) -> None:
        raise ConnectionError("temporary object store failure")

    monkeypatch.setattr(rt.store, "get", unavailable)
    with pytest.raises(ConnectionError):
        loader.process(load["id"])
    assert (
        rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
            "status"
        ]
        == "claimed"
    )
    assert rt.db.one("SELECT count(*) AS n FROM control.dead_letter")["n"] == 0


async def test_derived_ack_atomic_and_mirrored_before_return(
    rt: Runtime, databases: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mdp_functions.recovery import Recovery

    load = await pending(rt)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.streamline SET layer='silver' WHERE source_key='billboard_hot100'"
        )
    loader = rt.landing(load["warehouse_id"])
    original = loader.derived

    def crash(*args: Any) -> None:
        raise SimulatedCrash()

    monkeypatch.setattr(loader, "derived", crash)
    with pytest.raises(SimulatedCrash):
        loader.process(load["id"])
    assert (
        rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
            "status"
        ]
        == "claimed"
    )
    assert not rt.db.one(
        "SELECT * FROM control.cycle_input WHERE dump_id=%s", (load["dump_id"],)
    )
    monkeypatch.setattr(loader, "derived", original)
    expire(rt, load)
    loader.process(load["id"])
    assert (
        rt.db.one(
            "SELECT phase FROM control.cycle_input WHERE dump_id=%s", (load["dump_id"],)
        )["phase"]
        == "derived"
    )
    if isinstance(rt.warehouse, DuckDBWarehouse):
        with duckdb.connect(rt.warehouse.path) as conn:
            assert (
                conn.execute(
                    "SELECT phase FROM raw.cycle_inputs WHERE dump_id=?",
                    [str(load["dump_id"])],
                ).fetchone()[0]
                == "derived"
            )
    else:
        with rt.warehouse.connect() as conn:
            assert (
                conn.execute(
                    "SELECT phase FROM raw.cycle_inputs WHERE dump_id=%s",
                    (load["dump_id"],),
                ).fetchone()["phase"]
                == "derived"
            )
    # Simulate a legacy acknowledgement without membership; recovery must heal a loaded row too.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "DELETE FROM control.cycle_input WHERE dump_id=%s", (load["dump_id"],)
        )
    await Recovery(rt).once(resume=False)
    assert (
        rt.db.one(
            "SELECT phase FROM control.cycle_input WHERE dump_id=%s", (load["dump_id"],)
        )["phase"]
        == "derived"
    )


@pytest.mark.parametrize("truncated", [False, True])
async def test_corrupt_compressed_payload_exits_rejected(
    rt: Runtime, truncated: bool
) -> None:
    import gzip

    load = await pending(rt)
    dump = rt.db.one("SELECT * FROM control.dump WHERE id=%s", (load["dump_id"],))
    key = next(f["key"] for f in dump["files"] if f["role"] == "manifest")
    manifest = json.loads(rt.store.get(key))
    part_key = manifest["prefix"] + "corrupt.jsonl.gz"
    data = gzip.compress(b"{}")[:-2] if truncated else b"invalid gzip header"
    rt.store.put(part_key, data)
    manifest["files"].append(
        {
            "key": part_key,
            "role": "payload",
            "format": "jsonl.gz",
            "bytes": len(data),
            "row_count": 1,
        }
    )
    rt.store.put(key, json.dumps(manifest).encode())
    rt.landing(load["warehouse_id"]).process(load["id"])
    assert (
        rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
            "status"
        ]
        == "rejected"
    )
    assert rt.receipts(load["run_id"])["run"]["error_class"] == "dump_unreadable"
