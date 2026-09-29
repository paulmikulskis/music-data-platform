"""am_playlist: public playlist observation, snapshot emitted after its items."""

from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.playlist import DECLARATION, collect_apple


@bronze(
    # Public music.apple.com HTML over direct GET; no key, vendor API or request cost.
    canary=True,
    source_key="am_playlist",
    # 46 daily lists: tolerate isolated failures, but require nine in ten targets.
    min_target_coverage=0.9,
    knobs={
        "allow_partial": True,
        "batch_size": 5,
        "max_concurrency": 1,
        "timeout_s": 1500,
    },
    targets=Targets(
        "playlist", platforms=("apple_music", "am"), member_cadence="daily"
    ),
    hosts=["music.apple.com"],
    **DECLARATION,
)
async def playlist(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_apple(ctx, batch)
