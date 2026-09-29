"""One target, one batch, paired embed and page extent evidence."""

from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.playlist import DECLARATION, collect_spotify


@bronze(
    # Public Spotify embed and page GETs use no key or paid transport.
    canary=True,
    source_key="sp_playlist",
    # 34 daily lists: tolerate isolated failures, but require nine in ten targets.
    min_target_coverage=0.9,
    knobs={
        "allow_partial": True,
        "batch_size": 5,
        "max_concurrency": 1,
        "timeout_s": 3000,
    },
    targets=Targets("playlist", platforms=("spotify", "sp"), member_cadence="daily"),
    hosts=["open.spotify.com"],
    **DECLARATION,
)
async def playlist(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_spotify(ctx, batch)
