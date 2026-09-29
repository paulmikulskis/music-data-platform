"""Public playlist envelopes and shared, deliberately narrow landing schemas."""

import asyncio
import base64
import functools
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from mdp_functions import owners
from mdp_functions.errors import ServiceError

UA = "MusicDataPlatform/1.0"


class PlaylistSnapshot(BaseModel):
    model_config = {
        "json_schema_extra": {
            "non_personal": [
                "platform",
                "playlist_id",
                "variant",
                "stream",
                "observation_group",
                "snapshot_id",
                "position",
                "observed_at",
                "title",
                "owner_class",
                "owner_class_observed",
                "followers",
                "track_count_reported",
                "items_observed",
                "coverage",
                "observation",
                "content_ref",
                "platform_version",
                "snapshot_hash",
                "membership_hash",
                "content_hash",
                "etag",
                "continuation",
                "fetch_surface",
                "tier",
                "cadence",
            ]
        }
    }
    platform: str
    playlist_id: str
    variant: str
    stream: Literal["head", "full"] = "full"
    observation_group: str | None = None
    snapshot_id: str
    position: int = 0  # common multi-output manifest key; items start at 1
    observed_at: datetime
    title: str | None = None
    description: str | None = Field(default=None, json_schema_extra={"explore": "omit"})
    owner_id: str | None = Field(
        default=None, json_schema_extra={"explore": "pseudonym"}
    )
    owner_name: str | None = Field(default=None, json_schema_extra={"explore": "omit"})
    owner_class: str = (
        "unknown"  # the target's frozen label when it has one, else the observed class
    )
    # The class the parser observed in the payload. Every owner decision (name, algotorial, editorial)
    # reads it, never the label; staging derives it from payload evidence for rows landed before it.
    owner_class_observed: str | None = None
    followers: int | None = None
    track_count_reported: int | None = None
    items_observed: int = 0
    coverage: Literal["full", "partial", "header_only"]
    observation: Literal["content", "unchanged"] = "content"
    content_ref: str | None = None
    platform_version: str | None = None
    snapshot_hash: str | None = None  # legacy membership alias
    membership_hash: str | None = None
    content_hash: str | None = None
    canonical_url: str | None = Field(
        default=None, json_schema_extra={"explore": "omit"}
    )
    etag: str | None = None
    continuation: bool = False
    attributes: list[dict[str, str]] = Field(default_factory=list)
    fetch_surface: str
    tier: str = "direct"
    # The observing source's frozen cadence: the target's `cadence` for its playlist
    # collectors, its `render_cadence` for renders. Stream freshness reads it.
    cadence: Literal["daily", "weekly"] | None = None


class PlaylistItem(BaseModel):
    model_config = {
        "json_schema_extra": {
            "non_personal": [
                "platform",
                "playlist_id",
                "variant",
                "stream",
                "observation_group",
                "snapshot_id",
                "observed_at",
                "position",
                "item_type",
                "platform_item_id",
                "featured_track_id",
                "platform_track_id",
                "platform_row_id",
                "occurrence",
                "occurrence_key",
                "occurrence_inferred",
                "title",
                "artist_names",
                "platform_artist_ids",
                "platform_album_id",
                "duration_ms",
                "is_explicit",
                "playcount",
                "added_at",
                "isrc",
                "fetch_surface",
            ]
        }
    }
    platform: str
    playlist_id: str
    variant: str
    stream: Literal["head", "full"] = "full"
    observation_group: str | None = None
    snapshot_id: str
    observed_at: datetime
    position: int = Field(ge=1)
    item_type: Literal["track", "album", "package"] | None = "track"
    platform_item_id: str | None = None
    featured_track_id: str | None = None
    platform_track_id: str | None = None
    platform_row_id: str | None = None
    occurrence: int
    occurrence_key: str
    occurrence_inferred: bool
    title: str
    artist_names: list[str] = Field(default_factory=list)
    platform_artist_ids: list[str] = Field(default_factory=list)
    platform_album_id: str | None = None
    duration_ms: int | None = None
    is_explicit: bool | None = None
    playcount: int | None = None
    added_at: datetime | None = None
    added_by: str | None = Field(
        default=None, json_schema_extra={"explore": "pseudonym"}
    )
    isrc: str | None = None
    fetch_surface: str


# Row kinds known not to be tracks: Spotify episodes (page `__typename`, embed URI),
# Apple music videos, and SoundCloud entities other than tracks. Only these are
# unsupported; any other kind may be a renamed track kind, so it is rejected and counts.
NON_TRACK_KINDS = frozenset(
    {"Episode", "episode", "musicVideo", "playlist", "system-playlist", "user"}
)


ITEM_EXCLUSIONS = {"NotFound": "unavailable_track"}


DECLARATION = {
    "exclusion_reasons": tuple(f"unsupported_item:{kind}" for kind in sorted(NON_TRACK_KINDS))
    + tuple(ITEM_EXCLUSIONS.values()),
    "writes": ["raw.playlist_snapshots", "raw.playlist_items"],
    "cadence": "daily",
    "key": ["platform", "playlist_id", "variant", "stream", "snapshot_id", "position"],
    "schema": {
        "raw.playlist_snapshots": PlaylistSnapshot,
        "raw.playlist_items": PlaylistItem,
    },
    "keep_payload": False,
}


class PlaylistCandidate(BaseModel):
    model_config = {
        "json_schema_extra": {
            "non_personal": [
                "platform",
                "playlist_id",
                "variant",
                "stream",
                "snapshot_id",
                "position",
                "observed_at",
                "discovered_via",
                "via_ref",
                "hint_title",
                "hint_followers",
                "hint_track_count",
                "hint_version",
                "fetch_surface",
            ]
        }
    }
    """One playlist a discovery surface listed, keyed like an observation row."""

    platform: str
    playlist_id: str
    variant: str = ""
    stream: str = "candidate"
    snapshot_id: str  # the listing fetch that carried it
    position: int = Field(ge=1)  # rank within that listing
    observed_at: datetime
    discovered_via: str
    via_ref: str
    hint_title: str | None = None
    hint_owner_id: str | None = Field(
        default=None, json_schema_extra={"explore": "pseudonym"}
    )
    hint_owner_name: str | None = Field(
        default=None, json_schema_extra={"explore": "omit"}
    )
    hint_followers: int | None = None
    hint_track_count: int | None = None
    hint_version: str | None = None
    fetch_surface: str


CANDIDATES = {
    "writes": ["raw.playlist_candidates"],
    "key": ["platform", "playlist_id", "variant", "stream", "snapshot_id", "position"],
    "schema": {"raw.playlist_candidates": PlaylistCandidate},
    "keep_payload": False,
}


class EnvelopeError(ValueError):
    pass


class UnsupportedItem(EnvelopeError):
    """A known non-track kind, or an unavailable item verified by the parser."""

    def __init__(self, kind, *, unavailable=False):
        kind = str(kind)
        super().__init__(kind)
        self.known = kind in NON_TRACK_KINDS or (unavailable and kind in ITEM_EXCLUSIONS)
        self.kind = kind if re.fullmatch(r"[A-Za-z_-]{1,32}", kind) else "other"


# Rejections above this share of supported rows, or exclusions above this share
# of all rows, count as drift.
DRIFT_SHARE = 0.5


CADENCE_HOURS = {"daily": 24, "weekly": 168}


def frozen_cadence(target) -> str:
    """The cadence frozen in the exported spec. Specs frozen before per-target
    cadence stay on the daily collector they always ran on."""
    cadence = (target.get("params_json") or {}).get("cadence")
    return cadence if cadence in CADENCE_HOURS else "daily"


def weekday_bucket(target) -> int:
    """The weekday (UTC, Monday 0) a weekly target is fetched on: frozen in its spec,
    otherwise the same hash of its target id the seed freezes."""
    bucket = (target.get("params_json") or {}).get("weekday_bucket")
    if type(bucket) is int and 0 <= bucket < 7:
        return bucket
    return int(hashlib.sha1(str(target["id"]).encode()).hexdigest(), 16) % 7


def served_cadence(ctx) -> str:
    """A `<key>_weekly` source serves weekly targets; every other key daily ones."""
    return "weekly" if ctx.manifest.source_key.endswith("_weekly") else "daily"


def cadence_matches(ctx, target, today=None) -> bool:
    """Weekly sources run every day and fetch only the weekly targets whose frozen
    bucket is the bound cycle's weekday, so the weekly set spreads across the week and
    a batch that crosses UTC midnight, or retries the next day, keeps its bucket."""
    served = served_cadence(ctx)
    if frozen_cadence(target) != served:
        return False
    if served == "daily" or getattr(ctx, "is_probe", False):
        return True
    today = today or getattr(ctx, "cycle_opened_at", None) or datetime.now(timezone.utc)
    return weekday_bucket(target) == today.astimezone(timezone.utc).weekday()


async def off_loop(fn, *args):
    """Every CPU-bound parse runs here, never on the service's event loop."""
    return await asyncio.to_thread(fn, *args)


# A transport knob that cannot serve a target's frozen market (a residential
# `proxy_country` that disagrees with it, or a target with no market) refuses that
# target only; the run goes on with the rest of the batch.
TARGET_REFUSALS = ("variant_mismatch", "proxy_market_required")


def per_target(collector):
    """Run a collector one target at a time, rejecting a refused target in place."""

    @functools.wraps(collector)
    async def each(ctx, batch, *args, **kwargs):
        for target in batch:
            try:
                await collector(ctx, [target], *args, **kwargs)
            except ServiceError as exc:
                if exc.error_class not in TARGET_REFUSALS:
                    raise
                ctx.observed(1)
                ctx.reject(
                    sanitized_identifiers(
                        {"playlist_id": str(target.get("platform_account_id", ""))}
                    ),
                    reason=exc.error_class,
                )

    return each


def envelope_miss(ctx, surface, path):
    if hasattr(ctx.http, "envelope_miss"):
        ctx.http.envelope_miss(surface, path)


# A parser raising one of these means the surface is not the shape it reads.
PARSE_ERRORS = (
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    IndexError,
    StopIteration,
)
OWNER_CLASSES = {
    "editorial",
    "dsp_algorithmic",
    "chart",
    "curator",
    "user",
    "artist",
    "label",
}
# Only an owner observed to be the platform's own account keeps its name. Each parser classes the
# owner from the payload: Spotify's `spotify` account, Apple's `appleCurator` link, SoundCloud's
# own account or a system playlist, Bandcamp's own lists. A target's label
# never counts. Any other owner may be a private person: its name never lands, and staging
# pseudonymises its id from the same evidence (mdp_playlist_snapshot_rows).
PUBLIC_OWNERS = set(owners.PUBLIC_OWNERS)
CONTENT_FIELDS = (
    "position",
    "occurrence_key",
    "platform_row_id",
    "occurrence",
    "occurrence_inferred",
    "item_type",
    "platform_item_id",
    "featured_track_id",
    "platform_track_id",
    "title",
    "artist_names",
    "platform_artist_ids",
    "platform_album_id",
    "isrc",
    "duration_ms",
    "is_explicit",
    "playcount",
    "added_at",
    "added_by",
)


def required(value, path):
    if value is None:
        raise EnvelopeError(path)
    return value


def script(soup, selector, encoded=False):
    tag = soup.select_one(selector)
    if tag is None:
        raise EnvelopeError(selector)
    try:
        return json.loads(base64.b64decode(tag.text) if encoded else tag.text)
    except (ValueError, UnicodeError) as exc:
        raise EnvelopeError(selector) from exc


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def apple_ids(value, kind):
    return [
        str(
            d["identifiers"].get("storeAdamID")
            or d["identifiers"].get("socialProfileID")
        )
        for d in walk(value)
        if d.get("kind") == kind
        and (
            d.get("identifiers", {}).get("storeAdamID")
            or d.get("identifiers", {}).get("socialProfileID")
        )
    ]


def decode(soup, surface):
    """The one JSON document each surface ships; every later read uses it."""
    if surface == "am_playlist":
        return script(soup, "script#serialized-server-data")
    if surface == "sp_playlist_embed":
        return script(soup, "script#__NEXT_DATA__")
    return script(soup, "script#initialState", encoded=True)


def parse_apple(root, soup):
    data = root["data"][0]["data"]
    sections = data["sections"]
    header = next(
        (
            s["items"][0]
            for s in sections
            if s.get("itemKind") == "containerDetailHeaderLockup"
        ),
        None,
    )
    header = required(header, "data[0].data.sections.header")
    tracks = next(
        (s for s in sections if s.get("id", "").startswith("track-list -")), None
    )
    required(tracks, "data[0].data.sections.track-list")
    links = header.get("subtitleLinks") or []
    curator = next((d for d in links if apple_ids(d, "appleCurator")), None)
    # A listener profile: a socialProfile link, or a socialProfileID in another descriptor kind.
    social = apple_ids(header, "socialProfile") or [
        str(d["socialProfileID"]) for d in walk(header) if d.get("socialProfileID")
    ]
    owner_ids = (apple_ids(curator, "appleCurator") if curator else []) or social
    playlist_id = str(
        ((header.get("contentDescriptor") or {}).get("identifiers") or {}).get(
            "storeAdamID"
        )
        or tracks["id"].split(" - ", 1)[-1]
    )
    continuation = any(
        bool(d.get("nextIntent") or d.get("continuationIntent")) for d in walk(data)
    )
    published = None
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            published = next(
                (
                    d["datePublished"]
                    for d in walk(json.loads(tag.text))
                    if d.get("datePublished")
                ),
                published,
            )
        except ValueError:
            pass
    # The observed owner comes from the link that is the evidence. A `pl.u-` library list,
    # or a header with any listener profile, is a user's. On a catalog list, Apple's own curator link
    # (the appleCurator kind), or the bare "Apple Music" link with a JSON-LD date, is the platform's, and
    # a list whose rows carry a rank is its chart. Anything else is unknown, which a label may fill.
    # Only the evidence link names the owner, never whichever link comes first.
    platform_link = next(
        (
            d
            for d in links
            if d.get("title") == owners.APPLE_PLATFORM_OWNER
            and not apple_ids(d, "appleCurator")
        ),
        None,
    )
    catalog = re.fullmatch(owners.APPLE_CATALOG_ID, playlist_id) is not None
    evidence = None
    if playlist_id.startswith(owners.APPLE_LIBRARY_PREFIX) or social:
        owner_class = "user"
    elif catalog and curator is not None:
        evidence = curator
    elif catalog and platform_link is not None and published is not None:
        evidence = platform_link
    else:
        owner_class = "unknown"
    if evidence is not None:
        ranked = any(t.get("rankingText") for t in tracks.get("items") or [])
        owner_class = "chart" if ranked else "editorial"
    return {
        "title": header["title"],
        "owner_id": owner_ids[0] if owner_ids else None,
        "owner_class": owner_class,
        "owner_name": evidence.get("title") if evidence is not None else None,
        # Metadata and validator evidence only; continuation decides completeness.
        "track_count_reported": header["trackCount"],
        "continuation": continuation,
        "coverage": "partial" if continuation else "full",
        "platform_version": published,
        "canonical_url": data.get("canonicalURL"),
    }


def parse_item(t, surface):
    if surface == "am_playlist":
        return {
            "platform_track_id": str(
                t["contentDescriptor"]["identifiers"]["storeAdamID"]
            ),
            "title": t["title"],
            "duration_ms": t.get("duration"),
            "artist_names": [
                v["title"] for v in t.get("subtitleLinks", []) if v.get("title")
            ],
            "platform_artist_ids": apple_ids(t.get("subtitleLinks"), "artist"),
            "platform_album_id": next(
                iter(apple_ids(t.get("tertiaryLinks"), "album")), None
            ),
        }
    if surface == "sp_playlist_embed":
        return {
            "platform_track_id": t["uri"].rsplit(":", 1)[-1],
            "platform_row_id": t.get("uid"),
            "title": t["title"],
            "artist_names": t["subtitle"].split(", "),
            "duration_ms": t["duration"],
            "is_explicit": t["isExplicit"],
        }
    t = t["itemV2"]["data"]
    return {
        "platform_track_id": t["uri"].rsplit(":", 1)[-1],
        "title": t["name"],
        "duration_ms": t["duration"]["totalMilliseconds"],
        "platform_album_id": t["albumOfTrack"]["uri"].rsplit(":", 1)[-1],
        "artist_names": [a["profile"]["name"] for a in t["artists"]["items"]],
        "platform_artist_ids": [
            a["uri"].rsplit(":", 1)[-1] for a in t["artists"]["items"]
        ],
        "playcount": int(t["playcount"]) if t.get("playcount") is not None else None,
    }


def sanitized_identifiers(value):
    result = {}
    for key in ("playlist_id", "variant", "position"):
        item = value.get(key)
        if key == "position":
            if type(item) is int and item > 0:
                result[key] = item
        elif isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", item):
            result[key] = item
    return result


def sanitize_playlist_output(row):
    """One output boundary for all playlist surfaces; never retain arbitrary attributes."""
    row = dict(row)
    attributes = []
    kinds = {
        "isAlgotorial": "bool",
        "editorial_series": "text",
        "editorialSeries": "text",
        "last_updated": "text",
        "rank_type": "text",
        "date_published": "text",
        "new_entries_count": "int",
        "artistGid": "id",
        "autoplay": "id",
        "autoplay_id": "id",
    }
    candidates = row.get("attributes")
    for attr in candidates if isinstance(candidates, list) else []:
        if not isinstance(attr, dict):
            continue
        key, value = attr.get("key"), attr.get("value")
        if not isinstance(key, str):
            continue
        if (
            isinstance(value, str)
            and urlparse(value).scheme
            and not (
                key in ("autoplay", "autoplay_id")
                and re.fullmatch(r"spotify:playlist:[A-Za-z0-9]+", value)
            )
        ):
            continue
        kind = kinds.get(key)
        if kind == "bool" and (type(value) is bool or value in ("true", "false")):
            value = str(value).lower()
        elif kind == "int" and (
            type(value) is int or isinstance(value, str) and value.isdigit()
        ):
            value = str(value) if int(value) >= 0 else None
        elif kind in ("text", "id") and isinstance(value, str):
            pattern = (
                r"[A-Za-z0-9_.:-]{1,128}"
                if kind == "id"
                else r"[A-Za-z0-9 _.,:+-]{1,128}"
            )
            value = value if re.fullmatch(pattern, value) else None
        else:
            value = None
        if value is not None:
            attributes.append({"key": key, "value": value})
    if "attributes" in row:
        row["attributes"] = attributes
    return sanitize_strings(row)


# Credential shapes, redacted wherever they appear in a retained string. Ordinary
# words stay intact ("Session: Live"); only header lines, bearer values, URLs with a
# query or userinfo, bare query strings, and credential-named key=value pairs go.
SECRETS = (
    re.compile(
        r"(?i)\b(?:proxy-authorization|authorization|set-cookie|cookie)\s*:[^\r\n]*"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*"),
    re.compile(r"(?i)(?:\b[a-z][a-z0-9+.-]*:)?//[^\s<>\"']*[?@][^\s<>\"']*"),
    re.compile(r"\?[^\s<>\"'?=]+=[^\s<>\"']*"),
    re.compile(
        r"(?i)\b[\w.-]*(?:token|secret|signature|passw(?:or)?d|credential|session"
        r"|cookie|auth|sig|api_?key)[\w.-]*=[^\s;,&]+"
    ),
    re.compile(
        r"(?i)\b[\w.-]*(?:token|passw(?:or)?d|credential|api_?key)[\w.-]*\s*:\s*"
        r"[A-Za-z0-9._~+/=-]{12,}"
    ),
)


def sanitize_strings(value):
    """Scrub every retained string, including strings nested in lists and mappings."""
    if isinstance(value, str):
        for pattern in SECRETS:
            value = pattern.sub("[redacted]", value)
        return value
    if isinstance(value, dict):
        return {k: sanitize_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_strings(v) for v in value]
    return value


def parse_embed(root):
    entity = root["props"]["pageProps"]["state"]["data"]["entity"]
    tracks = entity["trackList"]
    if not isinstance(tracks, list):
        raise EnvelopeError("entity.trackList")
    # The head stream is complete when every row parsed and nothing continues; its
    # extent comes from the paired page total in SQL, never from this count.
    continuation = bool(entity.get("next") or entity.get("continuation"))
    return {
        "title": entity["title"],
        "attributes": entity.get("attributes", []),
        "owner_class": "dsp_algorithmic"
        if any(
            a.get("key") == "isAlgotorial" and a.get("value") in (True, "true")
            for a in entity.get("attributes", [])
            if isinstance(a, dict)
        )
        else "unknown",
        "track_count_reported": entity.get("totalCount", entity.get("trackCount")),
        "continuation": continuation,
        "coverage": "partial" if continuation else "full",
    }


def parse_page(root, playlist_id):
    entity = root["entities"]["items"]["spotify:playlist:" + playlist_id]
    content = entity["content"]
    owner = entity["ownerV2"]["data"]
    return {
        "title": entity["name"],
        "description": entity.get("description"),
        "followers": entity["followers"],
        "owner_id": owner["username"],
        "owner_class": "editorial"
        if owner["username"] in owners.SPOTIFY_EDITORIAL_IDS
        else "user",
        "owner_name": owner["name"],
        "track_count_reported": content.get("totalCount"),
        "coverage": "full"
        if len(content["items"]) == content.get("totalCount")
        else "partial",
        "continuation": bool(content.get("next") or content.get("continuation")),
    }


def canonical_apple(url, variant, playlist_id):
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "music.apple.com"
        and parsed.path.startswith(f"/{variant}/playlist/")
        and parsed.path.endswith("/" + playlist_id)
        and not parsed.query
        and not parsed.fragment
    )


def upstream_items(root, surface, playlist_id):
    if surface == "am_playlist":
        rows = next(
            s["items"]
            for s in root["data"][0]["data"]["sections"]
            if s.get("id", "").startswith("track-list -")
        )
    elif surface == "sp_playlist_embed":
        rows = root["props"]["pageProps"]["state"]["data"]["entity"]["trackList"]
    else:
        rows = root["entities"]["items"]["spotify:playlist:" + playlist_id]["content"][
            "items"
        ]
    if not isinstance(rows, list):
        raise EnvelopeError("items")
    return rows


def occurrence_identity(raw, surface):
    if surface == "am_playlist":
        descriptor = raw["contentDescriptor"]
        if descriptor.get("kind", "song") != "song":
            raise UnsupportedItem(descriptor["kind"])
        return str(descriptor["identifiers"]["storeAdamID"]), None
    if surface == "sp_playlist_page":
        wrapper = raw["itemV2"]
        data = wrapper["data"]
        if data.get("__typename") == "NotFound":
            raise UnsupportedItem(
                "NotFound",
                unavailable=wrapper.get("__typename") == "TrackResponseWrapper"
                and not has_track_uri(raw),
            )
        raw = data
        if raw.get("__typename", "Track") != "Track":
            raise UnsupportedItem(raw["__typename"])
    parts = raw["uri"].split(":")
    if len(parts) == 3 and parts[1] != "track":
        raise UnsupportedItem(parts[1])
    track = parts[-1]
    if not track:
        raise EnvelopeError("uri")
    return track, raw.get("uid") if surface == "sp_playlist_embed" else None


def has_track_uri(value):
    """Check the whole item, including nested wrapper metadata, before excluding it."""
    if isinstance(value, dict):
        return any(has_track_uri(child) for child in value.values())
    if isinstance(value, list):
        return any(has_track_uri(child) for child in value)
    return isinstance(value, str) and value.startswith("spotify:track:")


def digest(items, fields):
    return hashlib.md5(
        json.dumps(
            [[r.get(k) for k in fields] for r in items], separators=(",", ":")
        ).encode()
    ).hexdigest()


@dataclass
class Observation:
    """What one response yields. `miss` is `record` (no item list) or `header`."""

    url: str
    rows: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[int] = field(default_factory=list)
    unsupported: list[tuple[int, str]] = field(default_factory=list)
    miss: str | None = None
    empty: bool = False
    snapshot: dict[str, Any] | None = None
    unchanged: bool = False

    @property
    def mostly_excluded(self):
        return len(self.unsupported) > DRIFT_SHARE * self.rows


def assemble(
    common,
    url,
    rows,
    header,
    identify,
    fields,
    *,
    owner_class=None,
    cadence=None,
    tier="direct",
    etag=None,
    previous=None,
    count_is_extent=False,
    may_reuse=False,
):
    """Validate and hash one counted upstream list; shared by every platform.

    `rows` is the upstream list, counted before anything else is read, so a header
    failure hides no item. `identify(raw)` returns `(item_type, item_id, row_id)` and
    runs before every other field check, so a rejected duplicate never shifts a later
    inferred occurrence. `fields(raw)` returns the remaining item fields.
    """
    result = Observation(url=url, rows=len(rows))
    try:
        head = header()
        # Decided on the observed owner, before a target's label replaces the class.
        head["owner_class_observed"] = head.get("owner_class", "unknown")
        if head["owner_class_observed"] not in PUBLIC_OWNERS:
            head["owner_name"] = None
        if owner_class in OWNER_CLASSES:
            head["owner_class"] = owner_class
        if cadence:
            head["cadence"] = cadence
        # A header that names a canonical URL has already validated it.
        result.url = head.get("canonical_url") or url
        head = PlaylistSnapshot(**common, **sanitize_playlist_output(head)).model_dump(
            mode="json"
        )
    except PARSE_ERRORS:
        head = None
    counts = Counter()
    for position, raw in enumerate(rows, 1):
        try:
            item_type, item_id, row_id = identify(raw)
            if not item_id:
                raise EnvelopeError("identity")
        except UnsupportedItem as exc:
            if not exc.known:
                result.rejected.append(position)
                continue
            # Known not-a-track rows: the supported membership stays complete.
            result.unsupported.append((position, exc.kind))
            continue
        except PARSE_ERRORS:
            result.rejected.append(position)
            continue
        counts[item_type, item_id] += 1
        nth = counts[item_type, item_id]
        try:
            values = {
                "platform_track_id": item_id if item_type == "track" else None,
                "platform_row_id": row_id,
                **fields(raw),
            }
            item = PlaylistItem(
                **common,
                **values,
                position=position,
                item_type=item_type,
                platform_item_id=item_id,
                occurrence=nth,
                occurrence_key=row_id or f"{item_type}:{item_id}#{nth}",
                occurrence_inferred=not row_id,
            )
        except PARSE_ERRORS:
            result.rejected.append(position)
            continue
        result.items.append(sanitize_playlist_output(item.model_dump(mode="json")))
    if head is None:
        result.miss = "header"
        return result
    snapshot = {k: v for k, v in head.items() if k not in common}
    if (
        result.rejected
        or result.mostly_excluded
        or snapshot.get("continuation")
        or (
            count_is_extent
            and snapshot.get("track_count_reported") is not None
            and snapshot["track_count_reported"]
            != len(result.items) + len(result.unsupported)
        )
    ):
        snapshot["coverage"] = "partial"
    truly_empty = result.rows == 0 and snapshot.get("track_count_reported") == 0
    if not result.items and not truly_empty:
        # A full list with no supported row (every row unsupported, or no entry parsed
        # and no count) proves nothing: partial, and an envelope miss, never a mass
        # removal. A list the platform itself counts as empty is full: its removals are real.
        snapshot["coverage"] = "partial"
        result.empty = not result.rejected
    membership = digest(result.items, ("position", "occurrence_key"))
    content = digest(result.items, CONTENT_FIELDS)
    snapshot.update(
        snapshot_hash=membership,
        membership_hash=membership,
        content_hash=content,
        tier=tier,
        canonical_url=result.url,
        etag=etag,
    )
    previous = previous or {}
    # Only the embed may short-circuit, and only on matching content and completeness.
    result.unchanged = (
        may_reuse
        and not result.rejected
        and content == previous.get("value")
        and previous.get("coverage") == snapshot["coverage"]
        and previous.get("track_count_reported") == snapshot.get("track_count_reported")
        and previous.get("continuation") == snapshot.get("continuation", False)
        and all(r["platform_row_id"] for r in result.items)
    )
    if result.unchanged:
        snapshot.update(observation="unchanged", content_ref=previous["snapshot_id"])
        result.items = []
    snapshot["items_observed"] = len(result.items)
    result.snapshot = PlaylistSnapshot(
        **common, **sanitize_playlist_output(snapshot)
    ).model_dump(mode="json")
    return result


def observe(
    text, surface, common, url, owner_class, previous, tier, etag, cadence=None
):
    """Parse, validate, and hash one Apple or Spotify response.

    Pure and CPU-bound (HTML parse, JSON decode, per-item validation), so collectors
    run it through `off_loop` and keep the service's event loop free.
    """
    playlist_id, variant = common["playlist_id"], common["variant"]
    soup = BeautifulSoup(text, "html.parser")
    try:
        root = decode(soup, surface)
        rows = upstream_items(root, surface, playlist_id)
    except PARSE_ERRORS:
        return Observation(url=url, miss="record")

    def header():
        if surface == "sp_playlist_embed":
            return parse_embed(root)
        if surface == "sp_playlist_page":
            return parse_page(root, playlist_id)
        head = parse_apple(root, soup)
        if head.get("canonical_url") and not canonical_apple(
            head["canonical_url"], variant, playlist_id
        ):
            raise EnvelopeError("canonicalURL")
        return head

    return assemble(
        common,
        url,
        rows,
        header,
        lambda raw: ("track", *occurrence_identity(raw, surface)),
        lambda raw: parse_item(raw, surface),
        owner_class=owner_class,
        cadence=cadence,
        tier=tier,
        etag=etag,
        previous=previous,
        count_is_extent=surface == "sp_playlist_page",
        may_reuse=surface == "sp_playlist_embed",
    )


EMIT_CHUNK = 200


async def account(ctx, result, common, surface):
    """Every upstream row of one observation is yielded or rejected; the snapshot
    row lands last. Returns the emitted snapshot, or None when the header failed.

    Emitting re-sanitizes each row on the event loop, so a long list yields to the
    loop between chunks rather than holding it for the whole list."""
    playlist_id = common["playlist_id"]
    if result.miss == "record":
        envelope_miss(ctx, surface, "record")
        ctx.observed(1)
        ctx.reject(
            {"playlist_id": playlist_id}, reason=f"envelope_mismatch:{surface}:record"
        )
        return None
    supported = result.rows - len(result.unsupported)
    if (
        result.miss
        or result.empty
        or result.mostly_excluded
        or len(result.rejected) > DRIFT_SHARE * supported
    ):
        # Once per target and surface; surface_drift counts distinct targets. A few
        # odd rows are rejected but are not drift; a missing header, most rows, or a
        # list with no usable tracks are. A majority of exclusions cannot prove membership.
        if result.miss:
            path = "header"
        elif result.empty:
            path = "empty"
        elif result.mostly_excluded:
            path = "exclusions"
        else:
            path = "item"
        envelope_miss(ctx, surface, path)
    ctx.observed(1 + (0 if result.unchanged else result.rows))
    for position in result.rejected:
        ctx.reject(
            sanitized_identifiers({**common, "position": position}),
            reason=f"envelope_mismatch:{surface}:item",
        )
    for position, kind in [] if result.unchanged else result.unsupported:
        ctx.exclude(
            sanitized_identifiers({**common, "position": position}),
            reason=ITEM_EXCLUSIONS.get(kind, f"unsupported_item:{kind}"),
        )
    for start in range(0, len(result.items), EMIT_CHUNK):
        for item in result.items[start : start + EMIT_CHUNK]:
            ctx.emit("raw.playlist_items", item)
        await asyncio.sleep(0)
    if result.miss == "header":
        ctx.reject(
            {"playlist_id": playlist_id}, reason=f"envelope_mismatch:{surface}:header"
        )
        return None
    ctx.emit("raw.playlist_snapshots", result.snapshot)
    return result.snapshot


def observation_common(platform, playlist_id, variant, surface, observed_at=None):
    return {
        "platform": platform,
        "playlist_id": playlist_id,
        "variant": variant,
        "stream": "full",
        "observation_group": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "observed_at": observed_at or datetime.now(timezone.utc),
        "fetch_surface": surface,
    }


def reject_status(ctx, playlist_id, surface, status):
    ctx.observed(1)
    ctx.reject(
        {**sanitized_identifiers({"playlist_id": playlist_id}), "status": status},
        reason=f"envelope_mismatch:{surface}:http_status",
    )


@per_target
async def collect_apple(ctx, batch):
    await collect(ctx, batch, "am_playlist")


@per_target
async def collect_spotify(ctx, batch):
    """Embed then page for each target, sharing one observation group."""
    for target in batch:
        if target.platform not in ("spotify", "sp") or not cadence_matches(ctx, target):
            continue
        group = str(uuid4())
        await collect(ctx, [target], "sp_playlist_embed", group)
        await collect(ctx, [target], "sp_playlist_page", group)


async def collect(ctx, batch, surface, observation_group=None):
    apple = surface == "am_playlist"
    for target in batch:
        if target.platform not in (
            ("apple_music", "am") if apple else ("spotify", "sp")
        ) or not cadence_matches(ctx, target):
            continue
        identity = target.platform_account_id.split(":", 1)
        variant, playlist_id = (
            identity if len(identity) == 2 else ("" if apple else "US", identity[0])
        )
        frozen = (target.get("params_json") or {}).get(
            "storefront" if apple else "market"
        )
        # Apple storefronts are lower case, Spotify markets upper case; either way the
        # frozen spec and the account key must name the same variant.
        if not re.fullmatch(r"[a-z]{2}" if apple else r"[A-Z]{2}", variant) or (
            frozen and str(frozen).lower() != variant.lower()
        ):
            ctx.observed(1)
            ctx.reject(
                sanitized_identifiers({"playlist_id": playlist_id, "variant": variant}),
                reason="variant_mismatch",
            )
            continue
        platform, stream = ("apple_music", "full") if apple else ("spotify", "head")
        identity_binding = {
            "platform": platform,
            "playlist_id": playlist_id,
            "variant": variant,
            "stream": stream,
            "fetch_surface": surface,
        }
        root_cursor = ctx.cursor(target) or {}
        cursor = root_cursor.get("surfaces", {}).get(surface, root_cursor)
        if cursor.get("binding") != identity_binding:
            cursor = {}
        validators = cursor.get("validators", {})

        def bound_validator(
            name, validators=validators, identity_binding=identity_binding
        ):
            v = validators.get(name, {})
            if (
                any(v.get(k) != val for k, val in identity_binding.items())
                or not v.get("snapshot_id")
                or not v.get("membership_hash")
                or not v.get("content_hash")
                or not {"track_count_reported", "continuation", "coverage"} <= v.keys()
            ):
                return {}
            return v

        etag, content_hash = bound_validator("etag"), bound_validator("content_hash")
        if apple:
            url = (
                cursor.get("canonical_url")
                or f"https://music.apple.com/{variant}/playlist/{target.handle}/{playlist_id}"
            )
            if not canonical_apple(url, variant, playlist_id):
                ctx.observed(1)
                ctx.reject(
                    {"playlist_id": playlist_id},
                    reason="envelope_mismatch:am_playlist:canonical_url",
                )
                continue
        else:
            url = f"https://open.spotify.com/{'embed/' if surface == 'sp_playlist_embed' else ''}playlist/{playlist_id}"
        headers = {"User-Agent": UA}
        if apple and etag.get("value"):
            headers["If-None-Match"] = etag["value"]
        response = await ctx.http.get(url, headers=headers, follow_redirects=False)
        if apple and response.status_code == 301:
            redirected = response.headers.get("location", "")
            if canonical_apple(redirected, variant, playlist_id):
                url = redirected
                response = await ctx.http.get(
                    url, headers=headers, follow_redirects=False
                )
        common = {
            "platform": platform,
            "playlist_id": playlist_id,
            "variant": variant,
            "stream": stream,
            "observation_group": observation_group or str(uuid4()),
            "snapshot_id": str(uuid4()),
            "observed_at": datetime.now(timezone.utc),
            "fetch_surface": surface,
        }
        if response.status_code == 304:
            ctx.observed(1)
            if not apple or "If-None-Match" not in headers:
                ctx.reject(
                    {"playlist_id": playlist_id, "status": 304},
                    reason=f"envelope_mismatch:{surface}:unbound_304",
                )
                continue
            ctx.emit(
                "raw.playlist_snapshots",
                PlaylistSnapshot(
                    **common,
                    coverage="header_only",
                    observation="unchanged",
                    content_ref=etag["snapshot_id"],
                    snapshot_hash=etag.get("membership_hash"),
                    membership_hash=etag.get("membership_hash"),
                    content_hash=etag.get("content_hash"),
                    track_count_reported=etag.get("track_count_reported"),
                    continuation=etag.get("continuation", False),
                    tier=response.extensions.get("mdp_tier", "direct"),
                    canonical_url=url,
                    etag=etag["value"],
                    cadence=frozen_cadence(target),
                ).model_dump(mode="json"),
            )
            continue
        if response.status_code != 200:
            ctx.observed(1)
            ctx.reject(
                {"playlist_id": playlist_id, "status": response.status_code},
                reason=f"envelope_mismatch:{surface}:http_status",
            )
            continue
        result = await off_loop(
            observe,
            response.text,
            surface,
            common,
            url,
            (target.get("params_json") or {}).get("owner_class"),
            content_hash,
            response.extensions.get("mdp_tier", "direct"),
            response.headers.get("etag"),
            frozen_cadence(target),
        )
        snapshot = await account(ctx, result, common, surface)
        if snapshot is None:
            continue
        binding = {
            "snapshot_id": snapshot["content_ref"]
            if result.unchanged
            else common["snapshot_id"],
            **identity_binding,
            "coverage": snapshot["coverage"],
            "track_count_reported": snapshot.get("track_count_reported"),
            "continuation": snapshot.get("continuation", False),
            "membership_hash": snapshot["membership_hash"],
            "content_hash": snapshot["content_hash"],
        }
        validators = {
            "content_hash": {"value": snapshot["content_hash"], **binding},
            "snapshot_hash": {"value": snapshot["membership_hash"], **binding},
        }
        if apple and snapshot.get("etag"):
            validators["etag"] = {"value": snapshot["etag"], **binding}
        saved = sanitize_strings(
            {
                "binding": identity_binding,
                "canonical_url": result.url,
                "validators": validators,
            }
        )
        if observation_group:
            surfaces = dict(root_cursor.get("surfaces", {}))
            surfaces[surface] = saved
            ctx.set_cursor(target, {"surfaces": surfaces})
        else:
            ctx.set_cursor(target, saved)
