"""Stable work identity, per-attempt deadlines, and durable batch permits."""

import json
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from mdp_functions.budget import reserve
from mdp_functions.control_db import ControlDB, event
from mdp_functions.errors import ServiceError
from mdp_functions.registry import Manifest

TERMINAL = {"succeeded", "partial", "failed", "superseded"}
BATCH_TERMINAL = {"succeeded", "partial", "failed"}


def work_key(
    source_key: str, scope: str, target_set_id: Any, revision_id: Any, cycle_id: Any
) -> str:
    return json.dumps(
        [
            source_key,
            scope,
            str(target_set_id) if target_set_id else None,
            str(revision_id) if revision_id else None,
            str(cycle_id),
        ],
        separators=(",", ":"),
    )


def canonical_key(
    manifest: Manifest,
    cycle: dict[str, Any],
    revision: dict[str, Any] | None,
    key: str | None = None,
    manual: bool = False,
) -> str:
    """The work key: canonical for scheduled work, `manual:` for manual invocations and reruns by
    config_version. It never carries configuration."""
    if manual:
        return "manual:" + json.dumps(
            [key, manifest.source_key, cycle["scope"]], separators=(",", ":")
        )
    return work_key(
        manifest.source_key,
        cycle["scope"],
        revision["target_set_id"] if revision else None,
        revision["id"] if revision else None,
        cycle["id"],
    )


def admit(
    db: ControlDB,
    manifest: Manifest,
    cycle: dict[str, Any],
    revision: dict[str, Any] | None,
    key: str | None = None,
    manual: bool = False,
    estimate_cents: int = 0,
    config_version: str | None = None,
    input_relation: str | None = None,
    backfill: dict[str, Any] | None = None,
    target_ids: list[Any] | None = None,
    resolved_config: dict[str, Any] | None = None,
    absent: str | None = None,
) -> dict[str, Any]:
    """Admit or return the run of a work key. `absent` admits a rebound cycle's no-op: the source has no
    membership in the export that froze the cycle, so the run succeeds empty with error_class not_in_cycle."""
    manual_key = key or str(uuid4())
    canonical = canonical_key(manifest, cycle, revision, manual_key, manual)
    with db.transaction() as conn:
        if manual:
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", ("manual:" + manual_key,)
            )
            reused = conn.execute(
                "SELECT work_key FROM control.run WHERE work_key LIKE 'manual:%%'"
            ).fetchall()
            for row in reused:
                suffix = row["work_key"].removeprefix("manual:")
                try:
                    identity = json.loads(suffix)
                except ValueError:
                    identity = [suffix]
                if not isinstance(identity, list) or not identity:
                    identity = [suffix]
                if identity[0] == manual_key and row["work_key"] != canonical:
                    raise ServiceError(
                        "idempotency_conflict",
                        "Idempotency key is already bound to another function or scope",
                    )
        # Admission and scheduled binding serialize on the same cadence/scope lock.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"cycle:{cycle['cadence']}:{cycle['scope']}",),
        )
        existing = conn.execute(
            "SELECT * FROM control.run WHERE work_key=%s", (canonical,)
        ).fetchone()
        if existing:
            if (
                existing["cycle_id"] != cycle["id"]
                or existing["revision_id"] != (revision["id"] if revision else None)
                or existing["config_version"] != config_version
                or existing["input_relation"] != input_relation
            ):
                raise ServiceError(
                    "idempotency_conflict",
                    "Idempotency key is already bound to different inputs",
                )
            event(
                conn,
                existing["id"],
                "duplicate_invocation",
                "Returning existing work and receipts",
            )
            return existing
        state = conn.execute(
            "SELECT status FROM control.cycle WHERE id=%s", (cycle["id"],)
        ).fetchone()
        if state["status"] == "superseded":
            raise ServiceError("superseded", "Superseded cycles admit no new work")
        warehouse = conn.execute(
            "SELECT id FROM control.warehouse WHERE is_production"
        ).fetchone()
        if not warehouse:
            raise ServiceError(
                "warehouse_unavailable", "No production warehouse is configured", 503
            )
        streamline = conn.execute(
            "SELECT * FROM control.streamline WHERE source_key=%s",
            (manifest.source_key,),
        ).fetchone()
        if not streamline:
            raise ServiceError(
                "unknown_source", "Run registry sync before admission", 404
            )
        tenant_id = (
            cycle["scope"].removeprefix("tenant:")
            if cycle["scope"].startswith("tenant:")
            else None
        )
        if (
            manifest.kind not in {"export", "close"}
            and manifest.tenant_bound
            and not tenant_id
        ):
            raise ServiceError(
                "scope_mismatch", "Tenant-bound source requires a tenant scope"
            )
        if (
            manifest.kind not in {"export", "close"}
            and not manifest.tenant_bound
            and tenant_id
        ):
            raise ServiceError("scope_mismatch", "Global source requires global scope")
        run = conn.execute(
            """INSERT INTO control.run(kind,work_key,cycle_id,scope,streamline_id,target_set_id,
            revision_id,tenant_id,warehouse_id,trace_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(work_key) DO NOTHING RETURNING *""",
            (
                manifest.kind,
                canonical,
                cycle["id"],
                cycle["scope"],
                streamline["id"],
                revision["target_set_id"] if revision else None,
                revision["id"] if revision else None,
                tenant_id,
                warehouse["id"],
                uuid4().hex,
            ),
        ).fetchone()
        if not run:
            return conn.execute(
                "SELECT * FROM control.run WHERE work_key=%s", (canonical,)
            ).fetchone()
        conn.execute(
            "UPDATE control.run SET config_version=%s,input_relation=%s,resolved_config=%s WHERE id=%s",
            (config_version, input_relation, Jsonb(resolved_config), run["id"]),
        )
        targets = (
            [
                r["target_id"]
                for r in conn.execute(
                    "SELECT target_id FROM control.target_export_member WHERE revision_id=%s ORDER BY target_id",
                    (revision["id"],),
                ).fetchall()
            ]
            if revision
            else []
        )
        if target_ids is not None:
            selected = {str(t) for t in target_ids}
            if not selected <= {str(t) for t in targets}:
                raise ServiceError("scope_mismatch", "Targets must belong to the frozen export", 422)
            targets = [t for t in targets if str(t) in selected]
        reserve(conn, run, max(0, estimate_cents) * (len(targets) if revision else 1) * 4)
        if backfill is not None:
            event(conn, run["id"], "backfill_requested", "Isolated backfill inputs", backfill)
        size = max(1, streamline["batch_size"])
        groups = (
            [targets[i : i + size] for i in range(0, len(targets), size)]
            if manifest.targets
            else [[]]
        )
        if absent:
            groups = []
            conn.execute(
                "UPDATE control.run SET status='succeeded',coverage='empty',error_class='not_in_cycle',error_message=%s WHERE id=%s",
                (absent, run["id"]),
            )
            event(conn, run["id"], "not_in_cycle", absent)
        elif not streamline["enabled"]:
            groups = []
            conn.execute(
                "UPDATE control.run SET status='succeeded',coverage='empty',error_class='paused',error_message='Streamline is paused' WHERE id=%s",
                (run["id"],),
            )
        for index, group in enumerate(groups):
            conn.execute(
                "INSERT INTO control.batch(run_id,index,target_ids) VALUES (%s,%s,%s)",
                (run["id"], index, group),
            )
        conn.execute(
            "UPDATE control.run SET expected_batches=%s WHERE id=%s",
            (len(groups), run["id"]),
        )
        return conn.execute(
            "SELECT * FROM control.run WHERE id=%s", (run["id"],)
        ).fetchone()


# Time a caller needs after the attempt ends: settle, publish the receipt and answer one poll.
SETTLE_MARGIN_S = 20.0


def attempt_seconds(timeout_s: float, deadline_s: float | None) -> float:
    """An attempt ends inside the caller's wait, so the caller reads its terminal receipt rather
    than giving up first. A caller budget under twice the margin keeps half of it."""
    if deadline_s is None:
        return float(timeout_s)
    return min(float(timeout_s), max(deadline_s - SETTLE_MARGIN_S, deadline_s / 2))


def attempt(
    db: ControlDB, run_id: Any, dbt_run_id: str | None = None, deadline_s: float | None = None
) -> dict[str, Any] | None:
    with db.transaction() as conn:
        run = conn.execute(
            "SELECT * FROM control.run WHERE id=%s FOR UPDATE", (run_id,)
        ).fetchone()
        # A run its time budget ended is terminal for its work key, like a paused one.
        if (
            run["status"] in {"succeeded", "superseded"}
            or run["error_class"] in {"paused", "time_budget"}
        ):
            return None
        cycle = conn.execute(
            "SELECT status FROM control.cycle WHERE id=%s", (run["cycle_id"],)
        ).fetchone()
        if cycle["status"] == "superseded":
            return None
        active = conn.execute(
            "SELECT * FROM control.run_attempt WHERE run_id=%s AND status='running' AND deadline_at>now() ORDER BY attempt_no DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if active:
            return active
        conn.execute(
            "UPDATE control.run_attempt SET status='failed',ended_at=now() WHERE run_id=%s AND status='running'",
            (run_id,),
        )
        stale = conn.execute(
            "SELECT id FROM control.batch WHERE run_id=%s AND status IN ('running','draining') FOR UPDATE",
            (run_id,),
        ).fetchall()
        for batch in stale:
            conn.execute(
                "UPDATE control.batch SET status='failed' WHERE id=%s", (batch["id"],)
            )
        config = conn.execute(
            "SELECT timeout_s FROM control.streamline WHERE id=%s",
            (run["streamline_id"],),
        ).fetchone()
        number = conn.execute(
            "SELECT coalesce(max(attempt_no),0)+1 AS n FROM control.run_attempt WHERE run_id=%s",
            (run_id,),
        ).fetchone()["n"]
        result = conn.execute(
            """INSERT INTO control.run_attempt(run_id,attempt_no,dbt_run_id,deadline_at,status)
            VALUES (%s,%s,%s,now()+(%s * interval '1 second'),'running') RETURNING *""",
            (run_id, number, dbt_run_id, attempt_seconds(config["timeout_s"], deadline_s)),
        ).fetchone()
        conn.execute(
            "UPDATE control.batch SET status='queued' WHERE run_id=%s AND status IN ('failed','partial')",
            (run_id,),
        )
        conn.execute(
            "UPDATE control.batch SET attempt_id=%s WHERE run_id=%s AND status='queued'",
            (result["id"], run_id),
        )
        conn.execute(
            "UPDATE control.run SET status='running',error_class=NULL,error_message=NULL,settled_at=NULL,updated_at=now() WHERE id=%s",
            (run_id,),
        )
        return result


def acquire(
    db: ControlDB, run: dict[str, Any], attempt_row: dict[str, Any], lease_s: float
) -> dict[str, Any] | None:
    with db.transaction() as conn:
        conn.execute("SELECT id FROM control.run WHERE id=%s FOR UPDATE", (run["id"],))
        latest = conn.execute(
            "SELECT id FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
        if not latest or latest["id"] != attempt_row["id"]:
            return None
        config = conn.execute(
            "SELECT * FROM control.streamline WHERE id=%s", (run["streamline_id"],)
        ).fetchone()
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", (config["source_key"],)
        )
        cycle = conn.execute(
            "SELECT status FROM control.cycle WHERE id=%s FOR SHARE", (run["cycle_id"],)
        ).fetchone()
        live = conn.execute(
            "SELECT id FROM control.run_attempt WHERE id=%s AND deadline_at>now() AND status='running'",
            (attempt_row["id"],),
        ).fetchone()
        if not live or cycle["status"] == "superseded":
            return None
        count = conn.execute(
            "SELECT count(*) AS n FROM control.batch b JOIN control.run r ON r.id=b.run_id WHERE r.streamline_id=%s AND b.status IN ('running','draining')",
            (run["streamline_id"],),
        ).fetchone()["n"]
        if count >= config["max_concurrency"]:
            return None
        row = conn.execute(
            "SELECT * FROM control.batch WHERE run_id=%s AND status='queued' ORDER BY index FOR UPDATE SKIP LOCKED LIMIT 1",
            (run["id"],),
        ).fetchone()
        if not row:
            return None
        return conn.execute(
            """UPDATE control.batch SET status='running',attempt_id=%s,worker_id=%s,lease_token=%s,
            lease_expires_at=now()+(%s * interval '1 second'),heartbeat_at=now() WHERE id=%s RETURNING *""",
            (attempt_row["id"], str(uuid4()), uuid4(), lease_s, row["id"]),
        ).fetchone()
