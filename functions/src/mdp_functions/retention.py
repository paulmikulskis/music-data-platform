"""Retention of what a function lands: a manifest's `retain_days` bounds how long its raw
rows and its runs' stored objects exist, crash or not.

`sweep` deletes, for each such function:

- its raw rows, and its `raw._rejected` rows, landed more than `retain_days` ago, as `loader_wh`, the raw owner;
- every object under `dumps/<source_key>/dt=<day>/` once that day is `retain_days` old, by listing the prefix,
  so parts a crash left without a manifest and quarantined dumps go with their day;
- every object a run stored (its dumps' listed files, and whatever sits under `inputs/<run>/`,
  `enrichment/<run>/` and `failures/<run>/`) once the run is `retain_days` old, or, for a function that
  declares `retain_from`, once the oldest input row it read (that column's landing time) is. A
  `retention_purged` run event records the run, so a later sweep skips it.

A function's sweep that raises opens a critical `retention_failed` alert and the sweep goes on to the next.
Authored text and retained input copies expire according to the declaring function’s retention window.
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import structlog

from mdp_functions.control_db import ControlDB, event
from mdp_functions.registry import Manifest, discover
from mdp_functions.store import ObjectStore

PURGED = "retention_purged"
FAILED = "retention_failed"
PARTITION = re.compile(r"/dt=(\d{4}-\d{2}-\d{2})/")


def run_objects(db: ControlDB, store: ObjectStore, run_id: Any) -> list[str]:
    """Every object key a run stored: its dumps' listed files, then whatever sits under its own prefixes."""
    keys = [
        str(part["key"])
        for dump in db.all("SELECT files FROM control.dump WHERE run_id=%s", (run_id,))
        for part in (dump["files"] or [])
        if isinstance(part, dict) and part.get("key")
    ]
    for prefix in (f"inputs/{run_id}/", f"enrichment/{run_id}/", f"failures/{run_id}/"):
        keys.extend(store.list(prefix))
    return sorted(set(keys))


def oldest_input(store: ObjectStore, run_id: Any, column: str) -> datetime | None:
    """The earliest `column` value among the input rows a run snapshotted (its JSON parts, registered or not)."""
    values = [
        datetime.fromisoformat(str(row[column]).replace(" ", "T", 1))
        for key in store.list(f"inputs/{run_id}/")
        if key.endswith(".json")
        for row in json.loads(store.get(key))
        if isinstance(row, dict) and row.get(column)
    ]
    return min((v if v.tzinfo else v.replace(tzinfo=UTC) for v in values), default=None)


def sweep_partitions(store: ObjectStore, source_key: str, days: int, today: date) -> int:
    """Delete every object under the function's dump days that are `days` old, manifest or not."""
    keys = [
        key for key in store.list(f"dumps/{source_key}/")
        if (found := PARTITION.search(key)) and date.fromisoformat(found.group(1)) + timedelta(days=days) <= today
    ]
    for key in keys:
        store.delete(key)
    return len(keys)


def sweep_function(db: ControlDB, store: ObjectStore, warehouse_url: str, manifest: Manifest,
                   now: datetime) -> dict[str, Any]:
    days, deleted, cutoff = manifest.retain_days or 0, {}, now - timedelta(days=manifest.retain_days or 0)
    with psycopg.connect(warehouse_url, autocommit=True) as conn:
        for table in [*manifest.writes, "raw._rejected"]:
            if conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is None:
                continue
            deleted[table] = conn.execute(
                f"DELETE FROM {table} WHERE _source_key = %s AND _ingested_at < %s", (manifest.source_key, cutoff)
            ).rowcount
    partitions = sweep_partitions(store, manifest.source_key, days, now.date())
    # A run is due once its own age passes retain_days; one that read retained text is due by its oldest row.
    runs = db.all(
        """SELECT r.id, r.created_at FROM control.run r JOIN control.streamline s ON s.id = r.streamline_id
        WHERE s.source_key = %(key)s AND (%(from)s OR r.created_at < %(cutoff)s)
        AND NOT EXISTS (SELECT 1 FROM control.run_event e WHERE e.run_id = r.id AND e.event_type = %(purged)s)
        ORDER BY r.created_at""",
        {"key": manifest.source_key, "from": manifest.retain_from is not None, "cutoff": cutoff, "purged": PURGED},
    )
    purged, objects = 0, 0
    for run in runs:
        since = run["created_at"]
        if manifest.retain_from:
            since = min(since, oldest_input(store, run["id"], manifest.retain_from) or since)
        if since >= cutoff:
            continue
        keys = run_objects(db, store, run["id"])
        for key in keys:
            store.delete(key)
        objects, purged = objects + len(keys), purged + 1
        with db.transaction() as conn:
            event(conn, run["id"], PURGED, f"Objects older than {days} days deleted",
                  {"retain_days": days, "objects": len(keys), "since": since.isoformat()})
    return {"rows": deleted, "runs": purged, "objects": objects, "partition_objects": partitions}


def failed(db: ControlDB, source_key: str, exc: Exception) -> None:
    """One open critical alert per function whose sweep fails; its log line names the error."""
    structlog.get_logger().error(FAILED, source_key=source_key, error=f"{type(exc).__name__}: {exc}")
    with db.transaction() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{FAILED}:{source_key}",))
        if conn.execute(
            "SELECT 1 FROM control.alert WHERE class=%s AND subject_type='streamline' AND subject_id=%s "
            "AND resolved_at IS NULL", (FAILED, source_key),
        ).fetchone():
            return
        conn.execute(
            "INSERT INTO control.alert(class,severity,subject_type,subject_id,runbook_slug) "
            "VALUES (%s,'critical','streamline',%s,'retention-failed')", (FAILED, source_key),
        )


def sweep(db: ControlDB, store: ObjectStore, warehouse_url: str, now: datetime | None = None) -> dict[str, Any]:
    """Apply every declared retain_days; returns the deleted counts per source key."""
    now = now or datetime.now(UTC)
    report: dict[str, Any] = {}
    for manifest in sorted(discover().values(), key=lambda m: m.source_key):
        if not manifest.retain_days:
            continue
        try:
            report[manifest.source_key] = sweep_function(db, store, warehouse_url, manifest, now)
        except Exception as exc:  # noqa: BLE001 - every failure alerts, and the other functions still sweep
            failed(db, manifest.source_key, exc)
            report[manifest.source_key] = {"error": type(exc).__name__}
    return report
