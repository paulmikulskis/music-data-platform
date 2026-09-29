"""bc_discover_weekly: Bandcamp discover rankings, one call per spec (weekly targets)."""

from mdp_functions.bandcamp import collect_discover
from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.playlist import DECLARATION


@bronze(
    # Public Bandcamp pages and read APIs use no key or paid transport; probes refuse POST.
    canary=True,
    source_key="bc_discover_weekly",
    knobs={"enabled": False, "batch_size": 5, "max_concurrency": 1, "timeout_s": 1500},
    targets=Targets(
        "playlist",
        platforms=("bandcamp",),
        member_cadence="weekly",
        id_prefix="discover:",
    ),
    hosts=["bandcamp.com"],
    **DECLARATION,
)
async def playlist(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_discover(ctx, batch)
