"""ListenBrainz: popularity totals per act, sitewide weekly charts, fresh releases and Labs similar
artists.

Every call goes to a path on the runtime's ListenBrainz allowlist; the user, listener and donor
endpoints return usernames and are refused at the transport. Commercial use rides on the MetaBrainz
supporter tier, so every key ships `enabled=False` until the purchase is made. Parsers land what they
read and drop the tags (CC BY-NC-SA), including a fresh release's `release_tags`. Totals are cumulative
and refresh on the upstream job's own schedule: SQL takes the difference between changed values.
"""

import json
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel

from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx

API = "https://api.listenbrainz.org"
HOSTS = ["api.listenbrainz.org"]
LABS = "https://labs.api.listenbrainz.org/similar-artists/json"
LABS_HOSTS = ["labs.api.listenbrainz.org"]
POPULARITY = f"{API}/1/popularity/artist/"
SITEWIDE = f"{API}/1/stats/sitewide"
FRESH = f"{API}/1/explore/fresh-releases/"
SITEWIDE_COUNT = 1000
FRESH_DAYS = 3
# Sitewide entity: (path, payload list, id field, name field).
SITEWIDE_ENTITIES = {
    "artist": ("artists", "artists", "artist_mbid", "artist_name"),
    "recording": ("recordings", "recordings", "recording_mbid", "track_name"),
    "release_group": ("release-groups", "release_groups", "release_group_mbid", "release_group_name"),
}


class LbPopularity(BaseModel):
    """An act's cumulative listens and distinct listeners as ListenBrainz last computed them; both null
    when ListenBrainz has no data for the artist."""

    entity_type: str
    mbid: str
    total_listen_count: int | None = None
    total_user_count: int | None = None
    observed_at: datetime


class LbSitewide(BaseModel):
    """One row of a sitewide weekly top list, with the window it covers and when ListenBrainz computed it."""

    entity_type: str
    stats_range: str
    window_start: datetime
    window_end: datetime
    last_updated: datetime
    rank: int
    mbid: str | None = None
    name: str | None = None
    artist_mbids: str | None = None
    listen_count: int
    observed_at: datetime


class LbFreshRelease(BaseModel):
    release_mbid: str
    release_group_mbid: str | None = None
    artist_mbids: str
    artist_credit_name: str | None = None
    release_name: str | None = None
    release_group_primary_type: str | None = None
    release_date: date | None = None
    listen_count: int | None = None
    observed_at: datetime


class LbSimilarArtist(BaseModel):
    """One co-listen neighbour of a reference act under one Labs algorithm, in response order (rank 1 is
    the closest). Labs names, types, genders and comments never land."""

    reference_mbid: str
    neighbour_mbid: str
    score: int
    rank: int
    algorithm: str
    observed_at: datetime


def payload(ctx: Ctx, response: Any, surface: str, path: str) -> Any:
    """The JSON envelope at `path`, or None after an envelope miss has rejected this target or input."""
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


def stamp(seconds: Any) -> datetime:
    return datetime.fromtimestamp(int(seconds), UTC)


async def popularity(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    """Per input act, the artist's totals. The response keeps the request's order, so an answer for
    another MBID is drift."""
    for row in rows:
        mbid = str(row["mb_artist_gid"])
        response = await ctx.http.post(POPULARITY, json={"artist_mbids": [mbid]})
        answers = payload(ctx, response, "lb_popularity", "0")
        if answers is None:
            continue
        if not isinstance(answers, dict) or answers.get("artist_mbid") != mbid:
            ctx.observed(1)
            ctx.reject({"mbid": mbid}, reason="drift:lb_popularity:artist_mbid")
            continue
        ctx.emit("raw.lb_popularity", {
            "entity_type": "artist", "mbid": mbid, "total_listen_count": answers.get("total_listen_count"),
            "total_user_count": answers.get("total_user_count"), "observed_at": datetime.now(UTC),
        })


async def sitewide(ctx: Ctx):
    """The week's sitewide top lists: artists, recordings and release groups, up to 1,000 each."""
    for entity, (path, key, id_field, name_field) in SITEWIDE_ENTITIES.items():
        response = await ctx.http.get(f"{SITEWIDE}/{path}", params={"range": "week", "count": SITEWIDE_COUNT})
        body = payload(ctx, response, "lb_sitewide", "payload")
        if body is None:
            continue
        entries = body.get(key) if isinstance(body, dict) else None
        if not isinstance(entries, list) or not {"from_ts", "to_ts", "last_updated"} <= set(body):
            ctx.observed(1)
            ctx.reject({"entity_type": entity}, reason=f"drift:lb_sitewide:{key}")
            continue
        observed_at = datetime.now(UTC)
        ctx.observed(len(entries))
        for rank, entry in enumerate(entries, 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("listen_count"), int):
                ctx.reject({"entity_type": entity, "rank": rank}, reason=f"drift:lb_sitewide:{key}:listen_count")
                continue
            artists = entry.get("artist_mbids")
            yield {
                "entity_type": entity, "stats_range": body.get("range") or "week",
                "window_start": stamp(body["from_ts"]), "window_end": stamp(body["to_ts"]),
                "last_updated": stamp(body["last_updated"]), "rank": rank,
                "mbid": entry.get(id_field), "name": entry.get(name_field),
                "artist_mbids": json.dumps(artists) if isinstance(artists, list) else None,
                "listen_count": entry["listen_count"], "observed_at": observed_at,
            }


def release_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


async def fresh_releases(ctx: Ctx):
    """Releases dated within FRESH_DAYS of today, with their listen counts; tags never land."""
    response = await ctx.http.get(FRESH, params={"days": FRESH_DAYS})
    body = payload(ctx, response, "lb_fresh_releases", "payload.releases")
    if body is None:
        return
    observed_at = datetime.now(UTC)
    ctx.observed(len(body))
    for release in body:
        if not isinstance(release, dict) or not release.get("release_mbid"):
            ctx.reject({"release_mbid": None}, reason="drift:lb_fresh_releases:release_mbid")
            continue
        yield {
            "release_mbid": release["release_mbid"], "release_group_mbid": release.get("release_group_mbid"),
            "artist_mbids": json.dumps(release.get("artist_mbids") or []),
            "artist_credit_name": release.get("artist_credit_name"), "release_name": release.get("release_name"),
            "release_group_primary_type": release.get("release_group_primary_type"),
            "release_date": release_day(release.get("release_date")),
            "listen_count": release.get("listen_count") if isinstance(release.get("listen_count"), int) else None,
            "observed_at": observed_at,
        }


async def similar_artists(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    """Per input act, its neighbours under the input's algorithm. An act below the listen threshold gets
    an empty list, which lands nothing and completes the input."""
    for row in rows:
        mbid, algorithm = str(row["mb_artist_gid"]), str(row["algorithm"])
        response = await ctx.http.get(LABS, params={"artist_mbids": mbid, "algorithm": algorithm})
        try:
            neighbours = response.json()
        except ValueError:
            neighbours = None
        if not isinstance(neighbours, list):
            payload(ctx, response, "lb_similar_artists", "0")
            continue
        observed_at = datetime.now(UTC)
        for rank, neighbour in enumerate(neighbours, 1):
            if not isinstance(neighbour, dict) or not neighbour.get("artist_mbid") or not isinstance(neighbour.get("score"), int):
                ctx.observed(1)
                ctx.reject({"reference_mbid": mbid, "rank": rank}, reason="drift:lb_similar_artists:neighbour")
                continue
            if neighbour.get("reference_mbid") not in (None, mbid):
                ctx.observed(1)
                ctx.reject({"reference_mbid": mbid, "rank": rank}, reason="drift:lb_similar_artists:reference_mbid")
                continue
            ctx.emit("raw.lb_similar_artists", {
                "reference_mbid": mbid, "neighbour_mbid": neighbour["artist_mbid"], "score": neighbour["score"],
                "rank": rank, "algorithm": algorithm, "observed_at": observed_at,
            })
