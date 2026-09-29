"""One advisory-lock viewer reaps leases and recovers durable work on startup."""

import asyncio
import logging
import os
from typing import Any

import psycopg
import structlog
from botocore.exceptions import BotoCoreError, ClientError
from psycopg_pool import PoolTimeout

from mdp_functions import budget
from mdp_functions.dumps import json_bytes
from mdp_functions.errors import LeaseLost, ServiceError
from mdp_functions.runs import Runtime


def clear_validators(db: Any) -> list[dict[str, Any]]:
    """A rejected or quarantined dump never reconciles as full, so every target of its batch loses
    the validators (ETag and content hash) that batch wrote into its cursor, and the next fetch
    publishes full content instead of referencing it. A cursor already advanced by a later
    batch keeps its validators."""
    return db.all(
        """UPDATE control.cursor c SET cursor_value=c.cursor_value - 'validators',
        version=c.version+1, updated_at=now()
        WHERE jsonb_typeof(c.cursor_value)='object' AND c.cursor_value ? 'validators'
        AND EXISTS (
            SELECT 1 FROM control.dump written
            JOIN control.batch b ON b.run_id=written.run_id AND written.id=ANY(b.dump_ids)
            WHERE written.id=c.dump_id AND c.target_id=ANY(b.target_ids)
            AND EXISTS (
                SELECT 1 FROM control.dump d WHERE d.id=ANY(b.dump_ids)
                AND (d.quarantined_at IS NOT NULL OR d.rejected_at IS NOT NULL
                     OR EXISTS (SELECT 1 FROM control.load l WHERE l.dump_id=d.id AND l.status='rejected'))))
        RETURNING c.streamline_id,c.target_id"""
    )


class Recovery:
    # The reference probe reads the mirror and the warehouse; every 15 minutes is enough for its alerts.
    PROBE_EVERY_S = 900
    # Retention (retention.py) deletes what is past a function's retain_days; hourly keeps comment text to
    # its 30 days within the hour.
    RETAIN_EVERY_S = 3600

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.probed_at = float("-inf")
        self.retained_at = float("-inf")

    def retain(self) -> None:
        import time

        from mdp_functions.retention import sweep

        if time.monotonic() - self.retained_at < self.RETAIN_EVERY_S:
            return
        self.retained_at = time.monotonic()
        rt = self.runtime
        sweep(rt.db, rt.store, rt.settings.warehouse_url)

    def probe_reference(self) -> None:
        import os
        import time

        from mdp_functions.reference import probe

        if not os.environ.get("MDP_MB_DB_URL") or time.monotonic() - self.probed_at < self.PROBE_EVERY_S:
            return
        self.probed_at = time.monotonic()
        probe(self.runtime.db, self.runtime.settings.service_read_url)

    def reap(self) -> list[Any]:
        db = self.runtime.db
        from mdp_functions.cadence_health import sweep

        try:
            with db.transaction() as conn:
                sweep(conn)
        except Exception:  # noqa: BLE001 - advisory checks must not stop recovery
            logging.getLogger(__name__).warning("Cadence health check unavailable; recovery continues and retries the check next pass")
        runs = []
        # A refreshed lease never extends the attempt's hard deadline. Do this
        # before recovery can republish a manifest or resume an unfinished page.
        expired = db.all(
            "SELECT a.* FROM control.run_attempt a WHERE a.status='running' "
            "AND a.deadline_at<=clock_timestamp() AND NOT EXISTS "
            "(SELECT 1 FROM control.run_attempt newer WHERE newer.run_id=a.run_id AND newer.attempt_no>a.attempt_no)"
        )
        for attempt in expired:
            self.runtime.timeout_attempt(attempt["run_id"], attempt)
            runs.append(attempt["run_id"])
        with db.transaction() as conn:
            candidates = conn.execute(
                "SELECT id,run_id FROM control.batch WHERE status IN ('running','draining') AND lease_expires_at<now()"
            ).fetchall()
            for candidate in candidates:
                conn.execute(
                    "SELECT id FROM control.run WHERE id=%s FOR UPDATE",
                    (candidate["run_id"],),
                )
                row = conn.execute(
                    "SELECT * FROM control.batch WHERE id=%s AND status IN ('running','draining') AND lease_expires_at<now() FOR UPDATE",
                    (candidate["id"],),
                ).fetchone()
                if not row:
                    continue
                active = conn.execute(
                    "SELECT id,status,deadline_at>now() AS alive FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
                    (row["run_id"],),
                ).fetchone()
                resumable = (
                    active
                    and active["id"] == row["attempt_id"]
                    and active["status"] == "running"
                    and active["alive"]
                    and row["status"] != "draining"
                )
                conn.execute(
                    "UPDATE control.batch SET status=%s,lease_token=NULL,lease_expires_at=NULL WHERE id=%s",
                    ("queued" if resumable else "failed", row["id"]),
                )
                runs.append(row["run_id"])
            # Legacy webhook-only records have no attempt or worker heartbeat.
            # Bound their inactivity separately; never apply this fallback to
            # attempt-backed runs or records with a live batch lease.
            orphan_timeout = max(
                self.runtime.settings.lease_s,
                float(os.environ.get("MDP_ORPHAN_RUN_TIMEOUT_S", "900")),
            )
            orphans = conn.execute(
                "SELECT r.id FROM control.run r WHERE r.status IN ('queued','running') "
                "AND r.cycle_id IS NULL AND NOT EXISTS (SELECT 1 FROM control.run_attempt a WHERE a.run_id=r.id) "
                "AND greatest(r.updated_at,coalesce((SELECT max(e.at) FROM control.run_event e WHERE e.run_id=r.id),r.updated_at)) "
                "< clock_timestamp()-(%s * interval '1 second') "
                "AND NOT EXISTS (SELECT 1 FROM control.batch b WHERE b.run_id=r.id AND b.lease_expires_at>clock_timestamp()) "
                "FOR UPDATE OF r SKIP LOCKED",
                (orphan_timeout,),
            ).fetchall()
            for orphan in orphans:
                conn.execute(
                    "UPDATE control.batch SET status='failed',lease_token=NULL,lease_expires_at=NULL "
                    "WHERE run_id=%s AND status IN ('queued','running','draining')",
                    (orphan["id"],),
                )
                conn.execute(
                    "UPDATE control.run SET status='failed',coverage='partial',error_class='invoke_timeout',"
                    "error_message='Orphaned run inactivity deadline expired',updated_at=now() WHERE id=%s",
                    (orphan["id"],),
                )
                runs.append(orphan["id"])
        for run_id in set(runs):
            budget.settle(db, run_id)
        return runs

    async def once(self, resume: bool = True) -> None:
        for run_id in await asyncio.to_thread(self.recover):
            if resume:
                self.runtime.start(run_id, resume_only=True)
        await asyncio.to_thread(self.probe_reference)
        await asyncio.to_thread(self.retain)
        if self.runtime.settings.control_api_url:
            from mdp_functions.promoter import ControlTargets

            targets = ControlTargets(self.runtime.settings)
            async with targets.client() as client:
                try:
                    result = await targets.command(client, "park-stale", {})
                    from mdp_functions.control_db import open_alerts

                    for warning in result.get("warnings", []):
                        with self.runtime.db.transaction() as conn:
                            open_alerts(conn, warning["run_id"], [{"kind": "stale_target", "message": warning["reason"],
                                "target_id": None, "once": True, "attrs": {"source": warning["source"], "cycle_id": warning["cycle_id"]}}])
                except Exception:  # noqa: BLE001 - advisory checks fail open
                    logging.getLogger(__name__).warning("Target lifecycle check unavailable; scheduled work continues")

    def recover(self) -> list[Any]:
        rt = self.runtime
        resumable = []
        # Keep this dedicated session alive for the whole pass; transaction locks are insufficient.
        with rt.db.pool.connection() as viewer:
            locked = viewer.execute(
                "SELECT pg_try_advisory_lock(hashtext('mdp-recovery')) AS yes"
            ).fetchone()["yes"]
            viewer.commit()
            if not locked:
                return []
            try:
                self.reap()
                objects = set(rt.store.list("dumps"))
                for key in sorted(objects):
                    if not key.endswith("/manifest.json"):
                        continue
                    quarantine = key.removesuffix("manifest.json") + "quarantine.json"
                    if quarantine in objects:
                        continue
                    try:
                        rt.dumps.recover_manifest(key)
                    except (
                        LeaseLost,
                        FileNotFoundError,
                        KeyError,
                        TypeError,
                        ValueError,
                        ServiceError,
                    ) as exc:
                        rt.store.put(
                            quarantine,
                            json_bytes(
                                {
                                    "error_class": "dump_late"
                                    if isinstance(exc, LeaseLost)
                                    else "dump_unreadable",
                                    "message": str(exc),
                                }
                            ),
                        )
                        structlog.get_logger().warning(
                            "dump_unreadable", manifest=key, reason=str(exc)
                        )
                warehouses = rt.db.all("SELECT DISTINCT warehouse_id FROM control.load")
                for run in rt.db.all(
                    "SELECT DISTINCT d.run_id,l.warehouse_id FROM control.load l JOIN control.dump d ON d.id=l.dump_id "
                    "WHERE l.status IN ('pending','claimed') AND d.run_id IS NOT NULL"
                ):
                    # Runtime separates destination migration loads from the
                    # original attempt's deadline, just as retained repairs are.
                    rt.drain(run["warehouse_id"], run["run_id"])
                for warehouse in warehouses:
                    loader = rt.landing(warehouse["warehouse_id"])
                    loader.reconcile()
                # Grading commits before a backfill close. Keep that close eligible through
                # its warehouse mirror, even when the grade is old or succeeded.
                runs = rt.db.all("""SELECT r.*,c.status AS cycle_status FROM control.run r JOIN control.cycle c ON c.id=r.cycle_id
                    WHERE ((r.status IN ('queued','running','partial','failed')
                    AND (r.status IN ('queued','running') OR r.settled_at IS NULL
                        OR r.updated_at >= now()-interval '7 days'
                        OR EXISTS (SELECT 1 FROM control.dump d JOIN control.load l ON l.dump_id=d.id
                            WHERE d.run_id=r.id AND (l.status IN ('pending','claimed')
                                OR l.updated_at > r.settled_at
                                OR l.updated_at >= now()-interval '7 days'))))
                    OR (r.kind='backfill' AND r.status IN ('succeeded','partial','failed')
                        AND (c.status='open' OR (c.status='closed' AND (c.close_no IS NULL
                            OR c.close_no > coalesce((SELECT s.mirrored_close_no
                                FROM control.scope_close s WHERE s.scope=c.scope),-1))))))
                    AND NOT EXISTS (SELECT 1 FROM control.run_event e WHERE e.run_id=r.id AND e.event_type='cancel_requested')""")
                for run in runs:
                    rt.settle(run["id"])
                    failed = rt.db.one(
                        "SELECT count(*) AS n FROM control.batch WHERE run_id=%s AND status IN ('queued','failed')",
                        (run["id"],),
                    )["n"]
                    if (
                        failed
                        and run["cycle_status"] != "superseded"
                        and run["error_class"] is None
                        and rt.db.one(
                            "SELECT id FROM control.run_attempt WHERE run_id=%s AND status='running' AND deadline_at>now() ORDER BY attempt_no DESC LIMIT 1",
                            (run["id"],),
                        )
                    ):
                        resumable.append(run["id"])
                budget.settle(rt.db)
                clear_validators(rt.db)
                rt.cycles.mirror()
                return resumable
            finally:
                viewer.execute("SELECT pg_advisory_unlock(hashtext('mdp-recovery'))")
                viewer.commit()

    async def loop(self) -> None:
        while True:
            try:
                await self.once()
                await asyncio.sleep(30)
                await asyncio.to_thread(self.reap)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except (
                OSError,
                psycopg.Error,
                PoolTimeout,
                BotoCoreError,
                ClientError,
                ServiceError,
            ) as exc:
                structlog.get_logger().exception("recovery_failed", error=str(exc))
                await asyncio.sleep(30)
