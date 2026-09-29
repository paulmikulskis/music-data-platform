"""lb_similar_artists: enrich public MusicBrainz artist identities on a cycle-bound schedule."""

from mdp_functions import listenbrainz
from mdp_functions.layers import Ctx, gold
from mdp_functions.listenbrainz import LABS_HOSTS, LbSimilarArtist


@gold(
    source_key="lb_similar_artists",
    reads=["intermediate.int_lb__similarity_inputs"],
    writes=["raw.lb_similar_artists"],
    cadence="daily",
    external=True,
    input_key=["mb_artist_gid", "algorithm"],
    input_version=["mb_artist_gid", "algorithm", "similarity_week"],
    output_key=["neighbour_mbid"],
    schema=LbSimilarArtist,
    hosts=LABS_HOSTS,
    knobs={"enabled": False, "allow_partial": True, "batch_size": 25, "max_concurrency": 1, "timeout_s": 900},
)
async def neighbours(ctx: Ctx, rows: list[dict]) -> None:
    await listenbrainz.similar_artists(ctx, rows)
