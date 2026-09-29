"""Wikidata sitelinks and Wikimedia pageviews for public MusicBrainz artist identities."""

import weakref
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel

from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx

WIKIDATA = "https://www.wikidata.org/w/api.php"
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
SITELINK_HOSTS = ["www.wikidata.org"]
PAGEVIEW_HOSTS = ["wikimedia.org"]
# Wikidata sites that end in `wiki` but are not a Wikipedia language edition.
NOT_WIKIPEDIA = frozenset({
    "commonswiki", "specieswiki", "metawiki", "mediawikiwiki", "wikidatawiki", "sourceswiki",
    "wikimaniawiki", "outreachwiki", "incubatorwiki", "wikifunctionswiki", "foundationwiki",
    "testwiki", "test2wiki", "testwikidatawiki", "wikimaniateamwiki", "strategywiki",
})
# Wikidata site ids whose Wikipedia host is not the site prefix with hyphens.
PROJECT_HOSTS = {"be_x_oldwiki": "be-tarask", "zh_classicalwiki": "zh-classical"}
REFETCH_DAYS = 3
# The newest day any pageviews response of a run carried: Wikimedia has published through it.
PUBLISHED: "weakref.WeakKeyDictionary[Ctx, date]" = weakref.WeakKeyDictionary()
# Wikipedia articles fetched per act a day: the act's editions in seeds/wiki_projects.csv rank order, then
# the rest by project; int_wiki__articles applies it (a test holds the two equal).
PROJECTS_PER_ACT = 10


class WikiSitelink(BaseModel):
    """One Wikipedia article of an act's Wikidata item, as the item listed it."""

    qid: str
    entity_qid: str
    site: str
    project: str
    title: str
    observed_at: datetime


class WikiLookup(BaseModel):
    """One completed lookup of an act's Wikidata item: how many Wikipedia articles it listed (0 is a finding,
    distinct from a lookup that failed or has not run)."""

    qid: str
    entity_qid: str
    articles: int
    observed_at: datetime


class WikiPageview(BaseModel):
    """Human daily views of one article on one UTC day."""

    qid: str
    project: str
    title: str
    date: date
    views: int
    observed_at: datetime


def project(site: str) -> str | None:
    """`enwiki` -> `en.wikipedia`; None for a site that is not a Wikipedia edition."""
    if not site.endswith("wiki") or site in NOT_WIKIPEDIA or not site[:-4]:
        return None
    return PROJECT_HOSTS.get(site, site[:-4].replace("_", "-")) + ".wikipedia"


def sitelinks_params(qid: str) -> dict[str, str]:
    return {"action": "wbgetentities", "ids": qid, "props": "sitelinks", "format": "json"}


def pageviews_url(project: str, title: str, start: date, end: date) -> str:
    article = quote(title.replace(" ", "_"), safe="")
    return f"{PAGEVIEWS}/{project}/all-access/user/{article}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00"


def window(day: Any) -> tuple[date, date]:
    """The refetched days before the bound cycle's day (its input_version component)."""
    end = (day if isinstance(day, date) else date.fromisoformat(str(day)[:10])) - timedelta(days=1)
    return end - timedelta(days=REFETCH_DAYS - 1), end


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def body(ctx: Ctx, response: Any, surface: str, path: str) -> Any:
    """The JSON envelope at `path`, or None after an envelope miss has rejected this input (the derived
    path decides whether misses in a row are surface drift). A soft block the check detects still
    stops the run."""
    try:
        value = response.json()
    except ValueError:
        value = {}
    try:
        return ctx.require(value, path, surface)
    except ServiceError as exc:
        if exc.error_class != "envelope_mismatch":
            raise
        return None


async def sitelinks(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    """Per input QID, the Wikipedia articles of its item, and one lookup row with their count (0 for an item
    with none). A merged item's articles land under the requested QID with the item it resolved to. A
    missing item or a failed request lands no lookup row, so SQL never reads it as having no article."""
    for row in rows:
        qid = str(row["qid"])
        response = await ctx.http.get(WIKIDATA, params=sitelinks_params(qid))
        entities = body(ctx, response, "wiki_sitelinks", "entities")
        if entities is None:
            continue
        entity = entities.get(qid) or next(iter(entities.values()), None) if isinstance(entities, dict) else None
        if not isinstance(entity, dict) or "missing" in entity:
            ctx.observed(1)
            ctx.reject({"qid": qid}, reason="not_found")
            continue
        observed_at = datetime.now(timezone.utc)
        entity_qid, articles = str(entity.get("id") or qid), 0
        for site, link in sorted((entity.get("sitelinks") or {}).items()):
            name = project(site)
            if name and isinstance(link, dict) and link.get("title"):
                articles += 1
                ctx.emit("raw.wiki_sitelinks", {
                    "qid": qid, "entity_qid": entity_qid, "site": site,
                    "project": name, "title": link["title"], "observed_at": observed_at,
                })
        ctx.emit("raw.wiki_lookups", {"qid": qid, "entity_qid": entity_qid, "articles": articles,
                                      "observed_at": observed_at})


async def pageviews(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    """Per input article, its human daily views over the refetch window, one row for every day of it through
    the newest day the run has seen published: a day the response leaves out had no views, and a 404 is
    Wikimedia's answer for a window with none. Before any response has carried a day, nothing is known to be
    published and a 404 lands nothing; a later cycle refetches the window."""
    for row in rows:
        start, end = window(row["pageview_day"])
        response = await ctx.http.get(
            pageviews_url(row["project"], row["title"], start, end), extensions={"mdp_accept": (404,)}
        )
        observed_at = datetime.now(timezone.utc)
        if response.status_code == 404:
            items: Any = []
        else:
            items = body(ctx, response, "wiki_pageviews", "items")
            if items is None:
                continue
        if not isinstance(items, list) or any(
            not isinstance(i, dict) or i.get("agent") != "user" or not str(i.get("timestamp") or "")[:8].isdigit()
            or not isinstance(i.get("views"), int) for i in items
        ):
            # One odd item rejects the input whole, so no partial window lands beside a retry.
            ctx.observed(1)
            ctx.reject({"qid": row["qid"], "project": row["project"]}, reason="drift:wiki_pageviews:item")
            continue
        views = {date.fromisoformat(f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}-{i['timestamp'][6:8]}"): i["views"]
                 for i in items}
        published = max([d for d in (PUBLISHED.get(ctx), *views) if d is not None], default=None)
        if published is None:
            continue
        PUBLISHED[ctx] = published
        for day in days(start, min(end, published)):
            ctx.emit("raw.wiki_pageviews", {
                "qid": str(row["qid"]), "project": row["project"], "title": row["title"],
                "date": day, "views": views.get(day, 0), "observed_at": observed_at,
            })
