"""Fixture-only daily source for commit-boundary races."""

import os
from collections.abc import AsyncIterator

from mdp_functions.fixture_control import PLANS
from mdp_functions.layers import Ctx, bronze
from pydantic import BaseModel


class ProbeRow(BaseModel):
    page: int


if os.environ.get("MDP_FIXTURE_MODE") == "1":

    @bronze(
        source_key="lifecycle_daily_probe",
        writes=["raw.lifecycle_daily_probe"],
        cadence="daily",
        schema=ProbeRow,
        key=["page"],
    )
    async def probe(ctx: Ctx) -> AsyncIterator[dict[str, int]]:
        for page in range(1, PLANS["lifecycle_daily_probe"].pages + 1):
            await ctx.http.get(
                "https://fixture.invalid/lifecycle_daily_probe", params={"page": page}
            )
            ctx.observed(1)
            yield {"page": page}
