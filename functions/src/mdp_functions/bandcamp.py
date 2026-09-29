"""Bandcamp rankings, editorial lists, radio, fan playlists, and release pages.

Rankings and editorial lists keep the observed item's type, identity, and rank;
`int_bandcamp__release_tracks` relates releases to their recordings in SQL, never
as a track list membership. Stream URLs (`trackinfo[].file`, player stream fields) and
personal profile fields are never read into a row.
"""

import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from pydantic import BaseModel

from mdp_functions import owners
from mdp_functions.errors import ServiceError
from mdp_functions.playlist import (
    DRIFT_SHARE,
    PARSE_ERRORS,
    UA,
    EnvelopeError,
    UnsupportedItem,
    account,
    assemble,
    cadence_matches,
    envelope_miss,
    frozen_cadence,
    observation_common,
    off_loop,
    per_target,
    reject_status,
    sanitize_strings,
    sanitized_identifiers,
)
from mdp_functions.playlist_targets import spec_hash

BANDCAMP = "https://bandcamp.com"
DISCOVER_URL = BANDCAMP + "/api/discover/1/discover_web"
PLAYER_URL = BANDCAMP + "/api/player/2/player_data_web"
ITEM_TYPES = {"a": "album", "t": "track", "p": "package"}
# The owner of every Bandcamp Daily list (staging serves the same name for rows landed before it).
BANDCAMP_DAILY = owners.PLATFORM_OWNER_NAMES["bc_daily_list"]
DEFAULT_SIZE = 500
MAX_PAGES = 40
MAX_RELEASE_PAGES = 40  # album and track pages per artist per run
ARTIST_HOST = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\.bandcamp\.com")
FAN_PLAYLIST = re.compile(r"[A-Za-z0-9_-]{1,64}/playlist/[A-Za-z0-9_-]{1,128}")
HEADERS = {"User-Agent": UA}
# A failed sub-request leaves its list partial; a block still stops the target.
RECOVERABLE = ("vendor_4xx", "vendor_retryable", "stale_target")


def kind_of(target) -> tuple[str, str] | None:
    """`discover`, `daily`, `radio`, or `playlist`, and the platform id after it."""
    if target.platform != "bandcamp":
        return None
    kind, _, rest = str(target.platform_account_id).partition(":")
    return (kind, rest) if rest else None


def params_of(target) -> dict:
    return target.get("params_json") or {}


def seconds_ms(value) -> int | None:
    return round(float(value) * 1000) if value is not None else None


# --- discover rankings ---------------------------------------------------------


def discover_body(spec: dict, size: int) -> dict:
    return {
        "category_id": spec.get("category_id", 0),
        "tag_norm_names": spec.get("tag_norm_names", []),
        "geoname_id": spec.get("geoname_id", 0),
        "slice": spec.get("slice", "top"),
        "time_facet_id": spec.get("time_facet_id"),
        "cursor": "*",
        "size": size,
        "include_result_types": spec.get("include_result_types", ["a", "s"]),
        "followed_bands": False,
    }


def parse_discover(text, common, url, title, owner_class, cadence, tier):
    body = json.loads(text)
    rows = body["results"]
    if not isinstance(rows, list):
        raise EnvelopeError("results")

    def header():
        return {
            "title": title,
            "owner_class": "chart",
            # The batch is the ranking's defined extent: the top `size` items.
            "track_count_reported": body["batch_result_count"],
            "continuation": False,
            "coverage": "full",
        }

    def identify(raw):
        if raw["item_type"] not in ITEM_TYPES:
            raise UnsupportedItem(f"type_{raw['item_type']}")
        return ITEM_TYPES[raw["item_type"]], str(int(raw["item_id"])), None

    def fields(raw):
        featured = raw.get("featured_track") or {}
        return {
            "title": raw["title"],
            "artist_names": [raw.get("album_artist") or raw["band_name"]],
            "platform_artist_ids": [str(raw["band_id"])],
            "featured_track_id": str(featured["id"]) if featured.get("id") else None,
            "duration_ms": seconds_ms(raw.get("duration")),
        }

    return assemble(
        common,
        url,
        rows,
        header,
        identify,
        fields,
        owner_class=owner_class,
        cadence=cadence,
        tier=tier,
    )


@per_target
async def collect_discover(ctx, batch):
    surface = "bc_discover"
    for target in batch:
        kind = kind_of(target)
        if not kind or kind[0] != "discover" or not cadence_matches(ctx, target):
            continue
        playlist_id = str(target.platform_account_id)
        params = params_of(target)
        spec = params.get("spec")
        if not isinstance(spec, dict) or kind[1] != spec_hash(spec):
            ctx.observed(1)
            ctx.reject(
                sanitized_identifiers({"playlist_id": playlist_id}),
                reason="variant_mismatch",
            )
            continue
        response = await ctx.http.post(
            DISCOVER_URL,
            json=discover_body(spec, int(params.get("size", DEFAULT_SIZE))),
            headers=HEADERS,
            follow_redirects=False,
        )
        if response.status_code != 200:
            reject_status(ctx, playlist_id, surface, response.status_code)
            continue
        common = observation_common("bandcamp", playlist_id, "", surface)
        try:
            result = await off_loop(
                parse_discover,
                response.text,
                common,
                DISCOVER_URL,
                params.get("title") or target.get("display_name") or target.handle,
                params.get("owner_class"),
                frozen_cadence(target),
                response.extensions.get("mdp_tier", "direct"),
            )
        except PARSE_ERRORS:
            result = None
        await land(ctx, result, common, surface)


async def land(ctx, result, common, surface):
    """Account one parsed list, or reject the whole record when it never parsed."""
    if result is None:
        envelope_miss(ctx, surface, "record")
        ctx.observed(1)
        ctx.reject(
            {"playlist_id": common["playlist_id"]},
            reason=f"envelope_mismatch:{surface}:record",
        )
        return None
    return await account(ctx, result, common, surface)


# --- Bandcamp Daily lists ------------------------------------------------------


def normal_url(value: str) -> str:
    parsed = urlparse(value.strip())
    return f"https://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def read_daily(text: str) -> tuple[dict, list, dict]:
    """JSON-LD, entry headings in article order, and player infos by release URL."""
    soup = BeautifulSoup(text, "html.parser")
    ld = {}
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(tag.text)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("@type") == "Article":
            ld = value
    if not ld:
        raise EnvelopeError("ld+json")
    infos = {}
    for tag in soup.select("[data-player-infos]"):
        for info in json.loads(tag["data-player-infos"]):
            if isinstance(info, dict) and info.get("tralbum_url"):
                infos.setdefault(normal_url(info["tralbum_url"]), info)
    scope = soup.select_one("article") or soup
    entries = []
    # Intro embeds are not list entries: an entry is a heading that links a release.
    for heading in scope.select("h3"):
        link = next(
            (
                a["href"]
                for a in heading.select("a[href]")
                if re.search(r"/(album|track)/", urlparse(a["href"]).path)
            ),
            None,
        )
        if link:
            entries.append(normal_url(link))
    return ld, entries, infos


def parse_daily(parts, common, url, owner_class, cadence, tier):
    """Multi-part lists fold into one: positions continue across parts in order."""
    lds, rows, infos = [], [], {}
    for text in parts:
        ld, entries, found = read_daily(text)
        lds.append(ld)
        rows += entries
        infos.update(found)

    def header():
        published = lds[0].get("datePublished")
        return {
            "title": lds[0]["headline"],
            "description": lds[0].get("description"),
            # The list's owner is the platform's column; the writer's byline never lands.
            "owner_name": BANDCAMP_DAILY,
            "owner_class": "editorial",
            "platform_version": max(
                ld.get("dateModified") or ld.get("datePublished") or "" for ld in lds
            )
            or None,
            "attributes": [{"key": "date_published", "value": published}]
            if published
            else [],
            "continuation": False,
            "coverage": "full",
        }

    def identify(entry):
        key = infos[entry]["tralbum_key"]
        return ITEM_TYPES[key[0]], str(int(key[1:])), None

    def fields(entry):
        info = infos[entry]
        featured = None
        if info["tralbum_key"].startswith("a"):
            # The player opens on the featured track; its id is the player id.
            featured = next(
                (
                    t["track_id"]
                    for t in info.get("tracklist") or []
                    if info.get("featured_track_number") is not None
                    and t.get("track_number") == info["featured_track_number"]
                ),
                None,
            ) or (
                info["player_id"][1:]
                if str(info.get("player_id", "")).startswith("t")
                else None
            )
        return {
            "title": info["title"],
            "artist_names": [info["band_name"]],
            "platform_artist_ids": [str(info["band_id"])],
            "featured_track_id": str(featured) if featured else None,
        }

    return assemble(
        common,
        url,
        rows,
        header,
        identify,
        fields,
        owner_class=owner_class,
        cadence=cadence,
        tier=tier,
    )


def daily_urls(target) -> list[str]:
    urls = params_of(target).get("urls") or [
        "https://daily.bandcamp.com/" + str(target.handle).strip("/")
    ]
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "daily.bandcamp.com":
            raise EnvelopeError("daily_url")
    return urls


@per_target
async def collect_daily(ctx, batch):
    surface = "bc_daily_list"
    for target in batch:
        kind = kind_of(target)
        if not kind or kind[0] != "daily" or not cadence_matches(ctx, target):
            continue
        playlist_id = str(target.platform_account_id)
        try:
            urls = daily_urls(target)
        except EnvelopeError:
            ctx.observed(1)
            ctx.reject(
                sanitized_identifiers({"playlist_id": playlist_id}),
                reason="variant_mismatch",
            )
            continue
        parts, status, tier = [], 200, "direct"
        for url in urls:
            response = await ctx.http.get(url, headers=HEADERS, follow_redirects=False)
            if response.status_code != 200:
                status = response.status_code
                break
            parts.append(response.text)
            tier = response.extensions.get("mdp_tier", tier)
        if status != 200:
            reject_status(ctx, playlist_id, surface, status)
            continue
        common = observation_common("bandcamp", playlist_id, "", surface)
        try:
            result = await off_loop(
                parse_daily,
                parts,
                common,
                urls[0],
                params_of(target).get("owner_class"),
                frozen_cadence(target),
                tier,
            )
        except PARSE_ERRORS:
            result = None
        await land(ctx, result, common, surface)


# --- radio shows and fan playlists (player_data_web) ------------------------------


def track_fields(t: dict) -> dict:
    album = t.get("album") or {}
    return {
        "title": t["title"],
        "artist_names": [t["artistName"]] if t.get("artistName") else [],
        "platform_artist_ids": [str(t["bandId"])] if t.get("bandId") else [],
        "platform_album_id": str(album["id"]) if album.get("id") else None,
        "duration_ms": seconds_ms(t.get("duration")),
    }


def track_identity(t: dict):
    return "track", str(int(t["id"])), None


def read_tracklist(text: str) -> dict:
    tracklist = json.loads(text)["tracklist"]
    if not isinstance(tracklist.get("tracks"), list):
        raise EnvelopeError("tracklist.tracks")
    return tracklist


def read_fan_page(text: str) -> dict:
    """The playlist page's `data-blob` app data (the element id varies by page)."""
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.select("[data-blob]"):
        app = json.loads(tag["data-blob"]).get("appData")
        if isinstance(app, dict) and "playlistId" in app:
            if not isinstance(app["tracklist"].get("tracks"), list):
                raise EnvelopeError("appData.tracklist.tracks")
            return app
    raise EnvelopeError("data-blob")


async def player_pages(ctx, item_type, item_id, first):
    """Follow `nextCursor` from a first tracklist; stops on an empty or repeated
    page. Returns every track row and whether a continuation was left unread."""
    tracks, tracklist, seen = list(first["tracks"]), first, set()
    for _ in range(MAX_PAGES):
        cursor = tracklist.get("nextCursor")
        if cursor is None or cursor in seen:
            return tracks, False
        seen.add(cursor)
        try:
            response = await ctx.http.post(
                PLAYER_URL,
                json={
                    "item_type": item_type,
                    "item_id": item_id,
                    "next_cursor": cursor,
                },
                headers=HEADERS,
                follow_redirects=False,
            )
        except ServiceError as exc:
            if exc.error_class not in RECOVERABLE:
                raise
            return tracks, True
        if response.status_code != 200:
            return tracks, True
        tracklist = await off_loop(read_tracklist, response.text)
        if not tracklist["tracks"]:
            return tracks, False
        tracks += tracklist["tracks"]
    return tracks, tracklist.get("nextCursor") is not None


def parse_tracklist(tracklist, rows, left, common, url, owner_class, cadence, tier):
    def header():
        attribution = tracklist.get("attribution") or {}
        summary = tracklist.get("tracksSummary") or {}
        radio = tracklist["itemType"] == "radio"
        return {
            "title": tracklist["title"].strip(),
            "description": tracklist.get("description"),
            # A fan playlist's owner is a private person: the account id lands for
            # staging to pseudonymise, and the handle never lands.
            "owner_id": str(attribution["accountId"])
            if attribution.get("accountId")
            else None,
            "owner_name": (attribution.get("name") or tracklist.get("subtitle"))
            if radio
            else None,
            "owner_class": "editorial" if radio else "user",
            "platform_version": tracklist.get("date"),
            "track_count_reported": summary.get("totalCount"),
            "continuation": left,
            "coverage": "partial" if left else "full",
        }

    return assemble(
        common,
        url,
        rows,
        header,
        track_identity,
        track_fields,
        owner_class=owner_class,
        cadence=cadence,
        tier=tier,
        # The list's own count bounds it: fewer rows than `totalCount` is partial.
        count_is_extent=True,
    )


@per_target
async def collect_radio(ctx, batch):
    surface = "bc_radio"
    for target in batch:
        kind = kind_of(target)
        if not kind or kind[0] != "radio" or not cadence_matches(ctx, target):
            continue
        playlist_id = str(target.platform_account_id)
        if not kind[1].isdigit():
            ctx.observed(1)
            ctx.reject(
                sanitized_identifiers({"playlist_id": playlist_id}),
                reason="variant_mismatch",
            )
            continue
        show = int(kind[1])
        response = await ctx.http.post(
            PLAYER_URL,
            json={"item_type": "radio", "item_id": show},
            headers=HEADERS,
            follow_redirects=False,
        )
        if response.status_code != 200:
            reject_status(ctx, playlist_id, surface, response.status_code)
            continue
        common = observation_common("bandcamp", playlist_id, "", surface)
        result = None
        try:
            first = await off_loop(read_tracklist, response.text)
            if first.get("itemId") != show:
                raise EnvelopeError("itemId")
            rows, left = await player_pages(ctx, "radio", show, first)
            result = await off_loop(
                parse_tracklist,
                first,
                rows,
                left,
                common,
                f"{BANDCAMP}/radio?show={show}",
                params_of(target).get("owner_class"),
                frozen_cadence(target),
                response.extensions.get("mdp_tier", "direct"),
            )
        except PARSE_ERRORS:
            result = None
        await land(ctx, result, common, surface)


@per_target
async def collect_fan_playlists(ctx, batch):
    surface = "bc_fan_playlist"
    for target in batch:
        kind = kind_of(target)
        if not kind or kind[0] != "playlist" or not cadence_matches(ctx, target):
            continue
        playlist_id = str(target.platform_account_id)
        handle = str(target.handle).strip("/")
        if not kind[1].isdigit() or not FAN_PLAYLIST.fullmatch(handle):
            ctx.observed(1)
            ctx.reject(
                sanitized_identifiers({"playlist_id": playlist_id}),
                reason="variant_mismatch",
            )
            continue
        url = f"{BANDCAMP}/{handle}"
        response = await ctx.http.get(url, headers=HEADERS, follow_redirects=False)
        if response.status_code != 200:
            reject_status(ctx, playlist_id, surface, response.status_code)
            continue
        common = observation_common("bandcamp", playlist_id, "", surface)
        result = None
        try:
            app = await off_loop(read_fan_page, response.text)
            if str(app["playlistId"]) != kind[1]:
                raise EnvelopeError("playlistId")
            first = app["tracklist"]
            rows, left = await player_pages(ctx, "playlist", int(kind[1]), first)
            result = await off_loop(
                parse_tracklist,
                {**first, "date": app.get("modDate") or first.get("date")},
                rows,
                left,
                common,
                # The page URL names the fan; the stored URL is the playlist's identity.
                f"{BANDCAMP}/playlist/{kind[1]}",
                params_of(target).get("owner_class"),
                frozen_cadence(target),
                response.extensions.get("mdp_tier", "direct"),
            )
        except PARSE_ERRORS:
            result = None
        await land(ctx, result, common, surface)


# --- album and track pages -----------------------------------------------------------


class BandcampRelease(BaseModel):
    """One album or track page, as of its sitemap `lastmod`."""

    page_url: str
    position: int = 0  # common two-table key; tracks start at 1
    observed_at: datetime
    lastmod: str | None = None
    item_type: str
    item_id: str
    band_id: str | None = None
    title: str
    artist: str | None = None
    upc: str | None = None
    isrc: str | None = None
    album_id: str | None = None
    featured_track_id: str | None = None
    num_tracks: int | None = None
    release_date: str | None = None
    publish_date: str | None = None
    mod_date: str | None = None
    label: str | None = None
    fetch_surface: str = "bc_tralbum"


class BandcampTrack(BaseModel):
    """A release's track; the ISRC only when that track's own page carries it."""

    page_url: str
    position: int
    observed_at: datetime
    track_id: str
    album_id: str | None = None
    page_item_type: str
    page_item_id: str
    track_num: int | None = None
    title: str
    duration_ms: int | None = None
    isrc: str | None = None
    band_id: str | None = None
    fetch_surface: str = "bc_tralbum"


RELEASES = {
    "writes": ["raw.bc_releases", "raw.bc_tracks"],
    "key": ["page_url", "position"],
    "schema": {"raw.bc_releases": BandcampRelease, "raw.bc_tracks": BandcampTrack},
    "keep_payload": False,
}


SITEMAP_URL = re.compile(r"<url>(.*?)</url>", re.DOTALL)
SITEMAP_FIELD = re.compile(r"<(loc|lastmod)>\s*([^<]*?)\s*</\1>")


def read_sitemap(text: str, host: str) -> dict:
    """Release URLs on the artist's own host with their `lastmod`."""
    if "<urlset" not in text:
        raise EnvelopeError("urlset")
    found = {}
    for block in SITEMAP_URL.findall(text):
        fields = dict(SITEMAP_FIELD.findall(block))
        if not fields.get("loc"):
            continue
        url = normal_url(html.unescape(fields["loc"]))
        parsed = urlparse(url)
        if parsed.netloc == host and re.match(r"/(album|track)/[^/]+$", parsed.path):
            found[url] = fields.get("lastmod", "")
    return found


def read_release(text: str, url: str, lastmod: str, observed_at: datetime):
    soup = BeautifulSoup(text, "html.parser")
    tag = soup.select_one("[data-tralbum]")
    if tag is None:
        raise EnvelopeError("data-tralbum")
    tralbum = json.loads(tag["data-tralbum"])
    current = tralbum["current"]
    ld = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.text)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("@type") in (
            "MusicAlbum",
            "MusicRecording",
        ):
            ld = value
    item_type = tralbum["item_type"]
    if item_type not in ("album", "track"):
        raise EnvelopeError("item_type")
    item_id = str(int(tralbum["id"]))
    isrc = (current.get("isrc") or ld.get("isrcCode")) if item_type == "track" else None
    label = next(
        (
            r["recordLabel"].get("name")
            for r in ld.get("albumRelease") or []
            if isinstance(r, dict) and isinstance(r.get("recordLabel"), dict)
        ),
        None,
    )
    release = BandcampRelease(
        page_url=url,
        observed_at=observed_at,
        lastmod=lastmod or None,
        item_type=item_type,
        item_id=item_id,
        band_id=str(current["band_id"]) if current.get("band_id") else None,
        title=current["title"],
        artist=tralbum.get("artist"),
        upc=current.get("upc") or None,
        isrc=isrc or None,
        album_id=str(current["album_id"]) if current.get("album_id") else None,
        featured_track_id=str(tralbum["featured_track_id"])
        if tralbum.get("featured_track_id")
        else None,
        num_tracks=ld.get("numTracks"),
        release_date=current.get("release_date"),
        publish_date=current.get("publish_date"),
        mod_date=current.get("mod_date"),
        label=label,
    ).model_dump(mode="json")
    tracks, rejected = [], []
    rows = tralbum["trackinfo"]
    if not isinstance(rows, list):
        raise EnvelopeError("trackinfo")
    for position, info in enumerate(rows, 1):
        try:
            track_id = str(int(info["track_id"] or info["id"]))
            tracks.append(
                BandcampTrack(
                    page_url=url,
                    position=position,
                    observed_at=observed_at,
                    track_id=track_id,
                    album_id=item_id if item_type == "album" else release["album_id"],
                    page_item_type=item_type,
                    page_item_id=item_id,
                    track_num=info.get("track_num"),
                    title=info["title"],
                    duration_ms=seconds_ms(info.get("duration")),
                    isrc=isrc if item_type == "track" and track_id == item_id else None,
                    band_id=release["band_id"],
                ).model_dump(mode="json")
            )
        except PARSE_ERRORS:
            rejected.append(position)
    return release, tracks, rejected


@per_target
async def collect_releases(ctx, batch):
    """Artist sitemap `lastmod` gates album and track page fetches (label sitemaps
    miss releases hosted on artist subdomains, so the gate is per artist). The cursor
    counts each page's failed runs, and a page that has failed waits behind every
    page that has not, so persistent failures never hold all the slots."""
    surface = "bc_tralbum"
    for target in batch:
        host = str(target.handle).lower()
        if target.platform != "bandcamp" or not ARTIST_HOST.fullmatch(host):
            ctx.observed(1)
            ctx.reject(
                {"handle": str(target.handle)[:253]},
                reason=f"envelope_mismatch:{surface}:target",
            )
            continue
        cursor = ctx.cursor(target) or {}
        mine = cursor.get("host") == host
        seen = cursor.get("lastmod", {}) if mine else {}
        failures = cursor.get("failures", {}) if mine else {}
        response = await ctx.http.get(
            f"https://{host}/sitemap.xml", headers=HEADERS, follow_redirects=False
        )
        if response.status_code != 200:
            reject_status(ctx, host, surface, response.status_code)
            continue
        try:
            listed = await off_loop(read_sitemap, response.text, host)
        except PARSE_ERRORS:
            envelope_miss(ctx, surface, "sitemap")
            ctx.observed(1)
            ctx.reject({"host": host}, reason=f"envelope_mismatch:{surface}:sitemap")
            continue
        changed = sorted(
            (u for u, lastmod in listed.items() if seen.get(u) != lastmod),
            key=lambda u: (failures.get(u, 0), "/track/" in u, u),
        )[:MAX_RELEASE_PAGES]
        kept = {u: v for u, v in seen.items() if u in listed}
        failed = {u: n for u, n in failures.items() if u in listed}
        for url in changed:
            failed[url] = failed.get(url, 0) + 1
            try:
                page = await ctx.http.get(url, headers=HEADERS, follow_redirects=False)
            except ServiceError as exc:
                if exc.error_class not in RECOVERABLE:
                    raise
                # The runtime already recorded a gone page.
                if exc.error_class != "stale_target":
                    ctx.observed(1)
                    ctx.reject(
                        {"host": host},
                        reason=f"envelope_mismatch:{surface}:http_status",
                    )
                continue
            if page.status_code != 200:
                ctx.observed(1)
                ctx.reject(
                    {"host": host, "status": page.status_code},
                    reason=f"envelope_mismatch:{surface}:http_status",
                )
                continue
            try:
                release, tracks, rejected = await off_loop(
                    read_release,
                    page.text,
                    url,
                    listed[url],
                    datetime.now(timezone.utc),
                )
            except PARSE_ERRORS:
                envelope_miss(ctx, surface, "record")
                ctx.observed(1)
                ctx.reject({"host": host}, reason=f"envelope_mismatch:{surface}:record")
                continue
            ctx.observed(1 + len(tracks) + len(rejected))
            for position in rejected:
                ctx.reject(
                    {"host": host, "position": position},
                    reason=f"envelope_mismatch:{surface}:item",
                )
            # A few odd track rows are rejected but are not drift; most rows are.
            if len(rejected) > DRIFT_SHARE * (len(tracks) + len(rejected)):
                envelope_miss(ctx, surface, "item")
            for track in tracks:
                ctx.emit("raw.bc_tracks", sanitize_strings(track))
            ctx.emit("raw.bc_releases", sanitize_strings(release))
            if not rejected:
                kept[url] = listed[url]
                del failed[url]
        ctx.set_cursor(target, {"host": host, "lastmod": kept, "failures": failed})
