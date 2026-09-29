"""Probe the collector's first request, with its host cap and no landing."""

import asyncio
import inspect
from uuid import uuid4

from mdp_functions.control_db import event
from mdp_functions.errors import ServiceError
from mdp_functions.http import DryRunUnavailable, FixtureTransport, TracedClient
from mdp_functions.layers import Ctx, Target
from mdp_functions.registry import REGISTRY
from mdp_functions.settings import PACKAGE


class ProbeResult(BaseException):
    # Collector fallbacks may catch Exception. The probe stops before any fallback request.
    def __init__(self, status, error=None):
        self.status, self.error = status, error


class ProbeClient(TracedClient):
    async def send(self, request, **kwargs):
        kwargs["follow_redirects"] = False
        try:
            response = await super().send(request, **kwargs)
        except ServiceError as exc:
            raise ProbeResult(exc.vendor_status, exc.error_class) from exc
        raise ProbeResult(response.status_code)


async def probe_target(rt, target, kind):
    """The function selects its platform as usual. No matching request is an explicit skip."""
    candidates = [m for m in REGISTRY.values() if m.targets and m.targets.kind == kind
                  and m.layer == "bronze" and m.tenant_bound == bool(target.get("tenant_id"))]
    # Daily and weekly twins share the same first request. Use the frozen cadence if present.
    cadence = (target.get("params_json") or {}).get("cadence", "daily")
    candidates.sort(key=lambda m: (m.source_key.endswith("_weekly") != (cadence == "weekly"), m.source_key))
    for manifest in candidates:
        stream = rt.db.one("SELECT id,enabled FROM control.streamline WHERE source_key=%s", (manifest.source_key,))
        if not stream or not stream["enabled"]:
            continue
        run = rt.db.one(
            "INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,tenant_id,status) "
            "SELECT 'invoke',%s,%s,%s,id,%s,'running' FROM control.warehouse WHERE is_production RETURNING *",
            ("target-probe:" + uuid4().hex, "tenant:" + str(target["tenant_id"]) if target.get("tenant_id") else "global",
             stream["id"], target.get("tenant_id")),
        )
        ctx = Ctx(manifest, run)
        ctx.is_probe = True
        ctx.target, ctx.target_id = Target(target), None
        async def flush(ctx=ctx):
            ctx.clear_page()
        transport = rt.transport
        if transport is None and rt.settings.fixture:
            path = PACKAGE / "sources" / manifest.source_key.removesuffix("_weekly") / "fixtures" / "normal.jsonl"
            transport = FixtureTransport([path] if path.exists() else [])
        result = {"id": str(target["id"]), "source_key": manifest.source_key, "status": "skipped", "http_status": None}
        try:
            async with ProbeClient(ctx, rt.db, run, flush, settings=rt.settings, transport=transport, probe=True) as client:
                ctx.http = client
                body = manifest.function(ctx, [ctx.target])
                async with asyncio.timeout(15):
                    if inspect.isasyncgen(body):
                        async for _ in body:
                            pass
                    else:
                        await body
        except DryRunUnavailable:
            pass
        except ProbeResult as response:
            result.update(status=response.error or ("stale_target" if response.status in (404, 410) else "ok"), http_status=response.status)
        except ServiceError as exc:
            result.update(status=exc.error_class, http_status=exc.vendor_status)
        except (TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError):
            result.update(status="probe_unavailable")
        finally:
            rt.db.execute("UPDATE control.run SET status=%s,error_class=%s,updated_at=now() WHERE id=%s",
                          ("succeeded" if result["status"] in ("ok", "skipped") else "failed",
                           None if result["status"] in ("ok", "skipped") else result["status"], run["id"]))
            with rt.db.transaction() as conn:
                event(conn, run["id"], "target_probe", "First target request", result)
        if result["status"] != "skipped":
            return result
    return {"id": str(target["id"]), "status": "skipped", "http_status": None, "source_key": None}


async def probe(rt, targets):
    results = []
    for target in targets:
        results.append(await probe_target(rt, target, target["kind"]))
    return {"results": results}
