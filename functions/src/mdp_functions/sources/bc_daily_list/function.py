"""bc_daily_list: Bandcamp Daily editorial lists (daily targets)."""

from mdp_functions.bandcamp import collect_daily
from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.playlist import DECLARATION


@bronze(
    # Public Bandcamp pages and read APIs use no key or paid transport; probes refuse POST.
    canary=True,
    source_key="bc_daily_list",
    knobs={"enabled": False, "batch_size": 5, "max_concurrency": 1, "timeout_s": 900},
    targets=Targets(
        "playlist", platforms=("bandcamp",), member_cadence="daily", id_prefix="daily:"
    ),
    hosts=["daily.bandcamp.com"],
    **DECLARATION,
)
async def playlist(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_daily(ctx, batch)
