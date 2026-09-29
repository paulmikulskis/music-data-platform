"""Fixture-only account records for runtime accounting and retry tests."""
import os
from datetime import datetime, timezone

from mdp_functions.layers import Ctx, Targets, bronze, gold

if os.environ.get("MDP_FIXTURE_MODE") == "1":
    @bronze(source_key="fixture_accounts", writes=["raw.account_snapshots"], cadence="hourly",
            targets=Targets("account", platforms=("fixture",)), key=["platform_account_id", "snapshot_at"],
            schema="infer", keep_payload=True, min_target_coverage=0.9, knobs={"allow_partial": True})
    async def accounts(ctx: Ctx, batch: Targets.Batch):
        for target in batch:
            response = await ctx.http.get("https://fixture.invalid/accounts", params={"username": target.handle})
            ctx.observed(1)
            try:
                payload = response.json()
            except ValueError:
                ctx.reject(response.text, reason="Expected JSON account record")
                continue
            record = payload.get("record")
            if not isinstance(record, dict):
                ctx.reject(payload, reason="invalid_record")
                continue
            stats = record.get("stats", record)
            identity = record.get("identity", {})
            if not isinstance(stats, dict) or not isinstance(identity, dict):
                ctx.reject(payload, reason="invalid_record")
                continue
            yield {"platform": target.platform, "platform_account_id": identity.get("id", target.platform_account_id),
                   "handle": target.handle, "snapshot_at": datetime.now(timezone.utc),
                   "followers": stats.get("followers"), "following": stats.get("following"),
                   "likes": stats.get("likes"), "posts": stats.get("posts")}


    @gold(source_key="fixture_enrichment", reads=["marts.mart_enrichment_fixture"],
          writes=["raw.fixture_enrichment"], cadence="daily", llm_step="fixture_enrichment",
          input_key=["platform_account_id"], input_version=["followers"], knobs={"allow_partial": True})
    async def enrichment(ctx, rows):
        for row in rows:
            yield {"label": await ctx.llm.classify(row, prompt=ctx.prompt), **ctx.input_identity(row)}
