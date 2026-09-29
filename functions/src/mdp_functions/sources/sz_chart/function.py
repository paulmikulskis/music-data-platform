"""sz_chart: Shazam's country Top 200, Discovery and city Top 50 charts, daily."""

from collections.abc import AsyncIterator
from typing import Any

from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.shazam import HOSTS, ShazamChartEntry, collect


@bronze(
    # Public chart CSV and HTML GETs use no key, quota account or paid transport.
    canary=True,
    source_key="sz_chart",
    # 58 seeded charts: isolated dead charts must not hide a broad outage.
    min_target_coverage=0.9,
    writes=["raw.shazam_chart_entries"],
    cadence="daily",
    targets=Targets("chart", platforms=("shazam",)),
    key=["chart", "chart_date", "position"],
    schema=ShazamChartEntry,
    hosts=HOSTS,
    # About 60 charts, a CSV and a page each, at 0.5 requests a second: 240 s plus 25%, and the
    # pages' transfer time (up to 845 KB on the wire each).
    knobs={
        "allow_partial": True,
        "batch_size": 5,
        "max_concurrency": 1,
        "timeout_s": 900,
    },
)
async def charts(ctx: Ctx, batch: Targets.Batch) -> AsyncIterator[dict[str, Any]]:
    async for row in collect(ctx, batch):
        yield row
