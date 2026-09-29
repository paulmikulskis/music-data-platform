"""kexp_plays: KEXP's track plays by airdate window, daily from a watermark, with a two-year backfill one
airdate year per `backfill:` window. Operator-only and learning false until the registry
records KEXP's written permission; the key ships disabled."""

from collections.abc import AsyncIterator
from typing import Any

from mdp_functions import kexp
from mdp_functions.kexp import HOSTS, RadioPlay
from mdp_functions.layers import Ctx, bronze


@bronze(
    # Public play-history GET uses no key, quota account or paid transport.
    canary=True,
    source_key="kexp_plays",
    exclusion_reasons=("not_a_trackplay",),
    writes=["raw.radio_plays"],
    cadence="daily",
    key=["station", "play_id"],
    schema=RadioPlay,
    hosts=HOSTS,
    # A year's backfill window is about 1,500 pages at one request a second.
    knobs={"enabled": False, "timeout_s": 3600},
)
async def plays(ctx: Ctx) -> AsyncIterator[dict[str, Any]]:
    async for row in kexp.plays(ctx):
        yield row
