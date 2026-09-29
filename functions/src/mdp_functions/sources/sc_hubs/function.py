"""sc_hubs: SoundCloud editorial selections and chart shelves as candidates."""

from mdp_functions.layers import Ctx, bronze
from mdp_functions.playlist import CANDIDATES
from mdp_functions.soundcloud import HOSTS, collect_hubs


@bronze(
    source_key="sc_hubs",
    knobs={"enabled": False, "timeout_s": 900},
    hosts=HOSTS,
    cadence="daily",
    **CANDIDATES,
)
async def hubs(ctx: Ctx) -> None:
    await collect_hubs(ctx)
