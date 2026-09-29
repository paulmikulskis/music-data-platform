"""Admission returns immediately; bounded workers publish, land and settle receipts."""

import asyncio
import hashlib
import inspect
import os
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import duckdb
import httpx
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from psycopg_pool import PoolTimeout

from mdp_functions import admission, budget
from mdp_functions.alert_recovery import repeated_partial
from mdp_functions.control_db import ControlDB, alert, event, fence, open_alerts
from mdp_functions.coverage import (
    exclusion_counts,
    rejection_count,
    row_coverage,
    target_coverage,
    zero_yield_targets,
)
from mdp_functions.cycles import Cycles, mirror_derived
from mdp_functions.dumps import Dumps
from mdp_functions.errors import LeaseLost, ServiceError
from mdp_functions.fetch.guard import source_network
from mdp_functions.health_policy import (
    AD_HOC_CYCLE_PREFIXES,
    MIN_ROW_COVERAGE,
    scheduled_cycle_sql,
)
from mdp_functions.http import FixtureTransport, TracedClient
from mdp_functions.landing import Landing
from mdp_functions.layers import Ctx, Target
from mdp_functions.registry import REGISTRY, discover, mirror_streamlines, sync
from mdp_functions.schemas import RUN_COMPLETION
from mdp_functions.settings import PACKAGE, Settings
from mdp_functions.store import make_store
from mdp_functions.targets import export_targets
from mdp_functions.telemetry import trace_url
from mdp_functions.warehouse.base import Warehouse, mirror
from mdp_functions.warehouse.postgres import PostgresWarehouse

UNMIRRORED_DERIVED = (
    "SELECT count(*) AS n FROM control.cycle_input i JOIN control.dump d ON d.id=i.dump_id "
    "WHERE d.run_id=%s AND i.mirrored_at IS NULL"
)
# Completion rows are per-input evidence; they never count as a run's written rows.
COMPLETION_DUMPS = (
    "SELECT DISTINCT d.id FROM control.dump d JOIN control.load l ON l.dump_id=d.id "
    "WHERE d.run_id=%s AND l.target_table IN ('raw._enrichment_completion','raw._run_completion')"
)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        warehouse: Warehouse | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = ControlDB(settings.control_url)
        self.store = make_store(settings)
        self.warehouse = warehouse or PostgresWarehouse(settings.warehouse_url)
        self.override_warehouse = warehouse is not None
        self.transport = transport
        self.dumps = Dumps(self.db, self.store, settings)
        self.cycles = Cycles(self.db, self.warehouse, settings)
        self.tasks: dict[str, asyncio.Task[None]] = {}
        discover()

    def initialize(self) -> None:
        sync(self.db, self.warehouse)

    def warehouse_for(self, warehouse_id: Any) -> Warehouse:
        if self.override_warehouse:
            return self.warehouse
        row = self.db.one(
            "SELECT * FROM control.warehouse WHERE id=%s", (warehouse_id,)
        )
        if not row or row["adapter"] != "postgres":
            raise ServiceError(
                "warehouse_unavailable", "Unsupported pinned warehouse adapter", 503
            )
        url = (
            self.settings.warehouse_url
            if row["dsn_secret_ref"] == "MDP_WAREHOUSE_URL"
            else os.environ.get(row["dsn_secret_ref"])
        )
        if not url:
            raise ServiceError(
                "warehouse_unavailable", "Pinned warehouse secret is unavailable", 503
            )
        return PostgresWarehouse(url)

    def landing(self, warehouse_id: Any) -> Landing:
        return Landing(
            self.db,
            self.store,
            self.warehouse_for(warehouse_id),
            self.settings.load_timeout_s,
            warehouse_id,
        )

    def admit(
        self,
        source_key: str,
        cadence: str | None = None,
        dbt_run_id: str | None = None,
        manual: bool = False,
        scope: str | None = None,
        key: str | None = None,
        target_set: str | None = None,
        model: str | None = None,
        input_relation: str | None = None,
        config_version: str | None = None,
        backfill: dict[str, Any] | None = None,
        target_kinds: list[str] | None = None,
        **unused: Any,
    ) -> dict[str, Any]:
        manifest = REGISTRY.get(source_key)
        if not manifest:
            raise ServiceError("unknown_source", f"Unknown function {source_key}", 404)
        # A rerun by config_version is its own manual work key with its own frozen configuration
        # and snapshot; the canonical run keeps its key.
        rerun = manifest.layer == "gold" and config_version is not None and not manual
        if manual:
            key = key or str(uuid4())
        if key and not manual and not rerun:
            transport_key = hashlib.sha256(
                "|".join(
                    (
                        source_key,
                        dbt_run_id or "",
                        model or "",
                        target_set or "",
                        input_relation or "",
                    )
                ).encode()
            ).hexdigest()
            if key != transport_key:
                raise ServiceError(
                    "invalid_work_key",
                    "Idempotency-Key does not match the transport request",
                    400,
                )
        if input_relation:
            from mdp_functions.derived import declared_input

            input_relation = declared_input(self.settings, manifest, input_relation)
        if rerun:
            if dbt_run_id and not scope:
                scope = (self.db.one(
                    "SELECT c.scope FROM control.cycle c JOIN control.cycle_attempt a ON a.cycle_id=c.id WHERE a.dbt_run_id=%s",
                    (dbt_run_id,),
                ) or {}).get("scope")
            # It binds to the newest closed scheduled cycle of the function's cadence and scope, the
            # cycle whose build the input relation holds, never to a caller-chosen run's cycle.
            cycle = self.db.one(
                f"""SELECT * FROM control.cycle WHERE cadence=%s AND scope=%s AND status='closed'
                AND {scheduled_cycle_sql()}
                ORDER BY close_no DESC NULLS LAST, closed_at DESC LIMIT 1""",
                (manifest.cadence, scope or "global"),
            )
            if not cycle:
                raise ServiceError("scope_mismatch", "A rerun needs a closed cycle of the function's cadence")
            manual = True
            key = key or "config:" + hashlib.sha256(
                "|".join((str(cycle["id"]), input_relation or "", config_version)).encode()
            ).hexdigest()[:32]
        elif dbt_run_id:
            cycle = self.db.one(
                """SELECT c.*,a.stamp_protocol FROM control.cycle c JOIN control.cycle_attempt a
                ON a.cycle_id=c.id WHERE a.dbt_run_id=%s""",
                (dbt_run_id,),
            )
            if not cycle:
                raise ServiceError(
                    "scope_mismatch", "dbt run must bind a cycle before invocation"
                )
            if manifest.kind == "close" and not cycle["stamp_protocol"]:
                # A run bound through a pre-stamp hook reads the bronze cycle_inputs list, which a stamp
                # close no longer writes: closing its cycle would build every relation empty.
                raise ServiceError(
                    "runner_outdated",
                    "This dbt run was bound by a pre-stamp hook; its cycle stays open for a run on the current image",
                    409,
                )
            if scope and scope != cycle["scope"]:
                raise ServiceError(
                    "scope_mismatch",
                    "Invocation scope differs from its immutable binding",
                )
        elif backfill is not None:
            # A separate cycle prevents backfill from superseding or extending a
            # scheduled cycle's immutable manifest. Its historical opening time
            # preserves the requested window for lineage and source execution.
            cycle = self.db.one(
                "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,image_digest,opened_at,manifest_mode) "
                "VALUES (%s,%s,%s,%s,%s,'stamp') RETURNING *",
                (manifest.cadence, scope or "global", "backfill:" + str(uuid4()),
                 self.settings.image_digest, (backfill.get("window") or {}).get("from") or datetime.now(timezone.utc)),
            )
        elif manual:
            cycle = self.cycles.manual(cadence or manifest.cadence, scope or "global")
        else:
            raise ServiceError(
                "scope_mismatch", "Invocation requires dbt_run_id or manual=true"
            )
        if manifest.scheduled_only and (
            manual
            or (key or "").startswith("manual:")
            or str(cycle["opened_by_dbt_run_id"]).startswith(AD_HOC_CYCLE_PREFIXES)
        ):
            # Refused before any read, so nothing lands and frozen rows stay unchanged.
            raise ServiceError(
                "scheduled_only_refused",
                f"{source_key} runs only in a cycle a scheduled run opened",
                409,
            )
        if cadence and cadence != cycle["cadence"]:
            raise ServiceError("scope_mismatch", "Cadence differs from the bound cycle")
        if input_relation:
            from mdp_functions.derived import tenant_read

            # A tenant model's relation is readable only by a run of that tenant's own scope.
            slug = tenant_read(manifest, input_relation)[1]
            owner = (
                self.db.one(
                    "SELECT slug FROM control.tenant WHERE id::text=%s",
                    (cycle["scope"].removeprefix("tenant:"),),
                )
                if cycle["scope"].startswith("tenant:")
                else None
            )
            if slug is not None and slug != (owner or {}).get("slug"):
                raise ServiceError(
                    "undeclared_read", "A tenant relation is readable only in its tenant's scope"
                )
        if manifest.kind == "invoke" and manifest.cadence != cycle["cadence"]:
            raise ServiceError(
                "scope_mismatch", "Function cadence differs from the bound cycle"
            )
        revision, absent = None, None
        if manifest.targets:
            tenant = (
                None
                if manifest.targets.scope == "global" or cycle["scope"] == "global"
                else cycle["scope"].removeprefix("tenant:")
            )
            if target_set and target_set != manifest.targets.kind:
                raise ServiceError(
                    "scope_mismatch", "Target kind differs from the declaration"
                )
            revision = self.db.one(
                """SELECT e.* FROM control.target_export e JOIN control.target_set s ON s.id=e.target_set_id
                WHERE e.cycle_id=%s AND s.kind=%s AND s.tenant_id IS NOT DISTINCT FROM %s::uuid""",
                (cycle["id"], manifest.targets.kind, tenant),
            )
            if not revision and manual:
                if backfill and backfill.get("cycle_id"):
                    with self.db.transaction() as conn:
                        prior = conn.execute(
                            "SELECT e.* FROM control.target_export e JOIN control.target_set s ON s.id=e.target_set_id "
                            "WHERE e.cycle_id=%s AND s.kind=%s AND s.tenant_id IS NOT DISTINCT FROM %s::uuid",
                            (backfill["cycle_id"], manifest.targets.kind, tenant),
                        ).fetchone()
                        if not prior:
                            raise ServiceError("scope_mismatch", "Requested cycle has no frozen source targets", 422)
                        revision = conn.execute(
                            "INSERT INTO control.target_export(cycle_id,target_set_id,member_count) VALUES (%s,%s,%s) RETURNING *",
                            (cycle["id"], prior["target_set_id"], prior["member_count"]),
                        ).fetchone()
                        conn.execute(
                            "INSERT INTO control.target_export_member(revision_id,target_id,resource_kind,canonical_key,params_json,target_json,spec_recovered) SELECT %s,target_id,resource_kind,canonical_key,params_json,target_json,spec_recovered "
                            "FROM control.target_export_member WHERE revision_id=%s", (revision["id"], prior["id"]),
                        )
                else:
                    revision = export_targets(
                        self.db, self.warehouse, cycle["id"], manifest.targets.kind, tenant
                    )
            if not revision and not manual:
                absent = self.absent_from_export(manifest, cycle, tenant, dbt_run_id)
            if not revision and not absent:
                raise ServiceError(
                    "scope_mismatch",
                    "Export target membership before invoking this source",
                )
        try:
            self.store.health()
        except (OSError, BotoCoreError, ClientError) as exc:
            raise ServiceError(
                "object_store_unavailable", "Object storage is unreachable", 503
            ) from exc
        destination = self.db.one(
            "SELECT id FROM control.warehouse WHERE is_production"
        )
        if not destination:
            raise ServiceError(
                "warehouse_unavailable", "No production warehouse is configured", 503
            )
        warehouse = self.warehouse_for(destination["id"])
        try:
            warehouse.health()
        except (OSError, psycopg.Error, PoolTimeout, duckdb.Error) as exc:
            raise ServiceError(
                "warehouse_unavailable", "Warehouse is unreachable", 503
            ) from exc
        resolved_config = None
        if manifest.per_input:
            from mdp_functions.derived import resolve_config

            # A Retry or a Replay attaches to its work key's frozen configuration, whatever the
            # current code; only a new work key resolves the declared configuration.
            existing = self.db.one(
                "SELECT config_version,resolved_config FROM control.run WHERE work_key=%s",
                (admission.canonical_key(manifest, cycle, revision, key, manual),),
            )
            if existing:
                config_version, resolved_config = existing["config_version"], existing["resolved_config"]
            else:
                # A disabled streamline admits as paused and sends no request, so it needs neither a
                # prompt configuration nor the proxy.
                streamline = self.db.one("SELECT enabled FROM control.streamline WHERE source_key=%s", (source_key,))
                paused = streamline is not None and not streamline["enabled"]
                config_version, resolved_config = resolve_config(self, manifest, config_version, paused)
                # A rerun's dumps wait for the next close's stamp; they never become derived rows.
                resolved_config["rerun"] = rerun
        elif manifest.kind == "export":
            if target_kinds is None:
                # Exports without a payload list (manual Run Now) read the same generated macro.
                from mdp_functions.exporter import declared_export_kinds

                target_kinds = declared_export_kinds(cycle["cadence"], cycle["scope"])
            resolved_config = {"target_kinds": sorted(set(target_kinds))}
        if manifest.targets or manifest.min_target_coverage is not None:
            resolved_config = {**(resolved_config or {}), "target_coverage": {
                "min_target_coverage": manifest.min_target_coverage,
                "untargeted": manifest.targets is None,
                "stale_target_cycles": manifest.stale_target_cycles,
            }}
        if manifest.kind == "invoke":
            resolved_config = {**(resolved_config or {}), "min_row_coverage": manifest.min_row_coverage}
        if self.settings.fixture:
            resolved_config = {**(resolved_config or {}), "fixture": True, "fixture_scenario": self.settings.fixture_scenario}
        result = admission.admit(
            self.db,
            replace(manifest, kind="backfill") if backfill is not None else manifest,
            cycle,
            revision,
            key if manual else None,
            manual,
            self.settings.vendor_estimates.get(source_key, 0),
            config_version=config_version,
            resolved_config=resolved_config,
            input_relation=input_relation
            or (manifest.reads[0] if manifest.reads else None),
            backfill=backfill,
            target_ids=backfill.get("target_ids") if backfill else None,
            absent=absent,
        )
        mirror_streamlines(
            self.db, self.warehouse_for(result["warehouse_id"]), source_key
        )
        return result

    def absent_from_export(
        self, manifest: Any, cycle: dict[str, Any], tenant: str | None, dbt_run_id: str | None
    ) -> str | None:
        """Why a bronze source has no membership in a cycle an earlier run froze, or None.

        A restore, a Retry or a Replay rebinds a cycle whose membership froze with the export of the run
        that opened it. A source that export could not freeze has nothing to collect there and records
        not_in_cycle: its kind is in no frozen kind list (the source came after the cycle froze), or its
        target set was created after every export that listed the kind (the targets came after). The run
        that opens a cycle, a cycle with no succeeded export or no frozen list, a missing target set, a kind
        an export should have frozen, and a scope that does not fit the source stay scope_mismatch."""
        if manifest.layer != "bronze" or not dbt_run_id or cycle["opened_by_dbt_run_id"] == dbt_run_id:
            return None
        if manifest.tenant_bound != cycle["scope"].startswith("tenant:"):
            return None
        kind = manifest.targets.kind
        target_set = self.db.one(
            "SELECT created_at FROM control.target_set WHERE kind=%s AND tenant_id IS NOT DISTINCT FROM %s::uuid",
            (kind, tenant),
        )
        exports = self.db.all(
            """SELECT created_at,resolved_config->'target_kinds' AS kinds FROM control.run
            WHERE cycle_id=%s AND kind='export' AND status='succeeded' AND resolved_config ? 'target_kinds'""",
            (cycle["id"],),
        )
        if not target_set or not exports:
            return None
        # A tenant export freezes the global sets its tenant functions read as global:<kind>.
        label = "global:" + kind if tenant is None and cycle["scope"] != "global" else kind
        listed = [e for e in exports if label in e["kinds"]]
        if any(target_set["created_at"] <= e["created_at"] for e in listed):
            return None
        before = f"before its {kind} target set existed" if listed else f"without the {kind} kind"
        return (f"not_in_cycle: cycle {cycle['id']} froze its membership {before}, so "
                f"{manifest.source_key} has nothing to collect in it")

    def backfill(self, source_key: str, window: dict[str, str] | None = None,
                 target_ids: list[str] | None = None, cycle_id: str | None = None,
                 scope: str | None = None) -> dict[str, Any]:
        manifest = REGISTRY.get(source_key)
        if not manifest or manifest.kind != "invoke":
            raise ServiceError("unknown_source", "Backfill requires an invokable source", 404)
        if sum(x is not None for x in (window, target_ids, cycle_id)) != 1:
            raise ServiceError("invalid_request", "Select one backfill window, target list, or cycle", 422)
        if target_ids is not None and not manifest.targets:
            raise ServiceError("scope_mismatch", "Source does not accept targets", 422)
        if cycle_id:
            prior = self.db.one("SELECT * FROM control.cycle WHERE id=%s", (cycle_id,))
            if not prior or prior["status"] != "closed" or prior["cadence"] != manifest.cadence:
                raise ServiceError("scope_mismatch", "Select a closed cycle of the source cadence", 422)
            if scope and scope != prior["scope"]:
                raise ServiceError("scope_mismatch", "Scope differs from selected cycle", 422)
            scope = prior["scope"]
            # A cycle's data window spans its cadence, not the few seconds its
            # job took to execute (weekly chart dates may follow the job date).
            span = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(days=7)}[manifest.cadence]
            window = {"from": prior["opened_at"].isoformat(), "to": (prior["opened_at"] + span).isoformat()}
        scope = scope or "global"
        if manifest.tenant_bound != scope.startswith("tenant:"):
            raise ServiceError("scope_mismatch", "Source and backfill scope differ", 422)
        return self.admit(source_key, manual=True, scope=scope, key="backfill:" + str(uuid4()),
                          backfill={"window": window, "target_ids": target_ids, "cycle_id": cycle_id})

    def migrate(self, warehouse_id: Any, source_keys: list[str] | None = None,
                since: datetime | None = None) -> dict[str, Any]:
        loader = self.landing(warehouse_id)
        loader.warehouse.health()
        # Only retained output whose original destination committed qualifies.
        # Registration is one idempotent transaction; a crash is drained by recovery.
        with self.db.transaction() as conn:
            dumps = conn.execute(
                "SELECT DISTINCT d.id,l.target_table FROM control.dump d JOIN control.run r ON r.id=d.run_id "
                "JOIN control.streamline s ON s.id=d.streamline_id JOIN control.load l ON l.dump_id=d.id "
                "AND l.warehouse_id=r.warehouse_id WHERE d.kind='output' AND d.published_at IS NOT NULL "
                "AND d.quarantined_at IS NULL AND l.status='loaded' "
                "AND (%s::text[] IS NULL OR s.source_key=ANY(%s)) "
                "AND (%s::timestamptz IS NULL OR d.published_at>=%s)",
                (source_keys, source_keys, since, since),
            ).fetchall()
            queued = 0
            for dump in dumps:
                queued += conn.execute(
                    "INSERT INTO control.load(dump_id,warehouse_id,target_table,op) VALUES (%s,%s,%s,'load') "
                    "ON CONFLICT(dump_id,warehouse_id,target_table) DO NOTHING",
                    (dump["id"], warehouse_id, dump["target_table"]),
                ).rowcount
        ids = [d["id"] for d in dumps]
        for load in self.db.all("SELECT id FROM control.load WHERE warehouse_id=%s AND dump_id=ANY(%s::uuid[])", (warehouse_id, ids)):
            loader.process(load["id"])
        # The mirrors are incremental: mirrored_close_no only advances and revisions are written once,
        # so the destination gets the whole mirror history of every warehouse runs were pinned to.
        # Every mirror upserts by its key, so a repeated migrate changes nothing.
        for source in self.db.all(
            "SELECT DISTINCT warehouse_id FROM control.run WHERE warehouse_id<>%s ORDER BY 1", (warehouse_id,)
        ):
            mirror(loader.warehouse, self.warehouse_for(source["warehouse_id"]).mirror_rows())
        counts = {r["status"]: r["n"] for r in self.db.all(
            "SELECT status,count(*) AS n FROM control.load WHERE warehouse_id=%s AND dump_id=ANY(%s::uuid[]) GROUP BY status",
            (warehouse_id, ids),
        )}
        return {"warehouse_id": str(warehouse_id), "selected": len(dumps), "queued": queued,
                "loaded": counts.get("loaded", 0), "rejected": counts.get("rejected", 0),
                "pending": counts.get("pending", 0) + counts.get("claimed", 0)}

    def start(self, run_id: Any, resume_only: bool = False) -> None:
        key = str(run_id)
        if key not in self.tasks or self.tasks[key].done():
            task = asyncio.create_task(
                self.execute(key, resume_only=resume_only), name=f"run:{key}"
            )
            self.tasks[key] = task

            def report_failure(done: asyncio.Task[None]) -> None:
                if not done.cancelled() and done.exception():
                    import structlog

                    structlog.get_logger().error(
                        "worker_failed", run_id=key, error=str(done.exception())
                    )

            task.add_done_callback(report_failure)

    def drain(self, warehouse_id: Any, run_id: Any) -> None:
        loader = self.landing(warehouse_id)
        attempt = self.db.one(
            "SELECT * FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
            (run_id,),
        )
        if not attempt:
            loader.drain(run_id)
            return
        # Explicit repair is a separate operation on retained committed output,
        # not continued execution of the original invoke attempt.
        for repair in self.db.all(
            "SELECT l.id FROM control.load l JOIN control.dump d ON d.id=l.dump_id "
            "JOIN control.run r ON r.id=d.run_id "
            "WHERE d.run_id=%s AND l.warehouse_id=%s AND (l.op='repair' OR l.warehouse_id<>r.warehouse_id) "
            "AND (l.status='pending' OR (l.status='claimed' AND l.claim_expires_at<now()))",
            (run_id, warehouse_id),
        ):
            loader.process(repair["id"])

        def deadline(stage: str, conn: Any) -> None:
            if stage not in {"before_fence", "after_fence", "before_commit"}:
                return
            current = self.db.one(
                "SELECT id,status,extract(epoch FROM deadline_at-clock_timestamp()) AS remaining "
                "FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
                (run_id,),
            )
            if current["id"] != attempt["id"]:
                raise LeaseLost()
            if current["remaining"] <= 0 or current["status"] != "running":
                raise TimeoutError("Attempt deadline expired before landing")
            if isinstance(conn, psycopg.Connection):
                # Bound blocked warehouse statements as well as the worker task.
                conn.execute(
                    "SELECT set_config('statement_timeout',%s,true)",
                    (str(max(1, int(current["remaining"] * 1000))),),
                )

        if attempt["status"] != "running":
            return
        try:
            for _ in range(10):
                deadline("before_fence", None)
                loads = self.db.all(
                    "SELECT l.id FROM control.load l JOIN control.dump d ON d.id=l.dump_id "
                    "WHERE d.run_id=%s AND l.warehouse_id=%s "
                    "AND (l.status='pending' OR (l.status='claimed' AND l.claim_expires_at<now()))",
                    (run_id, warehouse_id),
                )
                if not loads:
                    return
                for load in loads:
                    loader.process(load["id"], hook=deadline)
        except psycopg.errors.QueryCanceled:
            # The deadline hook installed the warehouse statement timeout.
            self.timeout_attempt(run_id, attempt)
            return
        except TimeoutError:
            self.timeout_attempt(run_id, attempt)
            return
        raise ServiceError(
            "warehouse_unavailable", "Loads remain pending after repeated repairs"
        )

    def timeout_attempt(self, run_id: Any, attempt_row: dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            conn.execute("SELECT id FROM control.run WHERE id=%s FOR UPDATE", (run_id,))
            current = conn.execute(
                "SELECT * FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if (
                not current
                or current["id"] != attempt_row["id"]
                or current["status"] != "running"
            ):
                return
            rows = conn.execute(
                "SELECT * FROM control.batch WHERE run_id=%s AND attempt_id=%s AND status IN ('running','draining','queued') FOR UPDATE",
                (run_id, attempt_row["id"]),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE control.batch SET status='failed',lease_expires_at=NULL,lease_token=NULL WHERE id=%s AND attempt_id=%s AND lease_token IS NOT DISTINCT FROM %s",
                    (row["id"], attempt_row["id"], row["lease_token"]),
                )
            conn.execute(
                "UPDATE control.run_attempt SET status='failed',ended_at=now() WHERE id=%s",
                (attempt_row["id"],),
            )
            conn.execute(
                "UPDATE control.run SET status='failed',coverage='partial',updated_at=now(),error_class='invoke_timeout',error_message='Attempt deadline expired; a new attempt can resume' WHERE id=%s",
                (run_id,),
            )
        budget.settle(self.db, run_id)

    async def execute(self, run_id: Any, resume_only: bool = False) -> None:
        run = await asyncio.to_thread(
            self.db.one, "SELECT * FROM control.run WHERE id=%s", (run_id,)
        )
        if not run or run["status"] in {"succeeded", "superseded"}:
            return
        attempt_row = (
            await asyncio.to_thread(
                self.db.one,
                "SELECT * FROM control.run_attempt WHERE run_id=%s AND status='running' AND deadline_at>now() ORDER BY attempt_no DESC LIMIT 1",
                (run_id,),
            )
            if resume_only
            else await asyncio.to_thread(admission.attempt, self.db, run_id)
        )
        if not attempt_row:
            expired = await asyncio.to_thread(
                self.db.one,
                "SELECT * FROM control.run_attempt WHERE run_id=%s AND status='running' AND deadline_at<=clock_timestamp() ORDER BY attempt_no DESC LIMIT 1",
                (run_id,),
            )
            if expired:
                await asyncio.to_thread(self.timeout_attempt, run_id, expired)
            await asyncio.to_thread(self.settle, run_id)
            return
        manifest = REGISTRY[
            (
                await asyncio.to_thread(
                    self.db.one,
                    "SELECT source_key FROM control.streamline WHERE id=%s",
                    (run["streamline_id"],),
                )
            )["source_key"]
        ]
        remaining = (
            attempt_row["deadline_at"] - datetime.now(timezone.utc)
        ).total_seconds()
        context = trace.set_span_in_context(
            NonRecordingSpan(
                SpanContext(
                    trace_id=int(run["trace_id"], 16),
                    span_id=1,
                    is_remote=False,
                    trace_flags=TraceFlags(1),
                )
            )
        )
        try:
            with trace.get_tracer("mdp.functions").start_as_current_span(
                "function.run", context=context
            ):
                async with asyncio.timeout(max(0, remaining)):
                    workers: set[asyncio.Task[None]] = set()
                    try:
                        while True:
                            run = await asyncio.to_thread(
                                self.db.one,
                                "SELECT * FROM control.run WHERE id=%s",
                                (run_id,),
                            )
                            batch = await asyncio.to_thread(
                                admission.acquire,
                                self.db,
                                run,
                                attempt_row,
                                self.settings.lease_s,
                            )
                            if batch:
                                task = asyncio.create_task(
                                    self.work_batch(run, attempt_row, batch, manifest)
                                )
                                workers.add(task)
                            done = {task for task in workers if task.done()}
                            for task in done:
                                await task
                            workers -= done
                            pending = (
                                await asyncio.to_thread(
                                    self.db.one,
                                    "SELECT count(*) AS n FROM control.batch WHERE run_id=%s AND status IN ('queued','running','draining')",
                                    (run_id,),
                                )
                            )["n"]
                            if not pending and not workers:
                                break
                            await asyncio.sleep(0.02)
                    finally:
                        for task in workers:
                            task.cancel()
                        if workers:
                            await asyncio.gather(*workers, return_exceptions=True)
        except TimeoutError:
            await asyncio.to_thread(self.timeout_attempt, run_id, attempt_row)
        await asyncio.to_thread(self.drain, run["warehouse_id"], run_id)
        if manifest.completion:
            await asyncio.to_thread(self.write_completion, run_id, attempt_row, manifest)
            await asyncio.to_thread(self.drain, run["warehouse_id"], run_id)
        await asyncio.to_thread(self.settle, run_id, attempt_row["id"])

    def write_completion(
        self, run_id: Any, attempt_row: dict[str, Any], manifest: Any, settling: bool = False
    ) -> None:
        """After every batch is terminal, write raw._run_completion naming every registered output dump
        and its row count; a later attempt that registers more dumps writes a newer row. `settling`
        writes it for a run that settle reaches without a live attempt (a crash after the last batch,
        or an expired deadline), fenced on the run's latest attempt."""
        run = self.db.one("SELECT * FROM control.run WHERE id=%s", (run_id,))
        if self.db.one(
            "SELECT count(*) AS n FROM control.batch WHERE run_id=%s AND status NOT IN ('succeeded','partial','failed')",
            (run_id,),
        )["n"]:
            return
        outputs: dict[str, dict[str, int]] = {}
        for dump in self.db.all(
            """SELECT d.id,d.row_count,l.target_table FROM control.dump d
            JOIN control.load l ON l.dump_id=d.id AND l.warehouse_id=%s
            WHERE d.run_id=%s AND d.kind='output' AND l.target_table<>%s ORDER BY d.landed_seq""",
            (run["warehouse_id"], run_id, RUN_COMPLETION),
        ):
            outputs.setdefault(dump["target_table"], {})[str(dump["id"])] = int(dump["row_count"])
        recorded: dict[str, Any] = {}
        for page in self.db.all(
            "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='page_published' ORDER BY at",
            (run_id,),
        ):
            recorded.update(page["attrs"].get("completion") or {})
        last = self.db.one(
            "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='run_completion_written' ORDER BY at DESC LIMIT 1",
            (run_id,),
        )
        if last and last["attrs"]["outputs"] == outputs:
            return
        try:
            self.dumps.publish_completion(
                run,
                attempt_row,
                {"run_id": str(run_id), "source_key": manifest.source_key, "outputs": outputs, "recorded": recorded},
                settling=settling,
            )
        except LeaseLost:
            return

    async def heartbeat(self, batch: dict[str, Any]) -> None:
        while True:
            await asyncio.sleep(self.settings.lease_s / 3)
            await asyncio.to_thread(self.beat, batch)

    def beat(self, batch: dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            fence(conn, batch)
            conn.execute(
                "UPDATE control.batch b SET heartbeat_at=clock_timestamp(),lease_expires_at=least(a.deadline_at,clock_timestamp()+(%s * interval '1 second')) "
                "FROM control.run_attempt a WHERE b.id=%s AND a.id=b.attempt_id AND a.status='running' AND a.deadline_at>clock_timestamp()",
                (self.settings.lease_s, batch["id"]),
            )

    async def work_batch(
        self,
        run: dict[str, Any],
        attempt_row: dict[str, Any],
        batch: dict[str, Any],
        manifest: Any,
    ) -> None:
        checkpoint = batch["cursor_checkpoint"] or {}
        completed = list(checkpoint.get("completed_targets", []))
        cursors = {
            str(r["target_id"]) if r["target_id"] else "": r
            for r in await asyncio.to_thread(
                self.db.all,
                "SELECT * FROM control.cursor WHERE streamline_id=%s AND cursor_key=%s",
                (run["streamline_id"], "backfill:" + str(run["id"]) if run["kind"] == "backfill" else "default"),
            )
        }
        # A tenant cycle's build reads global dumps through its global close; that close's time bounds
        # every observation the build can hold (the observation window's end).
        cycle = await asyncio.to_thread(
            self.db.one,
            "SELECT c.opened_at,c.opened_by_dbt_run_id,c.local_weekday,c.timezone,c.cadence,c.scope,"
            "(SELECT max(g.closed_at) FROM control.cycle g WHERE g.scope='global' AND g.close_no=c.global_close_no) "
            "AS global_closed_at FROM control.cycle c WHERE c.id=%s",
            (run["cycle_id"],),
        )
        tenant = None
        if run.get("tenant_id"):
            tenant = await asyncio.to_thread(
                self.db.one,
                "SELECT id,slug,status::text AS status FROM control.tenant WHERE id=%s",
                (run["tenant_id"],),
            )
        ctx = Ctx(
            manifest,
            {**run, "cycle_opened_at": (cycle or {}).get("opened_at"), "cycle": cycle, "tenant": tenant},
            cursors,
        )
        ctx.batch = batch
        if run["kind"] == "backfill":
            inputs = await asyncio.to_thread(self.db.one,
                "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='backfill_requested' ORDER BY at LIMIT 1", (run["id"],))
            ctx.window = inputs["attrs"].get("window")
        status = "partial" if any(t.startswith(("stale_target:", "rejected:")) for t in completed) else "succeeded"
        published_completed = list(completed)

        async def flush(complete: bool = False) -> None:
            nonlocal published_completed
            has_page = bool(
                ctx.outputs or ctx.rejected or ctx.exclusions or ctx.pending_cursors or ctx.observed_count or ctx.alerts
            )
            if has_page or complete or completed != published_completed:
                await asyncio.to_thread(check_owner)
                draining = await asyncio.to_thread(
                    self.dumps.publish, ctx, run, batch, completed, complete
                )
                published_completed = list(completed)
                await asyncio.to_thread(self.drain, run["warehouse_id"], run["id"])
                if draining:
                    raise ServiceError(
                        "superseded",
                        "In-flight page landed; superseded cycle is draining",
                    )
            await asyncio.to_thread(check_page)

        def check_owner() -> None:
            with self.db.transaction() as conn:
                fence(conn, batch)

        def check_page() -> None:
            with self.db.transaction() as conn:
                locked = fence(conn, batch)
                if locked["status"] == "draining":
                    raise ServiceError(
                        "superseded", "No additional pages after supersession"
                    )
                alive = conn.execute(
                    "SELECT deadline_at>now() AS alive FROM control.run_attempt WHERE id=%s",
                    (attempt_row["id"],),
                ).fetchone()["alive"]
                if not alive:
                    raise TimeoutError("Attempt deadline expired")

        transport = self.transport
        if transport is None and self.settings.fixture:
            # Weekly source keys share their daily twin's code and fixtures.
            source_dir = (
                "billboard"
                if manifest.source_key == "billboard_hot100"
                else manifest.source_key.removesuffix("_weekly")
            )
            transport = FixtureTransport(
                sorted(
                    (PACKAGE / "sources" / source_dir / "fixtures").glob(
                        f"{self.settings.fixture_scenario}*.jsonl"
                    )
                )
            )
        heartbeat = asyncio.create_task(self.heartbeat(batch))
        try:
            if checkpoint.get("complete"):
                await asyncio.to_thread(self.drain, run["warehouse_id"], run["id"])
            elif manifest.kind == "export":
                scope = run["scope"]
                cycle = await asyncio.to_thread(
                    self.db.one,
                    "SELECT cadence FROM control.cycle WHERE id=%s",
                    (run["cycle_id"],),
                )
                # The export freezes exactly the kinds it was admitted with: the payload's
                # mdp_export_kinds() list, frozen in resolved_config. In a tenant export,
                # `global:<kind>` freezes the global sets its tenant functions read.
                if run["resolved_config"] is None:
                    from mdp_functions.exporter import declared_export_kinds

                    kinds = declared_export_kinds(cycle["cadence"], scope)
                else:
                    kinds = run["resolved_config"]["target_kinds"]
                read_kinds = {k for k in kinds if not k.startswith("global:")}
                sets = await asyncio.to_thread(
                    self.db.all,
                    "SELECT * FROM control.target_set WHERE tenant_id IS NOT DISTINCT FROM %s::uuid AND kind=ANY(%s)",
                    (
                        scope.removeprefix("tenant:")
                        if scope.startswith("tenant:")
                        else None,
                        list(read_kinds),
                    ),
                )
                if scope.startswith("tenant:"):
                    global_kinds = {k.removeprefix("global:") for k in kinds if k.startswith("global:")}
                    sets += await asyncio.to_thread(
                        self.db.all,
                        "SELECT * FROM control.target_set WHERE tenant_id IS NULL AND kind=ANY(%s)",
                        (list(global_kinds),),
                    )
                for item in sets:
                    await asyncio.to_thread(
                        export_targets,
                        self.db,
                        await asyncio.to_thread(
                            self.warehouse_for, run["warehouse_id"]
                        ),
                        run["cycle_id"],
                        item["kind"],
                        item["tenant_id"],
                    )
                await flush(True)
            elif manifest.kind == "close":
                await asyncio.to_thread(self.close_cycle, run)
                await flush(True)
            elif manifest.layer in {"silver", "gold"} or (
                manifest.layer == "universal" and manifest.reads
            ):
                from mdp_functions.derived import execute_derived

                try:
                    if manifest.external:
                        async with TracedClient(
                            ctx,
                            self.db,
                            run,
                            flush,
                            self.settings.http_backoff_s,
                            transport=transport,
                            settings=self.settings,
                            estimate_cents=self.settings.vendor_estimates.get(
                                manifest.source_key, 0
                            ),
                        ) as client:
                            ctx.http = client
                            await execute_derived(self, ctx, run, manifest, flush)
                    else:
                        await execute_derived(self, ctx, run, manifest, flush)
                except (
                    ServiceError,
                    TimeoutError,
                    asyncio.CancelledError,
                    psycopg.Error,
                    PoolTimeout,
                    BotoCoreError,
                    ClientError,
                    OSError,
                ):
                    raise
                except Exception as exc:
                    # Any other error a derived body raises (a promoter's control-api call among them)
                    # ends the run with a class, never the worker task: the run is not left running.
                    raise ServiceError(
                        "function_failed",
                        f"Function raised {type(exc).__name__}: {exc}",
                    ) from exc
            else:
                async with TracedClient(
                    ctx,
                    self.db,
                    run,
                    flush,
                    self.settings.http_backoff_s,
                    transport=transport,
                    settings=self.settings,
                    estimate_cents=self.settings.vendor_estimates.get(
                        manifest.source_key, 0
                    ),
                ) as client:
                    ctx.http = client
                    targets = (
                        [
                            Target.from_export(r)
                            for r in await asyncio.to_thread(
                                self.db.all,
                                "SELECT * FROM control.target_export_member WHERE revision_id=%s AND target_id=ANY(%s::uuid[]) ORDER BY target_id",
                                (run["revision_id"], batch["target_ids"]),
                            )
                        ]
                        if manifest.targets
                        else [None]
                    )
                    for target in targets:
                        identity = str(target["id"]) if target else ""
                        if identity in completed:
                            continue
                        ctx.target_id = identity or None
                        ctx.target = target
                        request_before = ctx.request_id
                        try:
                            with source_network(ctx):
                                if manifest.targets:
                                    result = manifest.function(ctx, [target])
                                elif (
                                    len(inspect.signature(manifest.function).parameters) > 1
                                ):
                                    result = manifest.function(ctx, [])
                                else:
                                    result = manifest.function(ctx)
                                if inspect.isasyncgen(result):
                                    async for row in result:
                                        ctx.yield_row(row)
                                elif inspect.isawaitable(result):
                                    await result
                                else:
                                    raise ServiceError(
                                        "invalid_function", "Function must be async"
                                    )
                        except ServiceError as exc:
                            if exc.error_class not in {
                                "vendor_4xx",
                                "vendor_retryable",
                                "scrape_blocked",
                                "envelope_mismatch",
                                "stale_target",
                                "proxy_quota_exhausted",
                            }:
                                raise
                            await asyncio.to_thread(
                                self.record_error, run, batch, exc, identity or None
                            )
                            status = "partial"
                            # A 429, or a transport failure that outlived its retries, is the
                            # host's: stop this batch at its first unfinished target, which
                            # stays resumable. Earlier uploads stay durable.
                            stop = exc.error_class == "vendor_retryable" and exc.vendor_status in (None, 429)
                            if exc.error_class in {"vendor_4xx", "vendor_retryable"} and not stop:
                                # Any other 4xx, or a 5xx after retries, is this target's own:
                                # reject it and go on. A 5xx target stays resumable.
                                ctx.observed(1)
                                ctx.reject({"target_id": identity}, reason=f"{exc.error_class}: {exc.message}")
                            if exc.error_class == "stale_target":
                                # Terminal for this immutable work key; preserve partial on retry.
                                completed.extend([identity, "stale_target:" + identity])
                            elif exc.error_class == "vendor_4xx":
                                completed.extend([identity, "rejected:" + identity])
                            await flush()
                            if stop:
                                break
                            continue
                        except (
                            TimeoutError,
                            asyncio.CancelledError,
                            psycopg.Error,
                            PoolTimeout,
                            BotoCoreError,
                            ClientError,
                            OSError,
                        ):
                            raise
                        except Exception as exc:
                            raise ServiceError(
                                "function_failed",
                                f"Function raised {type(exc).__name__}: {exc}",
                            ) from exc
                        completed.append(identity)
                        if (manifest.targets and ctx.request_id == request_before
                                and not (ctx.observed_count or ctx.outputs or ctx.rejected or ctx.exclusions or ctx.pending_cursors)):
                            completed.append("skipped:" + identity)
                        await flush()
                    await flush(status == "succeeded")
        except LeaseLost:
            return
        except ServiceError as exc:
            if exc.error_class != "superseded":
                status = (
                    "partial"
                    if exc.error_class
                    in {"cost_cap_hit", "llm_budget_exceeded", "litellm_unavailable"}
                    else "failed"
                )
                payload_ref = await asyncio.to_thread(
                    self.dumps.preserve_failure, ctx, run, batch, exc
                )
                await asyncio.to_thread(
                    self.record_error, run, batch, exc, ctx.target_id, payload_ref
                )
        except asyncio.CancelledError:
            # Leave ownership and checkpoint intact; only expiry/reaper may release the permit.
            raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, LeaseLost):
                await heartbeat
        await asyncio.to_thread(self.complete_batch, batch, status)

    def close_cycle(self, run: dict[str, Any]) -> None:
        Cycles(self.db, self.warehouse_for(run["warehouse_id"]), self.settings).close(
            run["cycle_id"]
        )

    def complete_batch(self, batch: dict[str, Any], status: str) -> None:
        with self.db.transaction() as conn:
            fence(conn, batch)
            conn.execute(
                "UPDATE control.batch SET status=%s,lease_expires_at=NULL WHERE id=%s",
                (status, batch["id"]),
            )

    def record_error(
        self,
        run: dict[str, Any],
        batch: dict[str, Any],
        exc: ServiceError,
        target: Any = None,
        payload_ref: str | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            fence(conn, batch)
            conn.execute(
                "INSERT INTO control.dead_letter(run_id,streamline_id,target_id,reason,payload_ref) VALUES (%s,%s,%s,%s,%s)",
                (
                    run["id"],
                    run["streamline_id"],
                    target,
                    f"{exc.error_class}: {exc.message}",
                    payload_ref,
                ),
            )
            conn.execute(
                "UPDATE control.run SET error_class=%s,error_message=%s WHERE id=%s",
                (exc.error_class, exc.message, run["id"]),
            )
            if exc.error_class == "stale_target" and target:
                from mdp_functions.control_db import open_alerts

                open_alerts(conn, run["id"], [{"kind": "stale_target", "message": "Target returned 404 or 410; consecutive cycles park it",
                    "target_id": str(target), "once": True, "attrs": {"reason": "stale_target"}}])
            else:
                alert(conn, run["id"], exc.error_class, str(run["id"]))

    def complete(self, run: dict[str, Any]) -> None:
        """A completion=True run gets its raw._run_completion row once every batch is terminal, also
        when recovery settles it after its attempt is gone; the write is idempotent."""
        streamline = self.db.one("SELECT source_key FROM control.streamline WHERE id=%s", (run["streamline_id"],))
        manifest = REGISTRY.get(streamline["source_key"]) if streamline else None
        if not manifest or not manifest.completion:
            return
        batches = self.db.one(
            "SELECT count(*) AS n,count(*) FILTER (WHERE status NOT IN ('succeeded','partial','failed')) AS open "
            "FROM control.batch WHERE run_id=%s",
            (run["id"],),
        )
        attempt = self.db.one(
            "SELECT * FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1", (run["id"],)
        )
        if not batches["n"] or batches["open"] or not attempt:
            return
        self.write_completion(run["id"], attempt, manifest, settling=True)
        # The row only describes dumps registered before every batch ended, so it lands past the
        # attempt's deadline too.
        loader = self.landing(run["warehouse_id"])
        for load in self.db.all(
            "SELECT l.id FROM control.load l JOIN control.dump d ON d.id=l.dump_id "
            "WHERE d.run_id=%s AND l.warehouse_id=%s AND l.target_table=%s AND l.status='pending'",
            (run["id"], run["warehouse_id"], RUN_COMPLETION),
        ):
            loader.process(load["id"])

    def settle(self, run_id: Any, attempt_id: Any = None) -> None:
        # A load acknowledged after this snapshot must remain eligible for another pass.
        settlement_started = self.db.one("SELECT clock_timestamp() AS at")["at"]
        run = self.db.one("SELECT * FROM control.run WHERE id=%s", (run_id,))
        self.complete(run)
        committed = self.committed(run)
        with self.db.transaction() as conn:
            run = conn.execute(
                "SELECT * FROM control.run WHERE id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if attempt_id:
                latest = conn.execute(
                    "SELECT id FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
                if not latest or latest["id"] != attempt_id:
                    return
            batches = conn.execute(
                "SELECT * FROM control.batch WHERE run_id=%s", (run_id,)
            ).fetchall()
            pending = conn.execute(
                "SELECT count(*) AS n FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s AND l.warehouse_id=%s AND l.status NOT IN ('loaded','rejected')",
                (run_id, run['warehouse_id']),
            ).fetchone()["n"]
            evidence = {d["id"] for d in conn.execute(COMPLETION_DUMPS, (run_id,))}
            total = sum(
                committed.get(str(d["id"]), 0)
                for d in conn.execute(
                    "SELECT id FROM control.dump WHERE run_id=%s AND kind='output'",
                    (run_id,),
                )
                if d["id"] not in evidence
            )
            conn.execute(
                "UPDATE control.run SET rows_written=%s WHERE id=%s", (total, run_id)
            )
            if pending or any(
                b["status"] not in admission.BATCH_TERMINAL for b in batches
            ):
                return
            counts = conn.execute(
                "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='page_published'",
                (run_id,),
            ).fetchall()
            observed = sum(int(c["attrs"]["observed"]) for c in counts)
            yielded = sum(int(c["attrs"]["yielded"]) for c in counts)
            rejected = rejection_count(counts)
            exclusions = exclusion_counts(counts)
            excluded = sum(exclusions.values())
            rejected_output_rows = conn.execute(
                "SELECT coalesce(sum(d.row_count),0) AS n FROM control.dump d JOIN control.load l ON l.dump_id=d.id WHERE d.run_id=%s AND d.kind='output' AND l.warehouse_id=%s AND l.status='rejected'",
                (run_id, run["warehouse_id"]),
            ).fetchone()["n"]
            conn.execute(
                "UPDATE control.run SET rows_rejected=%s WHERE id=%s",
                (rejected + rejected_output_rows, run_id),
            )
            failed_load = conn.execute(
                "SELECT count(*) AS n FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s AND l.warehouse_id=%s AND l.status='rejected'",
                (run_id, run['warehouse_id']),
            ).fetchone()["n"]
            cycle = conn.execute(
                "SELECT status FROM control.cycle WHERE id=%s", (run["cycle_id"],)
            ).fetchone()
            # A time-budget end is partial coverage by design, not an incident.
            budget_ended = run["error_class"] == "time_budget"
            # A gold run whose vendor, mirror or function rejected some inputs ended partial.
            inputs_rejected = run["error_class"] in {"vendor_retryable", "vendor_4xx", "input_rejected", "surface_drift"}
            # A playlist header is output, but cannot prove membership when most items are excluded.
            incomplete_membership = conn.execute(
                "SELECT 1 FROM control.run_event WHERE run_id=%s AND event_type='envelope_mismatch' "
                "AND attrs->>'path' IN ('empty','exclusions') LIMIT 1", (run_id,),
            ).fetchone()
            partial = bool(
                budget_ended
                or inputs_rejected
                or incomplete_membership
                or failed_load
                or any(b["status"] in {"failed", "partial"} for b in batches)
                or observed != yielded + rejected + excluded
            )
            status = "partial" if partial else "succeeded"
            # Balanced accounting can still contain rejected records.
            coverage = "partial" if partial or rejected else ("empty" if not batches else "full")
            error, message = run["error_class"], run["error_message"]
            if observed != yielded + rejected + excluded:
                error = "accounting_mismatch"
                message = (f"observed={observed}, yielded={yielded}, rejected={rejected}, excluded={excluded}. "
                           "Open /runbooks/accounting-mismatch to check the page counts.")
                alert(conn, run_id, error, str(run_id))
            if error in {
                "undeclared_write",
                "undeclared_exclusion",
                "schema_breaking",
                "not_implemented",
                "function_failed",
                "egress_blocked",
                "undeclared_read",
                "input_identity_missing",
                "invoke_timeout",
                "deadline_expired",
                "control_api_unavailable",
            }:
                status, coverage = "failed", "partial"
            row_result = row_coverage(total, rejected + rejected_output_rows,
                                      (run.get("resolved_config") or {}).get("min_row_coverage", MIN_ROW_COVERAGE), exclusions)
            if not row_result["row_coverage_met"]:
                status, coverage = "failed", "partial"
                error = error or "partial_coverage"
                detail = "Row acceptance is below its floor. Open /runbooks/partial-coverage and inspect the rejected rows."
                message = f"{message}; {detail}" if message else detail
            for target_id in zero_yield_targets(batches):
                if not conn.execute(
                    "SELECT 1 FROM control.run_event WHERE run_id=%s AND event_type='target_zero_yield' "
                    "AND attrs->>'target_id'=%s", (run_id, target_id),
                ).fetchone():
                    open_alerts(conn, run_id, [{
                        "kind": "target_zero_yield", "target_id": target_id, "once": True,
                        "message": f"Target {target_id} completed with no output after excluding or rejecting rows. "
                                   "Inspect its run receipts and exclusion reasons at /runbooks/target-zero-yield.",
                        "attrs": {},
                    }])
            target_result = target_coverage(run, batches)
            if target_result and target_result["targets_succeeded"] < target_result["targets_total"] and status == "succeeded":
                status, coverage = "partial", "partial"
            if target_result and (failed_load or observed != yielded + rejected + excluded):
                status, coverage = "failed", "partial"
            if target_result.get("target_coverage_met") is False:
                status, coverage = "failed", "partial"
                error = error or "partial_coverage"
                detail = (f"Target coverage {target_result['targets_succeeded']}/{target_result['targets_total']} "
                          f"is below the floor {target_result['min_target_coverage']:.0%}")
                message = f"{message}; {detail}" if message else detail
            if (not target_result and total == 0 and (observed > sum(exclusions.values()) or rejected + rejected_output_rows > 0)
                    and conn.execute("SELECT cardinality(writes)>0 AS expected FROM control.streamline WHERE id=%s",
                                     (run["streamline_id"],)).fetchone()["expected"]):
                status, coverage = "failed", "partial"
                error = error or "partial_coverage"
                message = message or "Expected work produced no accepted records"
            floor_missed = not row_result["row_coverage_met"] or target_result.get("target_coverage_met") is False
            should_alert = not budget_ended and (floor_missed or (coverage == "partial" and repeated_partial(conn, run)))
            if should_alert:
                alert(conn, run_id, "partial_coverage", str(run_id))
            if cycle["status"] == "superseded":
                status = "superseded"
            conn.execute(
                "UPDATE control.run SET status=%s,coverage=%s,error_class=%s,error_message=%s,"
                "settled_at=%s,updated_at=CASE WHEN %s THEN now() ELSE updated_at END WHERE id=%s",
                (status, coverage, error, message, settlement_started,
                 (run["status"], run["coverage"], run["error_class"], run["rows_written"], run["rows_rejected"])
                 != (status, coverage, error, total, rejected + rejected_output_rows), run_id),
            )
            conn.execute(
                "UPDATE control.run_attempt SET status=%s,ended_at=now() WHERE run_id=%s AND status='running'",
                (status, run_id),
            )
            conn.execute("SELECT control.resolve_recovered_alerts(%s,NULL)", (run_id,))
        if run["kind"] == "backfill" and status in {"succeeded", "partial", "failed"}:
            self.cycles.close(run["cycle_id"])
        budget.settle(self.db, run_id)

    def committed(self, run: dict[str, Any]) -> dict[str, int]:
        warehouse = self.warehouse_for(run["warehouse_id"])
        # Only this run's receipts; reading every receipt would grow with all history.
        dumps = [d["id"] for d in self.db.all("SELECT id FROM control.dump WHERE run_id=%s", (run["id"],))]
        try:
            return {
                str(r["dump_id"]): r["rows"]
                for r in warehouse.receipts(dumps)
                if r["committed_at"] is not None
                and str(r["warehouse_id"]) == str(run["warehouse_id"])
            }
        except (OSError, psycopg.Error, PoolTimeout, duckdb.Error) as exc:
            raise ServiceError(
                "warehouse_unavailable", "Committed receipts are unavailable", 503
            ) from exc

    def receipts(self, run_id: Any) -> dict[str, Any]:
        run = self.db.one("SELECT * FROM control.run WHERE id=%s", (run_id,))
        if not run:
            raise ServiceError("unknown_run", "Run does not exist", 404)
        batches = self.db.all(
            "SELECT * FROM control.batch WHERE run_id=%s ORDER BY index", (run_id,)
        )
        committed = self.committed(run)
        streamline = self.db.one(
            "SELECT allow_partial,source_key FROM control.streamline WHERE id=%s",
            (run["streamline_id"],),
        )
        allow_partial = streamline["allow_partial"]
        manifest = REGISTRY.get(streamline["source_key"])
        # The invoke UDF lets a non-blocking source's own failure through as receipt rows.
        blocks_cycle = manifest.blocks_cycle if manifest else True
        run["repairs_pending"] = self.db.one(
            "SELECT count(*) AS n FROM control.load l JOIN control.dump d ON d.id=l.dump_id WHERE d.run_id=%s AND (l.repair_requested OR (l.op='repair' AND l.status IN ('pending','claimed')))",
            (run_id,),
        )["n"]
        if run["error_class"] == "paused":
            run["status"], run["coverage"] = "paused", "empty"
        if run["status"] in admission.TERMINAL and self.db.one(UNMIRRORED_DERIVED, (run_id,))["n"]:
            # A same-cycle consumer must see every derived dump before the invoke returns.
            with suppress(ServiceError):
                mirror_derived(self.db, self.warehouse_for(run["warehouse_id"]), run_id)
            if self.db.one(UNMIRRORED_DERIVED, (run_id,))["n"]:
                run["status"] = "running"
        evidence = {d["id"] for d in self.db.all(COMPLETION_DUMPS, (run_id,))}
        run["rows_written"] = sum(
            committed.get(str(d["id"]), 0)
            for d in self.db.all("SELECT id FROM control.dump WHERE run_id=%s AND kind='output'", (run_id,))
            if d["id"] not in evidence
        )
        run["rows_rejected"] = (
            rejection_count(self.db.all(
                "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='page_published'",
                (run_id,),
            ))
            + self.db.one(
                "SELECT coalesce(sum(d.row_count),0) AS n FROM control.dump d JOIN control.load l ON l.dump_id=d.id WHERE d.run_id=%s AND d.kind='output' AND l.warehouse_id=%s AND l.status='rejected'",
                (run_id, run["warehouse_id"]),
            )["n"]
        )
        receipts = []
        for batch in batches or [None]:
            dumps = (
                self.db.all(
                    "SELECT id,kind,row_count,landed_seq FROM control.dump WHERE id=ANY(%s::uuid[]) ORDER BY landed_seq",
                    (batch["dump_ids"],),
                )
                if batch
                else []
            )
            outputs = [d for d in dumps if d["kind"] == "output" and d["id"] not in evidence]
            primary = outputs[0] if outputs else None
            counts = self.db.all(
                "SELECT attrs FROM control.run_event WHERE run_id=%s AND event_type='page_published' AND attrs->>'batch_id'=%s",
                (run_id, str(batch["id"]) if batch else ""),
            )
            rejected_count = rejection_count(counts)
            exclusions = exclusion_counts(counts)
            rejected_count += self.db.one(
                "SELECT coalesce(sum(d.row_count),0) AS n FROM control.dump d JOIN control.load l ON l.dump_id=d.id WHERE d.id=ANY(%s::uuid[]) AND d.kind='output' AND l.warehouse_id=%s AND l.status='rejected'",
                (batch["dump_ids"] if batch else [], run["warehouse_id"]),
            )["n"]

            written_count = sum(committed.get(str(d["id"]), 0) for d in outputs)
            receipts.append(
                {
                    "run_id": str(run["id"]),
                    "status": run["status"],
                    "allow_partial": allow_partial,
                    "blocks_cycle": blocks_cycle,
                    **target_coverage(run, batches),
                    "coverage": run["coverage"],
                    **row_coverage(written_count, rejected_count,
                                   (run.get("resolved_config") or {}).get("min_row_coverage", MIN_ROW_COVERAGE), exclusions),
                    "rows_written": str(written_count),
                    "rows_rejected": str(rejected_count),
                    "dump_id": str(primary["id"]) if primary else None,
                    "landed_seq": str(primary["landed_seq"]) if primary else None,
                    "trace_url": trace_url(self.settings, run["trace_id"]),
                    "error_class": run["error_class"],
                    "message": run["error_message"] or "",
                    "loads": self.db.all(
                        "SELECT dump_id,status,generation,rows_inserted FROM control.load WHERE dump_id=ANY(%s::uuid[]) AND warehouse_id=%s",
                        (batch["dump_ids"] if batch else [], run["warehouse_id"]),
                    ),
                }
            )
        return {
            "run": run,
            "receipts": receipts,
            "repairs_pending": run["repairs_pending"],
        }

    async def cancel(self, run_id: Any) -> None:
        task = self.tasks.get(str(run_id))
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await asyncio.to_thread(self.cancel_current, run_id)
        run = await asyncio.to_thread(
            self.db.one, "SELECT * FROM control.run WHERE id=%s", (run_id,)
        )
        await asyncio.to_thread(self.drain, run["warehouse_id"], run_id)
        await asyncio.to_thread(self.settle, run_id)

    def cancel_current(self, run_id: Any) -> None:
        with self.db.transaction() as conn:
            conn.execute("SELECT id FROM control.run WHERE id=%s FOR UPDATE", (run_id,))
            rows = conn.execute(
                "SELECT * FROM control.batch WHERE run_id=%s FOR UPDATE", (run_id,)
            ).fetchall()
            for row in rows:
                if row["status"] not in admission.BATCH_TERMINAL:
                    conn.execute(
                        "UPDATE control.batch SET status='failed',lease_expires_at=NULL WHERE id=%s",
                        (row["id"],),
                    )
            event(conn, run_id, "cancel_requested", "Run cancelled by request")

    async def close(self) -> None:
        tasks = [t for t in self.tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(self.db.close)
