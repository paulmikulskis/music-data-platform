"""lb_sitewide: ListenBrainz's sitewide weekly top 1,000 artists, recordings and release groups, with the
window and the time the upstream statistics job computed them. Gated on the MetaBrainz
supporter tier."""

from collections.abc import AsyncIterator
from typing import Any

from mdp_functions import listenbrainz
from mdp_functions.layers import Ctx, bronze
from mdp_functions.listenbrainz import HOSTS, LbSitewide


@bronze(
    source_key="lb_sitewide",
    writes=["raw.lb_sitewide"],
    cadence="weekly",
    key=["entity_type", "window_start", "last_updated", "rank"],
    schema=LbSitewide,
    hosts=HOSTS,
    knobs={"enabled": False, "timeout_s": 600},
)
async def charts(ctx: Ctx) -> AsyncIterator[dict[str, Any]]:
    async for row in listenbrainz.sitewide(ctx):
        yield row
