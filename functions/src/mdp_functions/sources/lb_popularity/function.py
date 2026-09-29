"""lb_popularity: enrich public MusicBrainz artist identities on a cycle-bound schedule."""

from mdp_functions import listenbrainz
from mdp_functions.layers import Ctx, gold
from mdp_functions.listenbrainz import HOSTS, LbPopularity


@gold(
    source_key="lb_popularity",
    reads=["intermediate.int_lb__popularity_inputs"],
    writes=["raw.lb_popularity"],
    cadence="daily",
    external=True,
    input_key=["mb_artist_gid"],
    input_version=["mb_artist_gid", "popularity_day"],
    schema=LbPopularity,
    hosts=HOSTS,
    # Commercial use rides on the MetaBrainz supporter tier: enabled once the purchase is made.
    knobs={"enabled": False, "allow_partial": True, "batch_size": 25, "max_concurrency": 1, "timeout_s": 900},
)
async def totals(ctx: Ctx, rows: list[dict]) -> None:
    await listenbrainz.popularity(ctx, rows)
