"""Authenticated admission and polling API; vendor work never blocks POST /invoke."""

import asyncio
import contextvars
import hmac
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from functools import partial
from typing import Any, Literal
from uuid import UUID

import duckdb
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field, model_validator

from mdp_functions import admission
from mdp_functions.code_fingerprint import fingerprint
from mdp_functions.errors import ServiceError, error_hint
from mdp_functions.fixture_control import (
    PLANS,
    ControlledTransport,
    FixturePlan,
    FixtureRelease,
)
from mdp_functions.fixture_control import hold as hold_barrier
from mdp_functions.fixture_control import release as release_barrier
from mdp_functions.preview import OutputPreview, output_preview
from mdp_functions.recovery import Recovery
from mdp_functions.registry import REGISTRY
from mdp_functions.runs import Runtime
from mdp_functions.service_models import (
    CostsyncResponse,
    ReferenceProbeResponse,
    RegistryResponse,
    RowCount,
    RunRow,
)
from mdp_functions.settings import Settings
from mdp_functions.telemetry import configure


def wire(value: Any) -> Any:
    encoded = jsonable_encoder(value)
    bigint_fields = {
        "rows_written",
        "rows_rejected",
        "landed_seq",
        "cost_cents",
        "rows_inserted",
        "row_count",
        "reserved_cents",
        "consumed_cents",
        "version",
        "reset_generation",
    }
    if isinstance(encoded, list):
        return [wire(item) for item in encoded]
    if isinstance(encoded, dict):
        return {
            key: str(item)
            if key in bigint_fields and isinstance(item, int)
            else wire(item)
            for key, item in encoded.items()
        }
    return encoded


class Invoke(BaseModel):
    source_key: str
    cadence: str | None = None
    target_set: str | None = None
    dbt_run_id: str | None = None
    invocation_id: str | None = None
    model: str | None = None
    input_relation: str | None = None
    config_version: str | None = None
    manual: bool = False
    scope: str | None = None
    # An export's frozen kinds, from the generated mdp_export_kinds().
    target_kinds: list[str] | None = None
    # Seconds the caller still waits; a new attempt ends inside it (admission.attempt_seconds).
    deadline_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class ShowcaseInventoryFailed(BaseModel):
    warehouse: UUID | None = None


class ShowcaseInventoryAlert(BaseModel):
    status: Literal["recorded"]
    next_step: str
    warehouse: UUID


class RunnerFailed(BaseModel):
    job_id: str | None = None
    dbt_run_id: str
    model: str = "runner"
    error: str


class CadenceFailed(BaseModel):
    """A Core gate stopped a period after its second failed scheduled cycle (core_gate.FAILED_CAP)."""

    job_id: str
    period_start: datetime


class ReferenceIncomplete(BaseModel):
    """A dbt run whose reference staging read a MusicBrainz generation retention deleted."""

    run_id: str


class Binding(BaseModel):
    runner: Literal["cloud", "core"] = "cloud"
    cadence: str
    scope: str
    dbt_run_id: str
    reason_category: str
    job_id: str
    cycle_id: str | None = None
    # The bind hook's generated mdp_global_inputs() list. Only a pre-stamp hook sends none; the
    # service refuses it with runner_outdated unless it is Run Now (lock_runner).
    global_inputs: list[str] | None = None
    # Run Now under Core binds only while it can take the runner's core:<cadence>:<scope> lock.
    lock_runner: bool = False


class Repair(BaseModel):
    dump_id: UUID
    warehouse_id: UUID | None = None
    target_table: str


class BackfillWindow(BaseModel):
    start: datetime = Field(alias="from")
    end: datetime = Field(alias="to")

    @model_validator(mode="after")
    def ordered(self) -> "BackfillWindow":
        if not self.start.tzinfo or not self.end.tzinfo or self.start >= self.end:
            raise ValueError("Window requires timezone-aware from < to")
        return self


class Backfill(BaseModel):
    source_key: str
    window: BackfillWindow | None = None
    target_ids: list[UUID] | None = Field(default=None, min_length=1)
    cycle_id: UUID | None = None
    scope: str | None = None

    @model_validator(mode="after")
    def selector(self) -> "Backfill":
        if sum(x is not None for x in (self.window, self.target_ids, self.cycle_id)) != 1:
            raise ValueError("Supply exactly one of window, target_ids, or cycle_id")
        return self


class Migrate(BaseModel):
    warehouse_id: UUID
    source_keys: list[str] | None = Field(default=None, min_length=1)
    since: datetime | None = None


class MigrationResponse(BaseModel):
    warehouse_id: UUID
    selected: int
    queued: int
    loaded: int
    rejected: int
    pending: int


class ErrorResponse(BaseModel):
    error_class: str
    message: str
    next_step: str
    runbook: str | None = None


class DbtWebhook(BaseModel):
    event_id: str
    run_id: str
    job_id: str
    status: Literal["running", "succeeded", "failed"]
    message: str = ""


class AdmissionResponse(BaseModel):
    run_id: UUID
    status: str


class LoadResponse(BaseModel):
    dump_id: UUID
    status: str
    generation: int
    rows_inserted: str | None = None


class ReceiptResponse(BaseModel):
    error_class: str | None = None
    row_coverage: Literal["full", "partial", "empty"] | None = None
    row_acceptance: float | None = None
    row_rejection_share: float | None = None
    rows_excluded: str | None = None
    row_exclusions: dict[str, int] | None = None
    min_row_coverage: float | None = None
    row_coverage_met: bool | None = None
    min_target_coverage: float | None = None
    target_coverage: float | None = None
    targets_succeeded: int | None = None
    targets_total: int | None = None
    target_coverage_met: bool | None = None
    allow_partial: bool
    blocks_cycle: bool = True
    run_id: UUID
    status: str
    coverage: str | None
    rows_written: str
    rows_rejected: str
    dump_id: UUID | None
    landed_seq: str | None
    trace_url: str
    message: str
    loads: list[LoadResponse]


class RunResponse(RunRow):
    repairs_pending: int


class PollResponse(BaseModel):
    run: RunResponse
    receipts: list[ReceiptResponse]
    repairs_pending: int


class FunctionResponse(BaseModel):
    manifest: dict[str, Any]
    last_runs: list[RunRow]
    receipts: list[list[ReceiptResponse]]
    output_preview: list[OutputPreview]
    rejected_sample: list[dict[str, Any]]
    row_counts: list[RowCount]
    fingerprint_history: list[dict[str, Any]]
    log_url: str



# Threads for run polls, admission and health checks. Page landings, loads and ledger writes use
# asyncio's default executor, so a queue of slow landings never delays a poll or an admission.
CONTROL_THREADS = 4


async def control_thread(request: Request, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run blocking control-plane work on the API's own small thread pool."""
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        request.app.state.control_pool, partial(context.run, fn, *args, **kwargs)
    )


def create_app(
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    recover: bool = True,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        rt = runtime or Runtime(settings)
        app.state.runtime = rt
        await asyncio.to_thread(rt.initialize)
        recovery_task = asyncio.create_task(Recovery(rt).loop()) if recover else None
        yield
        if recovery_task:
            recovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await recovery_task
        if runtime is None:
            await rt.close()
        app.state.control_pool.shutdown(wait=False)

    app = FastAPI(
        title="Music Data Platform Functions",
        version="0.0.1",
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        lifespan=lifespan,
        dependencies=[Depends(HTTPBearer(auto_error=False))],
        responses={
            code: {"model": ErrorResponse} for code in (400, 401, 404, 409, 422, 503)
        },
    )
    if runtime:
        app.state.runtime = runtime
    app.state.control_pool = ThreadPoolExecutor(CONTROL_THREADS, thread_name_prefix="mdp-control")

    if os.environ.get("MDP_FIXTURE_MODE") == "1":

        @app.post("/v1/_fixture/plan")
        async def fixture_plan(body: FixturePlan, request: Request) -> Any:
            rt = request.app.state.runtime
            if body.source_key not in {
                "fixture_accounts",
                "lifecycle_daily_probe",
                "lifecycle_tenant_probe",
            }:
                raise ServiceError("unknown_fixture", "Unsupported fixture source", 422)
            if not isinstance(rt.transport, ControlledTransport):
                rt.transport = ControlledTransport(rt.db)
            if body.hold:
                hold_barrier(body.source_key)
            PLANS[body.source_key] = body
            rt.transport.started[body.source_key] = 0
            return body.model_dump()

        @app.post("/v1/_fixture/release")
        async def fixture_release(body: FixtureRelease) -> Any:
            return {"source_key": body.source_key, "released": release_barrier(body.source_key)}

        @app.get("/v1/_fixture/state")
        async def fixture_state(request: Request) -> Any:
            transport = request.app.state.runtime.transport
            return {
                "started": transport.started
                if isinstance(transport, ControlledTransport)
                else {}
            }

    @app.middleware("http")
    async def auth(request: Request, call_next: Any) -> Any:
        if request.url.path == "/v1/health":
            return await call_next(request)
        supplied = request.headers.get("authorization", "")
        if not settings.service_token or not hmac.compare_digest(
            supplied, "Bearer " + settings.service_token
        ):
            return JSONResponse(
                {"error_class": "unauthorized", "message": "Bearer token required", "next_step": error_hint("unauthorized")["next_step"]},
                status_code=401,
            )
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            {"error_class": exc.error_class, "message": exc.message, "next_step": exc.next_step, "runbook": exc.runbook},
            status_code=exc.status_code,
        )

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error) -> JSONResponse:
        import structlog

        structlog.get_logger().error("database_unavailable", error=type(exc).__name__)
        return JSONResponse(
            {
                "error_class": getattr(request.state, "db_subsystem", "control_db")
                + "_unavailable",
                "message": "Database operation unavailable; retry polling",
                "next_step": error_hint("control_db_unavailable")["next_step"],
            },
            status_code=503,
        )

    @app.exception_handler(OSError)
    @app.exception_handler(BotoCoreError)
    @app.exception_handler(ClientError)
    async def object_error(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            {
                "error_class": "object_store_unavailable",
                "message": "Object storage operation unavailable; retry polling",
                "next_step": error_hint("object_store_unavailable")["next_step"],
            },
            status_code=503,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            {"error_class": "invalid_request", "message": str(exc), "next_step": error_hint("invalid_request")["next_step"]}, status_code=422
        )

    @app.post("/v1/invoke", status_code=202, response_model=AdmissionResponse)
    async def invoke(
        body: Invoke, request: Request, idempotency_key: str | None = Header(None)
    ) -> dict[str, Any]:
        rt = request.app.state.runtime
        if not body.dbt_run_id:
            raise ServiceError(
                "scope_mismatch", "Invocation requires a validated dbt run binding"
            )
        run = await control_thread(
            request, rt.admit, **body.model_dump(), key=idempotency_key
        )
        # Publish the new attempt before a client can poll the previous failure.
        active = await control_thread(
            request, admission.attempt, rt.db, run["id"], body.dbt_run_id, body.deadline_s
        )
        rt.start(run["id"])
        return {
            "run_id": str(run["id"]),
            "status": "running" if active else run["status"],
        }

    @app.get("/v1/runs/{run_id}", response_model=PollResponse)
    async def get_run(run_id: UUID, request: Request) -> Any:
        return wire(await control_thread(request, request.app.state.runtime.receipts, run_id))

    @app.get("/v1/runs", response_model=list[RunRow])
    def get_runs(
        request: Request,
        source_key: str | None = None,
        status: str | None = None,
        cycle_id: UUID | None = None,
        limit: int = 100,
    ) -> Any:
        return wire(
            request.app.state.runtime.db.all(
                """SELECT r.* FROM control.run r
            LEFT JOIN control.streamline s ON s.id=r.streamline_id
            WHERE (%s::text IS NULL OR s.source_key=%s) AND (%s::text IS NULL OR r.status::text=%s)
            AND (%s::uuid IS NULL OR r.cycle_id=%s) ORDER BY r.created_at DESC LIMIT %s""",
                (
                    source_key,
                    source_key,
                    status,
                    status,
                    cycle_id,
                    cycle_id,
                    min(max(limit, 1), 1000),
                ),
            )
        )

    @app.post("/v1/runs/{run_id}/cancel", response_model=PollResponse)
    async def cancel(run_id: UUID, request: Request) -> Any:
        rt = request.app.state.runtime
        await asyncio.to_thread(rt.receipts, run_id)
        await rt.cancel(run_id)
        return wire(await asyncio.to_thread(rt.receipts, run_id))

    @app.post("/v1/bind_cycle", response_model=dict[str, Any])
    async def bind(body: Binding, request: Request) -> Any:
        # A pre-stamp hook (81368e9 and earlier) reads its manifest from the bronze cycle_inputs
        # rows that a stamp close no longer writes, so it would build every relation empty.
        if body.global_inputs is None and not body.lock_runner:
            raise ServiceError(
                "runner_outdated",
                "This dbt hook predates close stamps; deploy mdp-core-runner (and dbt Cloud jobs) on the current image",
                409,
            )
        return wire(
            await request.app.state.runtime.cycles.bind_cycle(**body.model_dump())
        )

    @app.post("/v1/canaries", response_model=dict[str, Any])
    async def source_canaries(request: Request) -> Any:
        from mdp_functions.promoter import ControlTargets

        targets = ControlTargets(request.app.state.runtime.settings)
        async with targets.client() as client:
            return await targets.call(client, "POST", "/api/streamlines/canaries", json={}, timeout=240)

    @app.post("/v1/targets/probe", response_model=dict[str, Any])
    async def targets_probe(body: dict[str, Any], request: Request) -> Any:
        from mdp_functions.target_probe import probe

        # Control sends its transaction's target snapshot, including pending imported members.
        return await probe(request.app.state.runtime, body["targets"])

    @app.post("/v1/functions/{source_key}/probe", response_model=dict[str, Any])
    async def function_probe(source_key: str, request: Request, scope: str = "global") -> Any:
        from mdp_functions.canary import probe

        return await probe(request.app.state.runtime, source_key, scope)

    @app.post("/v1/alerts/canary_failed", response_model=dict[str, Any])
    def canary_failed(body: dict[str, Any], request: Request) -> Any:
        with request.app.state.runtime.db.transaction() as conn:
            subject = body["source_key"] + ":" + body["scope"]
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("canary:" + subject,))
            # Refresh the failure stamp even when the warning already exists. Recovery must
            # observe a passing audit newer than this notification, not an earlier probe.
            conn.execute(
                "UPDATE control.alert SET updated_at=clock_timestamp() "
                "WHERE class='source_canary_failed' AND subject_id=%s AND resolved_at IS NULL",
                (subject,),
            )
            conn.execute(
                "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug) "
                "SELECT 'source_canary_failed','warning','streamline',%s,%s,%s WHERE NOT EXISTS "
                "(SELECT 1 FROM control.alert WHERE class='source_canary_failed' AND subject_id=%s AND resolved_at IS NULL)",
                (subject, body.get("run_id"), error_hint("source_canary_failed")["runbook"], subject),
            )
        return {"status": "recorded"}

    @app.post("/v1/alerts/showcase_inventory_failed", response_model=ShowcaseInventoryAlert)
    def showcase_inventory_failed(body: ShowcaseInventoryFailed, request: Request) -> Any:
        hint = error_hint("showcase_inventory_failed")
        with request.app.state.runtime.db.transaction() as conn:
            warehouse = body.warehouse
            if warehouse is None:
                production = conn.execute("SELECT id FROM control.warehouse WHERE is_production").fetchone()
                if not production:
                    raise ServiceError("not_found", "Production warehouse not found. Open /ops to check setup.", 404)
                warehouse = production["id"]
            subject = str(warehouse)
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("showcase_inventory:" + subject,))
            if not conn.execute("SELECT 1 FROM control.warehouse WHERE id=%s", (warehouse,)).fetchone():
                raise ServiceError("not_found", "Warehouse not found. Open /ops to check its ID.", 404)
            conn.execute(
                "INSERT INTO control.alert(class,severity,subject_type,subject_id,runbook_slug) "
                "SELECT 'showcase_inventory_failed','warning','warehouse',%s,%s WHERE NOT EXISTS "
                "(SELECT 1 FROM control.alert WHERE class='showcase_inventory_failed' AND subject_id=%s AND resolved_at IS NULL)",
                (subject, hint["runbook"], subject),
            )
        return {"status": "recorded", "next_step": hint["next_step"], "warehouse": warehouse}

    @app.post("/v1/alerts/runner_failed", response_model=dict[str, Any])
    def runner_failed(body: RunnerFailed, request: Request) -> Any:
        from mdp_functions.cadence_health import fail_runner

        with request.app.state.runtime.db.transaction() as conn:
            return {"alert_id": fail_runner(conn, body.dbt_run_id, body.job_id, body.model, body.error)}

    @app.post("/v1/alerts/cadence_failed", response_model=dict[str, Any])
    def cadence_failed(body: CadenceFailed, request: Request) -> Any:
        from mdp_functions.core_gate import open_cadence_failed

        with request.app.state.runtime.db.transaction() as conn:
            alert_id, opened = open_cadence_failed(conn, body.job_id, body.period_start)
        return {"alert_id": alert_id, "opened": opened}

    @app.post("/v1/cycles/{cycle_id}/close")
    def close_cycle(cycle_id: UUID, request: Request) -> Any:
        return wire(request.app.state.runtime.cycles.close(cycle_id))

    @app.post("/v1/repair", response_model=dict[str, Any])
    def repair(body: Repair, request: Request) -> Any:
        rt = request.app.state.runtime
        warehouse_id = body.warehouse_id
        if not warehouse_id:
            row = rt.db.one(
                "SELECT r.warehouse_id FROM control.dump d JOIN control.run r ON r.id=d.run_id WHERE d.id=%s",
                (body.dump_id,),
            )
            if not row:
                raise ServiceError("unknown_dump", "Dump does not exist", 404)
            warehouse_id = row["warehouse_id"]
        return rt.landing(warehouse_id).repair(
            body.dump_id, warehouse_id, body.target_table
        )

    @app.post("/v1/costsync", response_model=CostsyncResponse)
    async def costsync(request: Request) -> Any:
        from mdp_functions.costsync import reconcile

        return await reconcile(request.app.state.runtime)

    @app.post("/v1/alerts/reference_generation_incomplete", response_model=dict[str, Any])
    def reference_incomplete(body: ReferenceIncomplete, request: Request) -> Any:
        from mdp_functions.reference import open_incomplete

        with request.app.state.runtime.db.transaction() as conn:
            alert_id, opened = open_incomplete(conn, body.run_id)
        return {"alert_id": alert_id, "opened": opened}

    @app.post("/v1/reference/probe", response_model=ReferenceProbeResponse)
    async def reference_probe(request: Request) -> Any:
        """Refresh the Reference page's state and the reference alert classes now."""
        from mdp_functions.reference import probe

        rt = request.app.state.runtime
        return await asyncio.to_thread(probe, rt.db, rt.settings.service_read_url)

    @app.post("/v1/backfill", status_code=202, response_model=AdmissionResponse)
    async def backfill(body: Backfill, request: Request) -> Any:
        rt = request.app.state.runtime
        run = await asyncio.to_thread(rt.backfill, **body.model_dump(mode="json", by_alias=True))
        rt.start(run["id"])
        return {"run_id": str(run["id"]), "status": run["status"]}

    @app.post("/v1/migrate", response_model=MigrationResponse)
    async def migrate(body: Migrate, request: Request) -> Any:
        return await asyncio.to_thread(request.app.state.runtime.migrate, **body.model_dump())

    @app.get("/v1/functions")
    async def functions() -> Any:
        return [m.public() for m in REGISTRY.values()]

    @app.get("/v1/functions/{source_key}", response_model=FunctionResponse)
    def function(source_key: str, request: Request, preview_table: str | None = None, preview_cursor: str | None = None, metadata_only: bool = False) -> Any:
        rt = request.app.state.runtime
        if source_key not in REGISTRY:
            raise ServiceError("unknown_source", "Function does not exist", 404)
        if (preview_table is not None and preview_table not in REGISTRY[source_key].writes) or (preview_cursor is not None and preview_table is None):
            raise ServiceError("invalid_cursor", "Choose a declared output for this preview", 400)
        runs = rt.db.all(
            """SELECT r.* FROM control.run r JOIN control.streamline s ON s.id=r.streamline_id
            WHERE s.source_key=%s ORDER BY r.created_at DESC LIMIT 20""",
            (source_key,),
        )
        rejected = []
        previews = []
        if settings.service_read_url and not metadata_only:
            request.state.db_subsystem = "warehouse"
            with psycopg.connect(
                settings.service_read_url, row_factory=dict_row
            ) as conn:
                conn.execute("SET LOCAL statement_timeout = '5s'")
                for table in REGISTRY[source_key].writes:
                    previews.append(output_preview(
                        conn, source_key, table, settings.service_token,
                        preview_cursor if table == preview_table else None,
                    ))
                if conn.execute(
                    "SELECT to_regclass('raw._rejected') AS name"
                ).fetchone()["name"]:
                    rejected = conn.execute(
                        "SELECT * FROM raw._rejected WHERE _source_key=%s ORDER BY _ingested_at DESC LIMIT 10",
                        (source_key,),
                    ).fetchall()
        request.state.db_subsystem = "control_db"
        history = [
            json.loads(p.read_text()) | {"fingerprint": p.stem}
            for p in sorted(
                (settings.schema_root / source_key).glob("*.json"),
                key=lambda p: p.stat().st_mtime,
            )
        ]
        return wire(
            {
                "manifest": {**REGISTRY[source_key].public(), "code_fingerprint": fingerprint(REGISTRY[source_key].function) if REGISTRY[source_key].function else None},
                "last_runs": runs,
                "receipts": [] if metadata_only else [rt.receipts(r["id"])["receipts"] for r in runs],
                "rejected_sample": rejected,
                "fingerprint_history": history,
                "output_preview": previews,
                "row_counts": [
                    {"at": r["created_at"].isoformat(), "rows": str(r["rows_written"])}
                    for r in reversed(runs)
                ],
                "log_url": "/functions/" + source_key + "/logs",
            }
        )

    @app.post(
        "/v1/functions/{source_key}/run",
        status_code=202,
        response_model=AdmissionResponse,
    )
    async def manual_run(
        source_key: str,
        request: Request,
        idempotency_key: str | None = Header(None),
        dbt_run_id: str | None = None,
        fixture_scenario: Literal["normal", "html", "missing_stats", "not_found", "retry", "all_null"] | None = None,
    ) -> Any:
        rt = request.app.state.runtime
        if fixture_scenario is not None:
            raise ServiceError("fixture_unavailable", "Use source fixture transports for offline scenarios", 403)
        if not dbt_run_id:
            raise ServiceError(
                "scope_mismatch",
                "Manual invocation requires a validated dbt run binding",
            )
        run = await asyncio.to_thread(
            rt.admit,
            source_key,
            manual=True,
            dbt_run_id=dbt_run_id,
            key=idempotency_key,
        )
        rt.start(run["id"])
        return {"run_id": str(run["id"]), "status": run["status"]}

    @app.post("/v1/registry/sync", response_model=RegistryResponse)
    def registry_sync(request: Request) -> Any:
        from mdp_functions.registry import sync

        rt = request.app.state.runtime
        sync(rt.db, rt.warehouse)
        return {"registered": len(REGISTRY)}

    @app.post("/v1/dbt/webhook", response_model=AdmissionResponse)
    def dbt_webhook(body: DbtWebhook, request: Request) -> Any:
        rt = request.app.state.runtime
        with rt.db.transaction() as conn:
            job = conn.execute(
                "SELECT * FROM control.dbt_job WHERE job_id=%s", (body.job_id,)
            ).fetchone()
            if not job:
                raise ServiceError("unknown_job", "Register the dbt job first", 404)
            warehouse = conn.execute(
                "SELECT id FROM control.warehouse WHERE is_production"
            ).fetchone()
            if not warehouse:
                raise ServiceError(
                    "warehouse_unavailable",
                    "Production warehouse is not configured",
                    503,
                )
            row = conn.execute(
                """INSERT INTO control.run(kind,work_key,scope,warehouse_id,status,error_class,error_message)
                VALUES ('dbt',%s,%s,%s,%s,%s,%s) ON CONFLICT(work_key) DO UPDATE SET
                updated_at=CASE WHEN control.run.status IN ('succeeded','failed') THEN control.run.updated_at ELSE now() END,
                status=CASE WHEN control.run.status IN ('succeeded','failed') THEN control.run.status ELSE EXCLUDED.status END,
                error_class=CASE WHEN control.run.status IN ('succeeded','failed') THEN control.run.error_class ELSE EXCLUDED.error_class END,
                error_message=CASE WHEN control.run.status IN ('succeeded','failed') THEN control.run.error_message ELSE EXCLUDED.error_message END
                RETURNING id,status""",
                (
                    "dbt:" + body.run_id,
                    job["scope"],
                    warehouse["id"],
                    body.status,
                    "dbt_failure" if body.status == "failed" else None,
                    body.message or None,
                ),
            ).fetchone()
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("dbt-event:" + body.event_id,),
            )
            prior = conn.execute(
                "SELECT id FROM control.run_event WHERE run_id=%s AND attrs->>'event_id'=%s",
                (row["id"], body.event_id),
            ).fetchone()
            if not prior:
                conn.execute(
                    "INSERT INTO control.run_event(run_id,level,event_type,message,attrs) VALUES (%s,'info','dbt_webhook',%s,%s)",
                    (
                        row["id"],
                        body.message,
                        Jsonb(body.model_dump() | {"dbt_cloud_run_id": body.run_id}),
                    ),
                )
                if body.status == "failed":
                    conn.execute(
                        "INSERT INTO control.alert(class,severity,subject_type,subject_id,run_id,runbook_slug) VALUES ('dbt_failure','critical','run',%s,%s,'dbt-failure')",
                        (str(row["id"]), row["id"]),
                    )
            if row["status"] == "succeeded":
                # A scheduled build, a Retry or a restore that closed its cycle resolves the cycle's
                # cadence alerts; the SQL refuses a Replay of an older cycle.
                conn.execute(
                    "SELECT control.resolve_recovered_alerts(NULL,cycle_id) FROM control.cycle_attempt "
                    "WHERE dbt_run_id=%s AND job_id=%s AND reason_category IN ('scheduled','other')",
                    (body.run_id, body.job_id),
                )
        return {"run_id": str(row["id"]), "status": row["status"]}

    async def health_checks(request: Request) -> dict[str, str]:
        rt = request.app.state.runtime
        checks = [
            ("control_db", lambda: rt.db.one("SELECT 1")),
            ("warehouse", rt.warehouse.health),
            ("object_store", rt.store.health),
        ]
        status = {}
        for name, check in checks:
            try:
                await control_thread(request, check)
                status[name] = "ok"
            except (
                OSError,
                psycopg.Error,
                PoolTimeout,
                BotoCoreError,
                ClientError,
                duckdb.Error,
                ServiceError,
            ):
                status[name] = "unavailable"
        return status

    @app.get("/v1/health")
    async def health(request: Request) -> Any:
        checks = await health_checks(request)
        healthy = all(value == "ok" for value in checks.values())
        return JSONResponse(
            {"status": "ok" if healthy else "degraded"},
            status_code=200 if healthy else 503,
        )

    @app.get("/v1/health/detail", response_model=dict[str, str])
    async def health_detail(request: Request) -> Any:
        status = await health_checks(request)
        return JSONResponse(
            status, status_code=200 if all(v == "ok" for v in status.values()) else 503
        )

    generate_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        schema = generate_openapi()
        # The public probe is the sole exception to the global bearer policy.
        schema["paths"]["/v1/health"]["get"]["security"] = []
        return schema

    app.openapi = openapi
    FastAPIInstrumentor.instrument_app(app)
    return app


def factory() -> FastAPI:
    settings = Settings()
    configure(settings)
    return create_app(settings)
