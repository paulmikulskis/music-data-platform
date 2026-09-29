"""Fixture-only tenant source for receipt and schema isolation."""

import os
from collections.abc import AsyncIterator

from mdp_functions.fixture_control import PLANS
from mdp_functions.layers import Ctx, bronze
from pydantic import BaseModel


class ProbeRow(BaseModel):
    page: int
    tenant_id: str | None = None


if os.environ.get("MDP_FIXTURE_MODE") == "1":

    @bronze(
        source_key="lifecycle_tenant_probe",
        writes=["raw.lifecycle_tenant_probe"],
        cadence="daily",
        tenant_bound=True,
        schema=ProbeRow,
        key=["page", "tenant_id"],
    )
    async def probe(ctx: Ctx) -> AsyncIterator[dict[str, int]]:
        for page in range(1, PLANS["lifecycle_tenant_probe"].pages + 1):
            await ctx.http.get(
                "https://fixture.invalid/lifecycle_tenant_probe", params={"page": page}
            )
            ctx.observed(1)
            yield {"page": page}
