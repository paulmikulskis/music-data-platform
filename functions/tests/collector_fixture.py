"""Run one collector over fixture pages, one target or input at a time, as the runtime runs a
batch (the harness of subject_collector_fixture.collect, with any gold input key)."""

import json
from pathlib import Path
from uuid import uuid4

from mdp_functions.errors import ServiceError
from mdp_functions.http import FixtureTransport, TracedClient
from mdp_functions.layers import Ctx, Target
from mdp_functions.registry import discover
from test_fetch_runtime import FakeDB, no_page

SOURCES = Path(__file__).parents[1] / "src/mdp_functions/sources"
# The failures the runtime records per target before it goes on (runs.execute).
TARGET_ERRORS = {"vendor_4xx", "vendor_retryable", "scrape_blocked", "envelope_mismatch", "stale_target"}


def target(platform, account, handle=None, **params):
    return Target(id=str(uuid4()), platform=platform, platform_account_id=account, handle=handle, params_json=params)


async def collect(key, batch, scenario="normal", transport=None, relations=None):
    """Returns (ctx, requests, refused): a gold input's rejects stay on ctx.rejected; a bronze target
    error the runtime would record is in refused."""
    manifest = discover()[key]
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None}
    ctx = Ctx(manifest, run)
    ctx.relations = dict(relations or {})
    calls, refused = [], []

    async def seen(request):
        calls.append(request)

    fixture = transport or FixtureTransport([SOURCES / key / "fixtures" / f"{scenario}.jsonl"])
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=fixture, event_hooks={"request": [seen]}) as client:
        ctx.http = client
        for item in batch:
            ctx.target, ctx.target_id = item, str(item.get("id") or "")
            try:
                if manifest.layer == "gold":
                    identity = json.dumps([item.get(k) for k in manifest.input_key])
                    ctx.input_row = {**item, "input_ref": identity, "input_version": identity}
                    await manifest.function(ctx, [item])
                elif manifest.writes and len(manifest.writes) > 1:
                    await manifest.function(ctx, [item])
                else:
                    async for row in manifest.function(ctx, [item]):
                        ctx.yield_row(row)
            except ServiceError as exc:
                if exc.error_class not in TARGET_ERRORS:
                    raise
                refused.append(exc.error_class)
    return ctx, calls, refused
