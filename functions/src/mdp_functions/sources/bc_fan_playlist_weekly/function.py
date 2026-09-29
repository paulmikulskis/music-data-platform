"""bc_fan_playlist_weekly: Bandcamp public fan playlists (weekly targets)."""

from mdp_functions.bandcamp import collect_fan_playlists
from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.playlist import DECLARATION


@bronze(
    # Public Bandcamp pages and read APIs use no key or paid transport; probes refuse POST.
    canary=True,
    source_key="bc_fan_playlist_weekly",
    knobs={"enabled": False, "batch_size": 5, "max_concurrency": 1, "timeout_s": 1800},
    targets=Targets(
        "playlist",
        platforms=("bandcamp",),
        member_cadence="weekly",
        id_prefix="playlist:",
    ),
    hosts=["bandcamp.com"],
    **DECLARATION,
)
async def playlist(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_fan_playlists(ctx, batch)
