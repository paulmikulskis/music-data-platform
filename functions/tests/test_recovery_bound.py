"""Old grades age out; unfinished and newly landed work still settles."""

from unittest.mock import Mock

import psycopg
import pytest
from conftest import bound
from mdp_functions import admission
from mdp_functions.layers import bronze
from mdp_functions.recovery import Recovery
from mdp_functions.registry import REGISTRY, sync
from mdp_functions.runs import Runtime
from test_alert_recovery import run as seed_run


@pytest.fixture(autouse=True)
def restore_registry():
    yield
    REGISTRY.pop("test_late_settlement", None)


def age_run(rt, run_id):
    rt.db.execute(
        "UPDATE control.run SET updated_at=now()-interval '8 days',settled_at=now()-interval '8 days' WHERE id=%s",
        (run_id,),
    )
    rt.db.execute(
        "UPDATE control.load SET updated_at=now()-interval '9 days' WHERE dump_id IN "
        "(SELECT id FROM control.dump WHERE run_id=%s)", (run_id,),
    )


@pytest.mark.parametrize("status", ["failed", "partial"])
def test_old_settled_runs_age_out_but_never_settled_and_recent_runs_do_not(rt, databases, monkeypatch, status):
    with psycopg.connect(databases["admin_control"]) as conn:
        run_id, _ = seed_run(conn, "fixture_accounts")
        conn.execute("UPDATE control.run SET status=%s,error_class='function_failed' WHERE id=%s", (status, run_id))
    age_run(rt, run_id)
    settle = Mock(wraps=rt.settle)
    monkeypatch.setattr(rt, "settle", settle)
    Recovery(rt).recover()
    settle.assert_not_called()
    rt.db.execute("UPDATE control.run SET settled_at=NULL WHERE id=%s", (run_id,))
    Recovery(rt).recover()
    settle.assert_called_once_with(run_id)
    marker = rt.db.one("SELECT settled_at FROM control.run WHERE id=%s", (run_id,))["settled_at"]
    assert marker is not None
    age_run(rt, run_id)
    rt.db.execute("UPDATE control.run SET updated_at=now()-interval '6 days' WHERE id=%s", (run_id,))
    before = rt.db.one("SELECT updated_at FROM control.run WHERE id=%s", (run_id,))["updated_at"]
    Recovery(rt).recover()
    assert settle.call_count == 2
    assert rt.db.one("SELECT updated_at FROM control.run WHERE id=%s", (run_id,))["updated_at"] == before


@pytest.mark.parametrize("pending", [True, False])
async def test_late_load_settles_an_old_run_even_after_acknowledgement(rt, monkeypatch, pending):
    @bronze(source_key="test_late_settlement", writes=["raw.test_late_settlement"], cadence="daily")
    async def source(ctx):
        ctx.observed(2)
        yield {"value": 1}

    sync(rt.db)
    _, run = await bound(rt, "test_late_settlement")
    await rt.execute(run["id"])
    assert rt.receipts(run["id"])["run"]["status"] == "partial"
    age_run(rt, run["id"])
    rt.db.execute("UPDATE control.run SET rows_written=0 WHERE id=%s", (run["id"],))
    rt.db.execute(
        "UPDATE control.load SET status=%s,updated_at=clock_timestamp() WHERE dump_id IN "
        "(SELECT id FROM control.dump WHERE run_id=%s)", ("pending" if pending else "loaded", run["id"]),
    )
    settle = Mock(wraps=rt.settle)
    monkeypatch.setattr(rt, "settle", settle)
    Recovery(rt).recover()
    settle.assert_called_once_with(run["id"])
    assert rt.receipts(run["id"])["run"]["rows_written"] == 1


async def test_old_run_retry_clears_marker_and_finishes_after_restart(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        run_id, _ = seed_run(conn, "fixture_accounts")
        conn.execute("UPDATE control.run SET status='failed',error_class='function_failed' WHERE id=%s", (run_id,))
    age_run(rt, run_id)
    attempt = admission.attempt(rt.db, run_id)
    assert attempt is not None
    retried = rt.db.one("SELECT * FROM control.run WHERE id=%s", (run_id,))
    assert retried["settled_at"] is None and retried["status"] == "running"
    # The worker has finished all batches but exits before it grades the run.
    # A new process completes that grade without relying on process memory.
    restarted = Runtime(rt.settings)
    try:
        Recovery(restarted).recover()
        result = restarted.db.one("SELECT * FROM control.run WHERE id=%s", (run_id,))
        assert result["status"] == "succeeded"
        assert result["settled_at"] is not None
        assert restarted.db.one("SELECT status FROM control.run_attempt WHERE id=%s", (attempt["id"],))["status"] == "succeeded"
    finally:
        await restarted.close()


@pytest.mark.parametrize("status,error", [
    ("failed", "function_failed"),
    ("partial", "time_budget"),
    ("succeeded", None),
])
@pytest.mark.parametrize("crash_at", ["before_close", "before_mirror"])
async def test_backfill_close_recovers_after_grade_commits(rt, databases, monkeypatch, status, error, crash_at):
    from mdp_functions import cycles

    with psycopg.connect(databases["admin_control"]) as conn:
        run_id, cycle_id = seed_run(conn, "fixture_accounts", prefix="backfill:", coverage="empty")
        conn.execute(
            "UPDATE control.run SET kind='backfill',status=%s,error_class=%s,coverage=%s,rows_written=0 WHERE id=%s",
            (status, error, "empty" if status == "succeeded" else "partial", run_id),
        )
    age_run(rt, run_id)
    with monkeypatch.context() as crash:
        if crash_at == "before_close":
            crash.setattr(rt.cycles, "close", Mock(side_effect=OSError("process stopped before close")))
        else:
            crash.setattr(cycles, "catch_up", Mock(side_effect=OSError("process stopped before mirror")))
        with pytest.raises(OSError, match="process stopped"):
            rt.settle(run_id)
    graded = rt.db.one("SELECT * FROM control.run WHERE id=%s", (run_id,))
    assert graded["status"] == status
    # Neither the grade nor recent work can hide the crash window from this test.
    assert graded["updated_at"] < graded["settled_at"]
    assert rt.db.one("SELECT status FROM control.cycle WHERE id=%s", (cycle_id,))["status"] == (
        "open" if crash_at == "before_close" else "closed"
    )
    restarted = Runtime(rt.settings)
    try:
        settle = Mock(wraps=restarted.settle)
        monkeypatch.setattr(restarted, "settle", settle)
        Recovery(restarted).recover()
        settle.assert_called_once_with(run_id)
        closed = restarted.db.one("SELECT * FROM control.cycle WHERE id=%s", (cycle_id,))
        assert closed["status"] == "closed"
        assert closed["close_no"] is not None
        mirrored = restarted.db.one("SELECT mirrored_close_no FROM control.scope_close WHERE scope='global'")
        assert mirrored["mirrored_close_no"] >= closed["close_no"]
        age_run(restarted, run_id)
        settle.reset_mock()
        Recovery(restarted).recover()
        settle.assert_not_called()
    finally:
        await restarted.close()
