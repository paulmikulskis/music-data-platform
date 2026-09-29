"""An alert migration releases queued readers and can be retried after a lock timeout."""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import psycopg
import pytest
from conftest import bound, isolated_databases
from mdp_functions.settings import REPO


@pytest.mark.parametrize("attempts", [False, True])
def test_alert_alters_time_out_and_retry_atomically(databases, attempts):
    files = ["0032_alert_attempt.sql", "0033_alert_attempt_baseline.sql"] if attempts else ["0029_alert_recovery.sql"]
    names = ["attempt_no"] if attempts else ["resolved_by", "resolution_reason"]
    statements = [statement for name in files
                  for statement in (REPO / "control/packages/control-db/drizzle" / name).read_text().split("--> statement-breakpoint")]
    with contextmanager(isolated_databases)() as isolated:
        url = isolated["admin_control"]
        with psycopg.connect(url) as conn:
            if attempts:
                conn.execute("ALTER TABLE control.alert DROP COLUMN attempt_no")
            else:
                conn.execute("DROP FUNCTION control.resolve_recovered_alerts(uuid,uuid)")
                conn.execute("ALTER TABLE control.alert DROP COLUMN resolved_by, DROP COLUMN resolution_reason")
        with psycopg.connect(url) as holder, psycopg.connect(url, autocommit=True) as observer:
            holder.execute("SELECT count(*) FROM control.alert")  # Retain ACCESS SHARE until rollback.
            waiting = Event()
            pid = []

            def migrate():
                with psycopg.connect(url) as migration:
                    pid.append(migration.info.backend_pid)
                    waiting.set()
                    for statement in statements:
                        migration.execute(statement)

            def read():
                with psycopg.connect(url) as reader:
                    return reader.execute("SELECT count(*) FROM control.alert").fetchone()[0]

            with ThreadPoolExecutor(max_workers=2) as pool:
                started = time.monotonic()
                writer = pool.submit(migrate)
                assert waiting.wait(3)
                deadline = time.monotonic() + 3
                while not observer.execute("SELECT 1 FROM pg_locks WHERE pid=%s AND mode='AccessExclusiveLock' AND NOT granted", (pid[0],)).fetchone():
                    assert time.monotonic() < deadline
                    time.sleep(.01)
                reader = pool.submit(read)
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    writer.result(timeout=5)
                assert time.monotonic() - started < 5
                assert reader.result(timeout=3) == 0  # The holder still has its long transaction.
            columns = observer.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='control' "
                                       "AND table_name='alert' AND column_name=ANY(%s)", (names,)).fetchall()
            assert columns == []  # No partial schema change survives the failed transaction.
            holder.rollback()
            migrate()  # Bootstrap can rerun the same unapplied migration after the blocker ends.
            assert observer.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='control' "
                                    "AND table_name='alert' AND column_name=ANY(%s)", (names,)).fetchone()[0] == len(names)
            if attempts:
                assert observer.execute("SELECT to_regclass('control.alert_run_attempt_class_unique')").fetchone()[0]
                assert observer.execute("SELECT has_column_privilege('functions_rt', 'control.alert', 'attempt_no', 'INSERT')").fetchone()[0]


async def test_attempt_backfill_times_out_on_a_locked_alert_and_retries(rt, databases):
    _, run = await bound(rt)
    url = databases["admin_control"]
    with psycopg.connect(url) as conn:
        alert_id, resolved_at = conn.execute(
            "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,resolved_at) "
            "VALUES ('partial_coverage','warning','run',%s,%s,now()) RETURNING id,resolved_at",
            (str(run["id"]), run["id"]),
        ).fetchone()
    statements = (REPO / "control/packages/control-db/drizzle/0033_alert_attempt_baseline.sql").read_text().split("--> statement-breakpoint")

    def migrate():
        with psycopg.connect(url) as conn:
            for statement in statements:
                conn.execute(statement)

    with psycopg.connect(url) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.execute("SELECT id FROM control.alert WHERE id=%s FOR UPDATE", (alert_id,))
        started = time.monotonic()
        writer = pool.submit(migrate)
        with pytest.raises(psycopg.errors.LockNotAvailable):
            writer.result(timeout=5)
        assert time.monotonic() - started < 5
        assert holder.execute(
            "SELECT attempt_no,resolved_at FROM control.alert WHERE id=%s", (alert_id,)
        ).fetchone() == (None, resolved_at)
        holder.rollback()
        migrate()
        assert holder.execute(
            "SELECT attempt_no,resolved_at FROM control.alert WHERE id=%s", (alert_id,)
        ).fetchone() == (0, resolved_at)
