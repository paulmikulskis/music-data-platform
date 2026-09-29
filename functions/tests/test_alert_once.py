"""Resolved run alerts stay resolved until another attempt starts."""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event
from uuid import uuid4

import psycopg
import pytest
from conftest import bound
from mdp_functions import admission, landing
from mdp_functions.control_db import alert, envelope_miss, open_alerts
from mdp_functions.derived import end_with_rejects
from mdp_functions.http import FixtureTransport
from mdp_functions.recovery import Recovery
from mdp_functions.settings import PACKAGE, REPO


@pytest.fixture
async def failed_run(rt):
    rt.transport = FixtureTransport([PACKAGE / "sources/fixture_accounts/fixtures/html.jsonl"])
    _, run = await bound(rt)
    await rt.execute(run["id"])
    assert rt.receipts(run["id"])["run"]["status"] == "failed"
    return run["id"]


def alerts(rt, run_id, kind="partial_coverage"):
    return rt.db.all(
        "SELECT id,attempt_no,opened_at,resolved_at,resolved_by,resolution_reason FROM control.alert "
        "WHERE run_id=%s AND class=%s ORDER BY opened_at,id",
        (run_id, kind),
    )


def resolve(databases, run_id, kind="partial_coverage"):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.alert SET resolved_at=now(),resolved_by='fixture-operator',"
            "resolution_reason='Reviewed. Open /ops#alerts.' WHERE run_id=%s AND class=%s",
            (run_id, kind),
        )


@pytest.mark.parametrize("resolved", [False, True])
async def test_settle_opens_once_per_attempt(rt, databases, failed_run, resolved):
    if resolved:
        resolve(databases, failed_run)
    first = alerts(rt, failed_run)
    assert len(first) == 1
    rt.settle(failed_run)
    rt.settle(failed_run)
    assert alerts(rt, failed_run) == first

    await rt.execute(failed_run)
    second = alerts(rt, failed_run)
    assert len(second) == 2
    assert second[0] == first[0]
    assert second[1]["resolved_at"] is None
    latest = rt.db.one(
        "SELECT attempt_no,started_at FROM control.run_attempt WHERE run_id=%s "
        "ORDER BY attempt_no DESC LIMIT 1",
        (failed_run,),
    )
    assert latest["attempt_no"] == 2
    assert second[1]["attempt_no"] == latest["attempt_no"]
    rt.settle(failed_run)
    assert alerts(rt, failed_run) == second


def test_recovered_alert_stays_resolved_when_old_closed_run_settles(rt, databases, failed_run):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.cycle SET status='closed',closed_at=now() "
            "WHERE id=(SELECT cycle_id FROM control.run WHERE id=%s)",
            (failed_run,),
        )
        recovered = conn.execute(
            "INSERT INTO control.run(kind,work_key,cycle_id,scope,streamline_id,warehouse_id,"
            "status,coverage,rows_written,resolved_config) "
            "SELECT 'invoke',%s,cycle_id,scope,streamline_id,warehouse_id,"
            "'succeeded','full',1,'{}' FROM control.run WHERE id=%s RETURNING id",
            (uuid4().hex, failed_run),
        ).fetchone()[0]
    rt.db.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (recovered,))
    first = alerts(rt, failed_run)
    assert len(first) == 1
    assert first[0]["resolved_by"] == "system:recovery"
    rt.settle(failed_run)
    Recovery(rt).recover()
    assert alerts(rt, failed_run) == first


def test_accounting_alert_stays_resolved_after_settlement(rt, databases, failed_run):
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.run_event SET attrs=jsonb_set(attrs,'{observed}',"
            "to_jsonb((attrs->>'observed')::int+1)) "
            "WHERE run_id=%s AND event_type='page_published'",
            (failed_run,),
        )
    rt.settle(failed_run)
    resolve(databases, failed_run, "accounting_mismatch")
    first = alerts(rt, failed_run, "accounting_mismatch")
    assert len(first) == 1 and first[0]["resolved_at"]
    rt.settle(failed_run)
    assert alerts(rt, failed_run, "accounting_mismatch") == first


@pytest.mark.parametrize("once", [False, True])
def test_run_body_alert_uses_attempt_boundary(rt, databases, failed_run, once):
    item = {
        "kind": "vendor_4xx",
        "message": "Request refused. Open /runbooks/vendor-4xx.",
        "attrs": {},
        "target_id": None,
        "once": once,
    }
    with rt.db.transaction() as conn:
        open_alerts(conn, failed_run, [item, item])
    # Alert identity does not depend on its timestamp.
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.alert SET opened_at='2000-01-01' WHERE run_id=%s AND class='vendor_4xx'",
            (failed_run,),
        )
    resolve(databases, failed_run, "vendor_4xx")
    first = alerts(rt, failed_run, "vendor_4xx")
    with rt.db.transaction() as conn:
        open_alerts(conn, failed_run, [item])
    assert len(first) == 1
    assert alerts(rt, failed_run, "vendor_4xx") == first


@pytest.mark.parametrize("body", [False, True])
def test_concurrent_run_alerts_open_once(rt, failed_run, body):
    def open_one(_):
        with rt.db.transaction() as conn:
            if body:
                open_alerts(conn, failed_run, [{
                    "kind": "vendor_retryable",
                    "message": "Request failed. Open /runbooks/vendor-retryable.",
                    "attrs": {},
                    "target_id": None,
                }])
            else:
                alert(conn, failed_run, "vendor_retryable", str(failed_run))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(open_one, range(8)))
    assert len(alerts(rt, failed_run, "vendor_retryable")) == 1


async def test_run_without_an_attempt_keeps_its_resolved_alert(rt, databases):
    _, run = await bound(rt)
    with rt.db.transaction() as conn:
        alert(conn, run["id"], "vendor_retryable", str(run["id"]))
    resolve(databases, run["id"], "vendor_retryable")
    first = alerts(rt, run["id"], "vendor_retryable")
    with rt.db.transaction() as conn:
        alert(conn, run["id"], "vendor_retryable", str(run["id"]))
    assert len(first) == 1
    assert alerts(rt, run["id"], "vendor_retryable") == first


def test_target_once_alert_can_recur_after_resolution(rt, databases, failed_run):
    resolve(databases, failed_run, "target_zero_yield")
    baseline = len(alerts(rt, failed_run, "target_zero_yield"))
    target = rt.db.one("SELECT id FROM control.target LIMIT 1")["id"]
    item = {
        "kind": "target_zero_yield",
        "message": "Target has no output. Open /runbooks/target-zero-yield.",
        "attrs": {},
        "target_id": str(target),
        "once": True,
    }
    with rt.db.transaction() as conn:
        open_alerts(conn, failed_run, [item, item])
    assert len(alerts(rt, failed_run, "target_zero_yield")) == baseline + 1
    resolve(databases, failed_run, "target_zero_yield")
    with rt.db.transaction() as conn:
        open_alerts(conn, failed_run, [item])
    assert len(alerts(rt, failed_run, "target_zero_yield")) == baseline + 2


def test_admission_waiting_behind_settlement_gets_a_new_alert(rt, databases, failed_run, monkeypatch):
    # Observe the real admission connection before its run lock blocks.
    started = Event()
    pid = []
    transaction = rt.db.transaction

    @contextmanager
    def observed_transaction():
        with transaction() as conn:
            pid.append(conn.info.backend_pid)
            conn.execute("SELECT now()")
            started.set()
            yield conn

    with (
        psycopg.connect(databases["admin_control"], autocommit=True) as observer,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        with psycopg.connect(databases["admin_control"]) as settling:
            settling.execute("SELECT id FROM control.run WHERE id=%s FOR UPDATE", (failed_run,))
            monkeypatch.setattr(rt.db, "transaction", observed_transaction)
            retry = pool.submit(admission.attempt, rt.db, failed_run)
            assert started.wait(5)
            deadline = time.monotonic() + 5
            while not observer.execute(
                "SELECT 1 FROM pg_stat_activity WHERE pid=%s AND wait_event_type='Lock'", (pid[0],)
            ).fetchone():
                assert time.monotonic() < deadline
                time.sleep(.01)
            # Make the old failure's alert later than admission's transaction start.
            settling.execute("DELETE FROM control.alert WHERE run_id=%s AND class='partial_coverage'", (failed_run,))
            alert(settling, failed_run, "partial_coverage", str(failed_run))
            settling.execute(
                "UPDATE control.alert SET opened_at=clock_timestamp(),resolved_at=clock_timestamp() "
                "WHERE run_id=%s AND class='partial_coverage'", (failed_run,)
            )
        attempt = retry.result(timeout=5)
    monkeypatch.setattr(rt.db, "transaction", transaction)
    first = alerts(rt, failed_run)
    assert first[0]["opened_at"] > attempt["started_at"]
    assert first[0]["attempt_no"] == 1
    # The new attempt fails its floor, just as the previous one did.
    rt.db.execute("UPDATE control.batch SET status='failed' WHERE run_id=%s", (failed_run,))
    rt.settle(failed_run)
    result = alerts(rt, failed_run)
    assert [row["attempt_no"] for row in result] == [1, 2]
    assert result[0]["resolved_at"] is not None
    assert result[1]["resolved_at"] is None


def test_concurrent_landing_rejections_finish_without_deadlock(rt, failed_run, monkeypatch):
    run = rt.db.one("SELECT * FROM control.run WHERE id=%s", (failed_run,))
    dumps = []
    for _ in range(2):
        dump = rt.db.one(
            "INSERT INTO control.dump(run_id,streamline_id,cycle_id,scope,kind,uri_prefix,files) "
            "VALUES (%s,%s,%s,'global','output',%s,'[]') RETURNING *",
            (failed_run, run["streamline_id"], run["cycle_id"], 'fixture://' + uuid4().hex),
        )
        load = rt.db.one(
            "INSERT INTO control.load(dump_id,warehouse_id,target_table) VALUES (%s,%s,'raw.fixture') RETURNING id",
            (dump["id"], run["warehouse_id"]),
        )
        dumps.append((dump, load["id"]))
    loader = landing.Landing(rt.db, rt.store, rt.warehouse, warehouse_id=run["warehouse_id"])
    claims = [(dump, loader.claim(load_id)) for dump, load_id in dumps]
    barrier = Barrier(2)

    def after_dead_letter(conn, *args, **kwargs):
        # Both dead-letter inserts hold KEY SHARE before either helper runs.
        conn.execute("SET LOCAL statement_timeout='5s'")
        barrier.wait(timeout=5)
        alert(conn, *args, **kwargs)

    monkeypatch.setattr(landing, "alert", after_dead_letter)

    def reject_one(pair):
        dump, claim = pair
        loader.reject(claim, dump, "schema_breaking", ValueError("Fixture rejected. Open /ops#alerts."))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(reject_one, claims))
    assert len(alerts(rt, failed_run, "schema_breaking")) == 1
    for dump, load_id in dumps:
        assert rt.db.one("SELECT status FROM control.load WHERE id=%s", (load_id,))["status"] == "rejected"
        assert rt.db.one("SELECT rejected_at FROM control.dump WHERE id=%s", (dump["id"],))["rejected_at"]
    assert rt.db.one(
        "SELECT count(*) AS n FROM control.dead_letter WHERE run_id=%s AND reason LIKE 'schema_breaking:%%'",
        (failed_run,),
    )["n"] == 2


@pytest.mark.parametrize("with_attempt", [False, True])
async def test_legacy_alert_baseline_preserves_resolution_and_allows_retry(rt, databases, with_attempt):
    _, run = await bound(rt)
    run_id = run["id"]
    if with_attempt:
        admission.attempt(rt.db, run_id)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("ALTER TABLE control.alert DROP COLUMN attempt_no")
        # Simulate duplicate resolved alerts left by the previous service.
        for _ in range(2):
            conn.execute(
                "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,resolved_at) "
                "VALUES ('partial_coverage','warning','run',%s,%s,now())", (str(run_id), run_id)
            )
        for statement in (REPO / "control/packages/control-db/drizzle/0032_alert_attempt.sql").read_text().split("--> statement-breakpoint"):
            conn.execute(statement)
        for statement in (REPO / "control/packages/control-db/drizzle/0033_alert_attempt_baseline.sql").read_text().split("--> statement-breakpoint"):
            conn.execute(statement)
    with rt.db.transaction() as conn:
        alert(conn, run_id, "partial_coverage", str(run_id))
    baseline = alerts(rt, run_id)
    assert len(baseline) == 2
    assert all(row["resolved_at"] for row in baseline)
    assert sorted(row["attempt_no"] for row in baseline if row["attempt_no"] is not None) == [int(with_attempt)]
    rt.db.execute("UPDATE control.run_attempt SET status='failed' WHERE run_id=%s", (run_id,))
    admission.attempt(rt.db, run_id)
    with rt.db.transaction() as conn:
        alert(conn, run_id, "partial_coverage", str(run_id))
    result = alerts(rt, run_id)
    assert len(result) == 3
    assert result[-1]["attempt_no"] == int(with_attempt) + 1
    assert result[-1]["resolved_at"] is None


@pytest.mark.parametrize("detector", ["envelope", "derived"])
def test_renewed_drift_pauses_reenabled_source_on_retry(rt, databases, failed_run, detector):
    run = rt.db.one("SELECT * FROM control.run WHERE id=%s", (failed_run,))

    def drift(path):
        if path == "envelope":
            for _ in range(3):
                envelope_miss(rt.db, str(failed_run), str(uuid4()), "embed", "data.items")
        else:
            end_with_rejects(rt, run, "surface_drift", 3)

    def enabled():
        return rt.db.one(
            "SELECT enabled FROM control.streamline WHERE id=%s", (run["streamline_id"],)
        )["enabled"]

    drift(detector)
    first = alerts(rt, failed_run, "surface_drift")
    assert len(first) == 1 and first[0]["attempt_no"] == 1
    assert enabled() is False
    resolve(databases, failed_run, "surface_drift")
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("UPDATE control.streamline SET enabled=true WHERE id=%s", (run["streamline_id"],))
    resolved = alerts(rt, failed_run, "surface_drift")
    # Repeated reports in the resolved attempt neither reopen nor pause again.
    drift(detector)
    assert alerts(rt, failed_run, "surface_drift") == resolved
    assert enabled() is True

    retry = admission.attempt(rt.db, failed_run)
    assert retry["attempt_no"] == 2
    drift(detector)
    result = alerts(rt, failed_run, "surface_drift")
    assert len(result) == 2
    assert result[0] == resolved[0]
    assert result[1]["attempt_no"] == 2 and result[1]["resolved_at"] is None
    assert enabled() is False
    assert rt.db.one("SELECT severity FROM control.alert WHERE id=%s", (result[1]["id"],))["severity"] == "critical"
    # Both reporting paths share the same alert within this attempt.
    drift("derived" if detector == "envelope" else "envelope")
    assert alerts(rt, failed_run, "surface_drift") == result
