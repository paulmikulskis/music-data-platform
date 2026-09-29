"""sc_curator_playlists: curator listings as candidates and a body change gate."""

from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.soundcloud import CURATORS, HOSTS, collect_curators


@bronze(
    source_key="sc_curator_playlists",
    knobs={"enabled": False, "batch_size": 2, "max_concurrency": 1, "timeout_s": 1800},
    targets=Targets("curator", platforms=("soundcloud",), member_cadence="daily"),
    hosts=HOSTS,
    **CURATORS,
)
async def curators(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_curators(ctx, batch)
