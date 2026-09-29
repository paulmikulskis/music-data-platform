"""wiki_sitelinks: enrich public MusicBrainz artist identities on a cycle-bound schedule."""

from mdp_functions import wikimedia
from mdp_functions.layers import Ctx, gold
from mdp_functions.wikimedia import SITELINK_HOSTS, WikiLookup, WikiSitelink


@gold(
    source_key="wiki_sitelinks",
    reads=["intermediate.int_wiki__artist_qids"],
    writes=["raw.wiki_sitelinks", "raw.wiki_lookups"],
    cadence="daily",
    external=True,
    input_key=["qid"],
    input_version=["qid", "sitelinks_week"],
    output_key=["site"],
    # One lookup row per completed lookup, whatever it found.
    output_keys={"raw.wiki_lookups": ["entity_qid"]},
    schema={"raw.wiki_sitelinks": WikiSitelink, "raw.wiki_lookups": WikiLookup},
    hosts=SITELINK_HOSTS,
    # Past 600 s (1,200 lookups at www.wikidata.org's 2 a second) the run ends partial, well inside the
    # invoke's 870 s deadline, and the rest of the week's lookups wait for the next daily cycle.
    time_budget_s=600,
    knobs={"allow_partial": True, "batch_size": 25, "max_concurrency": 1, "timeout_s": 900},
)
async def articles(ctx: Ctx, rows: list[dict]) -> None:
    await wikimedia.sitelinks(ctx, rows)
