"""sc_playlist: SoundCloud playlist bodies with ISRC hydration (daily targets)."""

from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.soundcloud import PLAYLISTS, collect_playlists


@bronze(
    source_key="sc_playlist",
    knobs={"enabled": False, "batch_size": 5, "max_concurrency": 1, "timeout_s": 1800},
    targets=Targets("playlist", platforms=("soundcloud",), member_cadence="daily"),
    hosts=["api-v2.soundcloud.com", "soundcloud.com"],
    **PLAYLISTS,
)
async def playlist(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_playlists(ctx, batch)
