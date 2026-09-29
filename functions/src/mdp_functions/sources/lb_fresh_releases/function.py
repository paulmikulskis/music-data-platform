"""lb_fresh_releases: releases ListenBrainz dates within three days of today, with listen counts; tags never
land. Gated on the MetaBrainz supporter tier."""

from collections.abc import AsyncIterator
from typing import Any

from mdp_functions import listenbrainz
from mdp_functions.layers import Ctx, bronze
from mdp_functions.listenbrainz import HOSTS, LbFreshRelease


@bronze(
    source_key="lb_fresh_releases",
    writes=["raw.lb_fresh_releases"],
    cadence="daily",
    key=["release_mbid", "observed_at"],
    schema=LbFreshRelease,
    hosts=HOSTS,
    knobs={"enabled": False, "timeout_s": 600},
)
async def releases(ctx: Ctx) -> AsyncIterator[dict[str, Any]]:
    async for row in listenbrainz.fresh_releases(ctx):
        yield row
