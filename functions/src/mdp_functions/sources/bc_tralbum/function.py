"""bc_tralbum: album and track pages behind each artist's sitemap lastmod gate.

Artist pages live on per-artist subdomains. The declared `*.bandcamp.com` keys
their pacing, permits, and block pauses on `bandcamp.com`, so a block on one artist
host pauses them all; the collector fetches only its target's own host.
"""

from mdp_functions.bandcamp import RELEASES, collect_releases
from mdp_functions.layers import Ctx, Targets, bronze


@bronze(
    # Public sitemap and release HTML GETs use no key or paid transport.
    canary=True,
    source_key="bc_tralbum",
    knobs={"enabled": False, "batch_size": 2, "max_concurrency": 1, "timeout_s": 1800},
    targets=Targets("artist_page"),
    hosts=["*.bandcamp.com"],
    cadence="daily",
    **RELEASES,
)
async def releases(ctx: Ctx, batch: Targets.Batch) -> None:
    await collect_releases(ctx, batch)
