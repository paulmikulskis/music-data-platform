"""sc_curator_playlists_weekly: curator listings and the body change gate for
weekly curators."""

from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.soundcloud import CURATORS, HOSTS, collect_curators


@bronze(
    source_key="sc_curator_playlists_weekly",
    knobs={"enabled": False, "batch_size": 2, "max_concurrency": 1, "timeout_s": 1800},
    targets=Targets("curator", platforms=("soundcloud",), member_cadence="weekly"),
    hosts=HOSTS,
    **CURATORS,
)
async def curators(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_curators(ctx, batch)
