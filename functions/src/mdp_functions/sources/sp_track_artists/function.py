"""sp_track_artists: artist ids from the Spotify track page for tracks no playlist page row
identified. Once per track id, oldest first, under a time budget."""

from mdp_functions import releases
from mdp_functions.layers import Ctx, gold
from mdp_functions.releases import SPOTIFY_HOSTS, SpTrackArtist


@gold(
    source_key="sp_track_artists",
    reads=["intermediate.int_spotify__track_artist_inputs"],
    writes=["raw.sp_track_artists"],
    cadence="daily",
    external=True,
    input_key=["platform_track_id"],
    input_version=["platform_track_id"],
    output_key=["artist_id"],
    schema=SpTrackArtist,
    hosts=SPOTIFY_HOSTS,
    time_budget_s=1200,
    # Oldest first; a track that failed on its own in three runs (a 404, a page without its entity)
    # is parked for 28 days, so dead ids never head the read or open the circuit on live ones.
    input_order=["first_landed_seq", "platform_track_id"],
    park_after=3,
    knobs={"allow_partial": True, "batch_size": 25, "max_concurrency": 1, "timeout_s": 1800},
)
async def track_artists(ctx: Ctx, rows: list[dict]) -> None:
    await releases.sp_track_artists(ctx, rows)
