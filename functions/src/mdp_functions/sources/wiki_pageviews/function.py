"""wiki_pageviews: enrich public MusicBrainz artist identities on a cycle-bound schedule."""

from mdp_functions import wikimedia
from mdp_functions.layers import Ctx, gold
from mdp_functions.wikimedia import PAGEVIEW_HOSTS, WikiPageview


@gold(
    source_key="wiki_pageviews",
    reads=["intermediate.int_wiki__articles"],
    writes=["raw.wiki_pageviews"],
    cadence="daily",
    external=True,
    input_key=["qid", "project"],
    input_version=["title", "pageview_day"],
    input_order=["project_rank", "qid", "project"],
    output_key=["date"],
    schema=WikiPageview,
    hosts=PAGEVIEW_HOSTS,
    # 600 s at wikimedia.org's 2 requests a second reads 1,200 articles: 120 acts at PROJECTS_PER_ACT, with
    # the invoke's 900 s timeout (870 s deadline) covering a part of 25 inputs past the budget.
    time_budget_s=600,
    knobs={"allow_partial": True, "batch_size": 25, "max_concurrency": 1, "timeout_s": 900},
)
async def views(ctx: Ctx, rows: list[dict]) -> None:
    await wikimedia.pageviews(ctx, rows)
