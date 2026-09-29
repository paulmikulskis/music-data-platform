"""In-memory canaries: read live inputs, fetch, parse and validate without publication."""

import asyncio
import inspect
from datetime import UTC, datetime
from uuid import uuid4

from mdp_functions.errors import ServiceError
from mdp_functions.fetch.guard import source_network
from mdp_functions.health_policy import CANARY_TIMEOUT_S as TIMEOUT_S
from mdp_functions.http import DryRunUnavailable, FixtureTransport, TracedClient
from mdp_functions.layers import Ctx, Target
from mdp_functions.playlist import cadence_matches
from mdp_functions.registry import REGISTRY
from mdp_functions.schemas import shape
from mdp_functions.settings import PACKAGE

SAMPLE_SIZE = 1


class ProbeComplete(BaseException):
    """Stop at the sample boundary even if a collector catches Exception for fallback."""


class ProbeDB:
    """Only read transactions reach the shared control database."""

    def __init__(self, db):
        self._db = db

    def all(self, query, params=()):
        with self._db.transaction() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout='2s'")
            return conn.execute(query, params).fetchall()

    def one(self, query, params=()):
        return next(iter(self.all(query, params)), None)


class ProbeCtx(Ctx):
    def emit(self, table, row):
        super().emit(table, row)
        if self.yielded_count >= SAMPLE_SIZE:
            raise ProbeComplete()


def matches_source(manifest, target):
    """Only the platforms and list kinds this collector serves are sample candidates."""
    key = manifest.source_key.removesuffix("_weekly")
    if key.startswith("bc_"):
        from mdp_functions.bandcamp import kind_of

        kinds = {"bc_discover": "discover", "bc_daily_list": "daily", "bc_radio": "radio", "bc_fan_playlist": "playlist"}
        if key in kinds:
            kind = kind_of(target)
            return bool(kind and kind[0] == kinds[key])
        return target.platform == "bandcamp"
    platforms = {
        "am_playlist": {"apple_music", "am"}, "sp_playlist": {"spotify", "sp"},
        "sc_playlist": {"soundcloud"}, "sc_curator_playlists": {"soundcloud"},
        "sz_chart": {"shazam"},
    }
    return key not in platforms or target.platform in platforms[key]


def sample_targets(db, manifest, ctx, tenant_id):
    # One statement freezes current identity and specification in memory. It publishes no export.
    members = db.all(
        "SELECT t.*,s.resource_kind,s.canonical_key,"
        "CASE WHEN t.role IS NOT NULL THEN jsonb_set(coalesce(s.params_json,'{}'::jsonb),'{role}',to_jsonb(t.role)) "
        "ELSE coalesce(s.params_json,'{}'::jsonb) END AS params_json "
        "FROM control.target t JOIN control.target_set ts ON ts.id=t.target_set_id "
        "LEFT JOIN control.target_spec s ON s.target_id=t.id "
        "WHERE ts.kind=%s AND ts.tenant_id IS NOT DISTINCT FROM %s::uuid "
        "AND t.resolution_status='resolved' AND t.activated_at<=now() AND t.deactivated_at IS NULL ORDER BY t.id",
        (manifest.targets.kind, tenant_id),
    )
    targets = [Target(m) for m in members if matches_source(manifest, Target(m))]
    if not targets:
        return [], "skipped"
    if manifest.targets.kind in {"playlist", "curator"}:
        targets = [t for t in targets if cadence_matches(ctx, t)]
        if not targets:
            return [], "not_due"
    return targets[:SAMPLE_SIZE], None


def validate(ctx):
    if ctx.rejected:
        raise ServiceError("partial_coverage", "The sample contains rejected records")
    count = 0
    for table, records in ctx.outputs.items():
        declaration = ctx.manifest.schema.get(table, "infer") if isinstance(ctx.manifest.schema, dict) else ctx.manifest.schema
        accepted, _, rejected, _ = shape(records, declaration, None)
        if rejected:
            raise ServiceError("schema_drift", "The sample does not match the declared output schema")
        count += len(accepted)
    if not count and not ctx.exclusions:
        raise ServiceError("partial_coverage", "The sample produces no records to validate")
    return count


async def probe(rt, source_key, scope="global"):
    try:
        async with asyncio.timeout(TIMEOUT_S):
            return await _probe(rt, source_key, scope)
    except TimeoutError:
        return {"status": "timed_out", "error_class": "invoke_timeout", "records_validated": 0,
                "next_step": f"Open /functions/{source_key}"}


async def _probe(rt, source_key, scope):
    manifest = REGISTRY.get(source_key)
    if not manifest:
        raise ServiceError("unknown_source", "Choose a registered function", 404)
    page = f"Open /functions/{source_key}"
    def outcome(status, reason=None, error_class=None, records=0):
        return {"status": status, "error_class": error_class, "records_validated": records,
                "next_step": f"{reason}; {page}" if reason else page}
    if not manifest.canary:
        return outcome("skipped", "not declared probe-safe")
    # These execution contracts require runtime-owned state or paid request accounting.
    # Refuse by capability before invoking any preparation or source code.
    if manifest.kind != "invoke" or manifest.layer != "bronze" or manifest.prepare or manifest.completion or manifest.reads or manifest.scheduled_only:
        return outcome("skipped", "This function requires a cycle, declared warehouse inputs or completion state")
    db = ProbeDB(rt.db)
    tenant_id = scope.removeprefix("tenant:") if scope.startswith("tenant:") else None
    if manifest.tenant_bound != bool(tenant_id) or (scope != "global" and not tenant_id):
        raise ServiceError("scope_mismatch", "Use the function's declared scope", 422)
    tenant = db.one("SELECT * FROM control.tenant WHERE id=%s AND status='active'", (tenant_id,)) if tenant_id else None
    if tenant_id and not tenant:
        return outcome("skipped", "No active tenant")
    stream = db.one("SELECT * FROM control.streamline WHERE source_key=%s", (source_key,))
    if not stream or not stream["enabled"]:
        return outcome("skipped", "Function is paused")
    if manifest.provider or rt.settings.vendor_estimates.get(source_key, 0) > 0 or (stream.get("transport_override") or manifest.transport) != "direct":
        return outcome("skipped", "This transport requires paid request accounting")
    run = {"id": uuid4(), "cycle_id": None, "streamline_id": stream["id"],
           "cycle_opened_at": datetime.now(UTC), "tenant": tenant}
    ctx = ProbeCtx(manifest, run)
    targets, noop = sample_targets(db, manifest, ctx, tenant_id) if manifest.targets else ([None], None)
    if noop:
        return outcome(noop, "No due target" if noop == "not_due" else "No active target")
    transport = rt.transport
    if transport is None and rt.settings.fixture:
        path = PACKAGE / "sources" / source_key.removesuffix("_weekly") / "fixtures" / "normal.jsonl"
        transport = FixtureTransport([path] if path.exists() else [])
    async def before_page():
        if ctx.outputs or ctx.rejected or ctx.exclusions:
            raise ProbeComplete()
    try:
        async with TracedClient(ctx, db, run, before_page, settings=rt.settings, transport=transport, dry_run=True) as client:
            ctx.http = client
            for target in targets:
                ctx.target, ctx.target_id = target, str(target["id"]) if target else None
                body = None
                try:
                    with source_network(ctx):
                        args = [ctx, [target]] if manifest.targets else [ctx]
                        if not manifest.targets and len(inspect.signature(manifest.function).parameters) > 1:
                            args.append([])
                        body = manifest.function(*args)
                        if inspect.isasyncgen(body):
                            async for row in body:
                                ctx.yield_row(row)
                        else:
                            await body
                finally:
                    if inspect.isasyncgen(body):
                        with source_network(ctx):
                            await body.aclose()
    except ProbeComplete:
        pass
    except DryRunUnavailable as exc:
        return outcome("skipped", str(exc))
    except ServiceError as exc:
        return outcome("failed", error_class=exc.error_class)
    except Exception:  # noqa: BLE001 -- a source exception cannot escape the probe boundary
        return outcome("failed", error_class="function_failed")
    try:
        return outcome("passed", records=validate(ctx))
    except ServiceError as exc:
        return outcome("failed", error_class=exc.error_class)
