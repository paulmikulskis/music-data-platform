"""Offline SoundCloud parsing fixtures.

Live collection is refused before any request. An official, credentialed API
integration is required for live access and is not included in this snapshot.
The synthetic recordings exercise allowlisted row parsing and retry behavior.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlparse
from uuid import uuid4

from pydantic import BaseModel

from mdp_functions import owners
from mdp_functions.control_db import block_host
from mdp_functions.errors import ServiceError
from mdp_functions.playlist import (
    DECLARATION,
    PARSE_ERRORS,
    UA,
    EnvelopeError,
    PlaylistCandidate,
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

API = "https://api-v2.soundcloud.com"
API_HOST = "api-v2.soundcloud.com"
HYDRATION_URL = "https://soundcloud.com/discover"
HOSTS = ["api-v2.soundcloud.com", "soundcloud.com"]
CLIENT_ID = re.compile(r"[A-Za-z0-9]{16,64}")
SYSTEM_URN = re.compile(r"soundcloud:system-playlists:[a-z0-9:_-]{1,96}")
BATCH = 50  # tracks?ids= answers 400 above 50 ids
STALE_DAYS = 30
MAX_PAGES = 20
GATED_BODIES = 10  # extra body fetches per curator per run
SOUNDCLOUD_USER = int(owners.SOUNDCLOUD_ACCOUNT_ID)
# A failed hydration leaves its rows rejected; a block still stops the target.
RECOVERABLE = ("vendor_4xx", "vendor_retryable", "stale_target")


class SoundCloudTrack(BaseModel):
    """Platform track detail from a hydration, keyed by the observation that hydrated it."""

    platform: str = "soundcloud"
    playlist_id: str
    variant: str = ""
    stream: str = "full"
    snapshot_id: str
    position: int  # the track's first position in that observation
    observed_at: datetime
    track_id: str
    urn: str | None = None
    title: str | None = None
    permalink_url: str | None = None
    genre: str | None = None
    label_name: str | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None
    display_date: datetime | None = None
    release_date: datetime | None = None
    last_modified: datetime | None = None
    playback_count: int | None = None
    likes_count: int | None = None
    reposts_count: int | None = None
    comment_count: int | None = None
    monetization_model: str | None = None
    policy: str | None = None
    isrc: str | None = None
    upc: str | None = None
    p_line: str | None = None
    c_line: str | None = None
    release_title: str | None = None
    album_title: str | None = None
    publisher_artist: str | None = None
    writer_composer: str | None = None
    publisher: str | None = None
    explicit: bool | None = None
    uploader_id: str | None = None
    uploader_permalink: str | None = None
    uploader_username: str | None = None
    uploader_verified: bool | None = None
    uploader_followers: int | None = None
    fetch_surface: str = "sc_tracks"


PLAYLISTS = {
    **DECLARATION,
    "writes": [*DECLARATION["writes"], "raw.sc_tracks"],
    "schema": {**DECLARATION["schema"], "raw.sc_tracks": SoundCloudTrack},
}
CURATORS = {
    **PLAYLISTS,
    "writes": [*PLAYLISTS["writes"], "raw.playlist_candidates"],
    "schema": {**PLAYLISTS["schema"], "raw.playlist_candidates": PlaylistCandidate},
}


def hydration_client_id(text: str) -> str:
    """The `apiClient` entry of `window.__sc_hydration`; pure, runs off the loop."""
    start = text.index("window.__sc_hydration")
    start = text.index("[", start)
    entries, _ = json.JSONDecoder().raw_decode(text, start)
    for entry in entries:
        if isinstance(entry, dict) and entry.get("hydratable") == "apiClient":
            value = entry["data"]["id"]
            if isinstance(value, str) and CLIENT_ID.fullmatch(value):
                return value
    raise EnvelopeError("apiClient")


def track_detail(t: dict) -> dict:
    """Allowlisted track fields; tokens, transcodings, and personal fields never pass."""
    meta = t.get("publisher_metadata") or {}
    user = t.get("user") or {}
    if t.get("kind", "track") != "track" or not t.get("title"):
        raise EnvelopeError("track")
    return {
        "track_id": str(t["id"]),
        "urn": t.get("urn"),
        "title": t["title"],
        "permalink_url": t.get("permalink_url"),
        "genre": t.get("genre") or None,
        "label_name": t.get("label_name") or None,
        "duration_ms": t.get("duration"),
        "created_at": t.get("created_at"),
        "display_date": t.get("display_date"),
        "release_date": t.get("release_date"),
        "last_modified": t.get("last_modified"),
        "playback_count": t.get("playback_count"),
        "likes_count": t.get("likes_count"),
        "reposts_count": t.get("reposts_count"),
        "comment_count": t.get("comment_count"),
        "monetization_model": t.get("monetization_model"),
        "policy": t.get("policy"),
        "isrc": meta.get("isrc") or None,
        "upc": meta.get("upc_or_ean") or None,
        "p_line": meta.get("p_line") or None,
        "c_line": meta.get("c_line") or None,
        "release_title": meta.get("release_title") or None,
        "album_title": meta.get("album_title") or None,
        "publisher_artist": meta.get("artist") or None,
        "writer_composer": meta.get("writer_composer") or None,
        "publisher": meta.get("publisher") or None,
        "explicit": meta.get("explicit"),
        "uploader_id": str(user["id"]) if user.get("id") is not None else None,
        "uploader_permalink": user.get("permalink"),
        "uploader_username": user.get("username"),
        "uploader_verified": user.get("verified"),
        "uploader_followers": user.get("followers_count"),
    }


def cache_entry(detail: dict, day: str) -> list:
    """What an item needs from a hydration, kept in the cursor between runs."""
    return [
        detail["title"],
        detail["publisher_artist"] or detail["uploader_username"],
        detail["uploader_id"],
        detail["duration_ms"],
        detail["isrc"],
        day,
    ]


def read_body(text: str, playlist_id: str) -> tuple[dict, list, dict, list]:
    """Header, counted rows, full track details, and rejected full tracks of one body."""
    body = json.loads(text)
    rows = body["tracks"]
    if not isinstance(rows, list):
        raise EnvelopeError("tracks")
    details, broken = {}, []
    for raw in rows:
        if isinstance(raw, dict) and raw.get("title"):
            try:
                detail = track_detail(raw)
                details.setdefault(detail["track_id"], detail)
            except PARSE_ERRORS:
                broken.append(raw.get("id"))
    return body, rows, details, broken


def read_tracks(text: str) -> tuple[dict, list]:
    """Details keyed by track id from one `tracks?ids=` answer, and rejected objects."""
    rows = json.loads(text)
    if not isinstance(rows, list):
        raise EnvelopeError("tracks")
    details, broken = {}, []
    for raw in rows:
        try:
            detail = track_detail(raw)
        except PARSE_ERRORS:
            broken.append(raw.get("id") if isinstance(raw, dict) else None)
            continue
        details[detail["track_id"]] = detail
    return details, broken


def header(body: dict, playlist_id: str) -> dict:
    system = playlist_id.startswith("soundcloud:")
    if str(body["urn" if system else "id"]) != playlist_id:
        raise EnvelopeError("id")
    user = body.get("user") or {}
    return {
        "title": body["title"],
        "description": body.get("description"),
        "owner_id": str(user["id"]) if user.get("id") is not None else None,
        "owner_name": user.get("username"),
        "owner_class": "dsp_algorithmic"
        if system
        else "editorial"
        if user.get("id") == SOUNDCLOUD_USER
        else "unknown",
        "followers": body.get("likes_count"),
        # Metadata only: a body lists every available id in one response.
        "track_count_reported": body.get("track_count"),
        "platform_version": body.get("last_modified") or body.get("last_updated"),
        "continuation": False,
        "coverage": "full",
    }


def build(common, url, rows, body, known, owner_class, cadence, tier):
    def identify(raw):
        if raw.get("kind", "track") != "track":
            raise UnsupportedItem(raw["kind"])
        return "track", str(raw["id"]), None

    def fields(raw):
        title, artist, uploader, duration, isrc, _ = known[str(raw["id"])]
        return {
            "title": title,
            "artist_names": [artist] if artist else [],
            "platform_artist_ids": [uploader] if uploader else [],
            "duration_ms": duration,
            "isrc": isrc,
        }

    result = assemble(
        common,
        url,
        rows,
        lambda: header(body, common["playlist_id"]),
        identify,
        fields,
        owner_class=owner_class,
        cadence=cadence,
        tier=tier,
    )
    # A track row whose hydration failed is rejected on its own reason: it leaves
    # the observation partial but is not drift.
    unhydrated = [
        position
        for position in result.rejected
        if isinstance(raw := rows[position - 1], dict)
        and raw.get("kind", "track") == "track"
        and raw.get("id") is not None
        and str(raw["id"]) not in known
    ]
    result.rejected = [p for p in result.rejected if p not in unhydrated]
    return result, unhydrated


def refuse_auth(ctx):
    """api-v2 refused a freshly hydrated client id: pause its host as `auth_refused`
    and stop, rather than refetch the hydration for every later target."""
    http = ctx.http
    if hasattr(http, "db"):
        block_host(
            http.db, ctx.run_id, API_HOST, "auth_refused", http.settings.host_block_s
        )
    ctx.observed(1)
    ctx.reject({"host": API_HOST}, reason="blocked:auth_refused")
    raise ServiceError("scrape_blocked", "blocked:auth_refused")


class ClientIdError(EnvelopeError):
    """The page hydration did not yield a web client id."""


class Session:
    """One function run's view of the web client id, shared across its targets."""

    def __init__(self, ctx, state: dict):
        if not ctx.fixture:
            raise ServiceError(
                "vendor_4xx",
                "SoundCloud live collection requires an official, credentialed API integration; "
                "the included reader supports offline fixtures only",
            )
        self.ctx, self.state = ctx, state

    async def client_id(self, refresh=False) -> str:
        cached = getattr(self.ctx, "sc_client_id", None) or self.state.get("client_id")
        if cached and not refresh:
            self.ctx.sc_client_id = cached
            return cached
        response = await self.ctx.http.get(
            HYDRATION_URL, headers={"User-Agent": UA}, follow_redirects=False
        )
        if response.status_code != 200:
            raise ClientIdError("hydration_status")
        try:
            value = await off_loop(hydration_client_id, response.text)
        except PARSE_ERRORS as exc:
            raise ClientIdError("apiClient") from exc
        self.ctx.sc_client_id = self.state["client_id"] = value
        return value

    async def get(self, path: str, params: dict | None = None):
        """GET an api-v2 path; a 401 refetches the client id once and retries, and a
        second 401 refuses the run."""
        response = None
        for attempt in range(2):
            client = await self.client_id(refresh=attempt == 1)
            try:
                response = await self.ctx.http.get(
                    API + path,
                    params={**(params or {}), "client_id": client},
                    headers={"User-Agent": UA},
                    follow_redirects=False,
                )
            except ServiceError as exc:
                if exc.error_class == "vendor_4xx" and exc.message == "HTTP 401":
                    if attempt == 0:
                        continue
                    refuse_auth(self.ctx)
                raise
            if response.status_code == 401:
                if attempt == 0:
                    continue
                refuse_auth(self.ctx)
            return response
        return response

    async def pages(self, path: str, params: dict):
        """Linked-partitioning pages; stops on an empty or repeated page, never on
        `next_href` alone (it stays non-null past the end of some windows)."""
        seen = None
        for _ in range(MAX_PAGES):
            response = await self.get(path, params)
            if response.status_code != 200:
                raise EnvelopeError(f"http_{response.status_code}")
            body = await off_loop(json.loads, response.text)
            collection = body["collection"]
            if not isinstance(collection, list):
                raise EnvelopeError("collection")
            ids = [
                c.get("id", c.get("urn")) if isinstance(c, dict) else c
                for c in collection
            ]
            if not collection or ids == seen:
                return
            seen = ids
            yield collection
            following = body.get("next_href")
            if not following:
                return
            parsed = urlparse(following)
            if parsed.netloc != urlparse(API).netloc or parsed.path != path:
                raise EnvelopeError("next_href")
            query = {k: v for k, v in parse_qsl(parsed.query) if k != "client_id"}
            params = {**query, **{k: v for k, v in params.items() if k not in query}}


async def observe_playlist(
    ctx, session, target, playlist_id, cache, owner_class, surface="sc_playlist"
):
    """Fetch one body, hydrate unseen or stale ids, and land a full observation.

    Returns the track cache for the ids now on the playlist and whether a snapshot
    landed. Raises ClientIdError when no web client id can be resolved.
    """
    system = playlist_id.startswith("soundcloud:")
    path = ("/system-playlists/" if system else "/playlists/") + playlist_id
    response = await session.get(path)
    if response.status_code != 200:
        reject_status(ctx, playlist_id, surface, response.status_code)
        return cache, False
    common = observation_common("soundcloud", playlist_id, "", surface)
    try:
        body, rows, details, broken = await off_loop(
            read_body, response.text, playlist_id
        )
    except PARSE_ERRORS:
        envelope_miss(ctx, surface, "record")
        ctx.observed(1)
        ctx.reject(
            {"playlist_id": playlist_id}, reason=f"envelope_mismatch:{surface}:record"
        )
        return cache, False
    today = datetime.now(timezone.utc).date()
    fresh = {
        k: v
        for k, v in cache.items()
        if today - date.fromisoformat(v[-1]) < timedelta(days=STALE_DAYS)
    }
    first = {}
    for position, raw in enumerate(rows, 1):
        if isinstance(raw, dict) and raw.get("id") is not None:
            first.setdefault(str(raw["id"]), position)
    wanted = [i for i in first if i not in details and i not in fresh]
    # Rows left without a hydration are rejected as `unhydrated`; only an answer
    # that does not parse is drift.
    hydrated, drifted = {}, False
    for start in range(0, len(wanted), BATCH):
        try:
            reply = await session.get(
                "/tracks", {"ids": ",".join(wanted[start : start + BATCH])}
            )
        except ServiceError as exc:
            if exc.error_class not in RECOVERABLE:
                raise
            continue
        if reply.status_code != 200:
            continue
        try:
            found, lost = await off_loop(read_tracks, reply.text)
        except PARSE_ERRORS:
            drifted = True
            continue
        hydrated.update(found)
        broken += lost
    if drifted:
        envelope_miss(ctx, "sc_tracks", "item")
    landed = {**details, **hydrated}
    day = today.isoformat()
    known = {**fresh, **{k: cache_entry(v, day) for k, v in landed.items()}}
    result, unhydrated = await off_loop(
        build,
        common,
        f"{API}{path}",
        rows,
        body,
        known,
        owner_class,
        frozen_cadence(target),
        response.extensions.get("mdp_tier", "direct"),
    )
    # Every track object read (from the body or a hydration) lands or is rejected.
    ctx.observed(len(landed) + len(broken))
    for track_id, detail in landed.items():
        if track_id not in first:
            ctx.reject(
                {"playlist_id": playlist_id, "track_id": track_id},
                reason="envelope_mismatch:sc_tracks:unrequested",
            )
            continue
        try:
            row = SoundCloudTrack(
                playlist_id=playlist_id,
                snapshot_id=common["snapshot_id"],
                position=first[track_id],
                observed_at=common["observed_at"],
                **detail,
            ).model_dump(mode="json")
        except PARSE_ERRORS:
            ctx.reject(
                {"playlist_id": playlist_id, "track_id": track_id},
                reason="envelope_mismatch:sc_tracks:item",
            )
            continue
        ctx.emit("raw.sc_tracks", sanitize_strings(row))
    for _ in broken:
        ctx.reject(
            sanitized_identifiers({"playlist_id": playlist_id}),
            reason="envelope_mismatch:sc_tracks:item",
        )
    for position in unhydrated:
        ctx.reject(
            sanitized_identifiers({"playlist_id": playlist_id, "position": position}),
            reason="unhydrated:sc_tracks",
        )
    snapshot = await account(ctx, result, common, surface)
    return {k: v for k, v in known.items() if k in first}, snapshot is not None


def binding(target, surface):
    return {
        "platform": "soundcloud",
        "account": target.platform_account_id,
        "fetch_surface": surface,
    }


def state_for(ctx, target, surface):
    cursor = ctx.cursor(target) or {}
    return cursor if cursor.get("binding") == binding(target, surface) else {}


def save(ctx, target, surface, state, **values):
    ctx.set_cursor(
        target,
        {
            "binding": binding(target, surface),
            "client_id": state.get("client_id"),
            **values,
        },
    )


def playlist_id_of(target) -> str | None:
    account_id = str(target.platform_account_id)
    if account_id.isdigit() or SYSTEM_URN.fullmatch(account_id):
        return account_id
    return None


@per_target
async def collect_playlists(ctx, batch):
    for target in batch:
        if target.platform != "soundcloud" or not cadence_matches(ctx, target):
            continue
        playlist_id = playlist_id_of(target)
        if playlist_id is None:
            ctx.observed(1)
            ctx.reject(
                sanitized_identifiers({"playlist_id": str(target.platform_account_id)}),
                reason="envelope_mismatch:sc_playlist:target",
            )
            continue
        state = state_for(ctx, target, "sc_playlist")
        session = Session(ctx, state)
        try:
            cache, _ = await observe_playlist(
                ctx,
                session,
                target,
                playlist_id,
                state.get("tracks", {}),
                (target.get("params_json") or {}).get("owner_class"),
            )
        except ClientIdError:
            # The client id could not be resolved from the page hydration.
            envelope_miss(ctx, "sc_client_id", "apiClient")
            ctx.observed(1)
            ctx.reject(
                {"playlist_id": playlist_id},
                reason="envelope_mismatch:sc_client_id:record",
            )
            continue
        save(ctx, target, "sc_playlist", state, tracks=cache)


def candidate(item, position, snapshot_id, observed_at, via, ref, surface):
    kind = item.get("kind")
    if kind == "system-playlist":
        playlist_id = str(item.get("urn") or item["id"])
        if not SYSTEM_URN.fullmatch(playlist_id):
            raise EnvelopeError("urn")
    elif kind == "playlist":
        playlist_id = str(int(item["id"]))
    else:
        raise EnvelopeError(f"kind:{kind}")
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    return PlaylistCandidate(
        platform="soundcloud",
        playlist_id=playlist_id,
        snapshot_id=snapshot_id,
        position=position,
        observed_at=observed_at,
        discovered_via=via,
        via_ref=ref,
        hint_title=item.get("title"),
        # A listed playlist carries no owner class, so its owner may be a private person: the
        # name never lands, and staging pseudonymises the id.
        hint_owner_id=str(user["id"]) if user.get("id") is not None else None,
        hint_followers=item.get("likes_count"),
        hint_track_count=item.get("track_count"),
        hint_version=item.get("last_modified") or item.get("last_updated"),
        fetch_surface=surface,
    ).model_dump(mode="json")


@per_target
async def collect_curators(ctx, batch):
    """A curator's mini listing lands as candidates and is the change gate: a
    playlist whose (last_modified, track_count) moved since the previous listing
    gets an extra body fetch, and the gate keeps a moved playlist's old value until
    a body for it lands. A gated body carries the owner class its own header gives,
    never the curator's. Tracked bodies are also fetched unconditionally on their
    frozen cadence by `sc_playlist`."""
    surface = "sc_curator_playlists"
    for target in batch:
        if target.platform != "soundcloud" or not cadence_matches(ctx, target):
            continue
        user_id = str(target.platform_account_id)
        if not user_id.isdigit():
            ctx.observed(1)
            ctx.reject(
                sanitize_strings({"owner_id": user_id[:128]}),
                reason=f"envelope_mismatch:{surface}:target",
            )
            continue
        state = state_for(ctx, target, surface)
        session = Session(ctx, state)
        previous = state.get("gate")
        gate, listing = {}, str(uuid4())
        observed_at, position = datetime.now(timezone.utc), 0
        try:
            async for page in session.pages(
                "/" + f"users/{user_id}/playlists_without_albums",
                {"limit": "50", "linked_partitioning": "1", "representation": "mini"},
            ):
                ctx.observed(len(page))
                for item in page:
                    position += 1
                    try:
                        row = candidate(
                            item,
                            position,
                            listing,
                            observed_at,
                            "sc_curator_listing",
                            user_id,
                            surface,
                        )
                    except PARSE_ERRORS:
                        ctx.reject(
                            {"owner_id": user_id, "position": position},
                            reason=f"envelope_mismatch:{surface}:item",
                        )
                        continue
                    ctx.emit("raw.playlist_candidates", sanitize_strings(row))
                    gate[row["playlist_id"]] = [
                        row["hint_version"],
                        row["hint_track_count"],
                    ]
        except PARSE_ERRORS:
            envelope_miss(ctx, surface, "record")
            ctx.observed(1)
            ctx.reject(
                {"owner_id": user_id}, reason=f"envelope_mismatch:{surface}:record"
            )
            continue
        # The first listing only records the gate; there is no change to act on yet.
        moved = [
            pid
            for pid, value in gate.items()
            if previous is not None and previous.get(pid) != value
        ]
        cache, kept, fetched = state.get("tracks", {}), {}, set()
        for playlist_id in moved[:GATED_BODIES]:
            try:
                tracks, landed = await observe_playlist(
                    ctx, session, target, playlist_id, cache, None
                )
            except ClientIdError:
                break
            kept.update(tracks)
            if landed:
                fetched.add(playlist_id)
        for playlist_id in moved:
            if playlist_id in fetched:
                continue
            if playlist_id in previous:
                gate[playlist_id] = previous[playlist_id]
            else:
                del gate[playlist_id]
        save(ctx, target, surface, state, gate=gate, tracks=kept)


async def collect_hubs(ctx):
    """Editorial selections, Buzzing, charts, and trending shelves as candidates."""
    state = ctx.cursor(None) or {}
    session = Session(ctx, state)
    observed_at = datetime.now(timezone.utc)
    for surface, path in (
        ("sc_hubs_mixed", "/mixed-selections"),
        ("sc_hubs_charts", "/charts/selections"),
    ):
        try:
            async for page in session.pages(path, {"limit": "10"}):
                for selection in page:
                    ref = selection.get("urn") if isinstance(selection, dict) else None
                    items = (selection or {}).get("items")
                    items = (
                        items.get("collection") if isinstance(items, dict) else items
                    )
                    if not ref or not isinstance(items, list):
                        envelope_miss(ctx, surface, "selection")
                        ctx.observed(1)
                        ctx.reject(
                            {"selection": str(ref)},
                            reason=f"envelope_mismatch:{surface}:selection",
                        )
                        continue
                    listing = str(uuid4())
                    ctx.observed(len(items))
                    for position, item in enumerate(items, 1):
                        try:
                            row = candidate(
                                item,
                                position,
                                listing,
                                observed_at,
                                "sc_hub",
                                ref,
                                surface,
                            )
                        except PARSE_ERRORS as exc:
                            ctx.reject(
                                {"selection": ref, "position": position},
                                reason=f"not_a_playlist:{exc}"[:64],
                            )
                            continue
                        ctx.emit("raw.playlist_candidates", sanitize_strings(row))
        except PARSE_ERRORS:
            envelope_miss(ctx, surface, "record")
            ctx.observed(1)
            ctx.reject(
                {"surface": surface}, reason=f"envelope_mismatch:{surface}:record"
            )
    ctx.set_cursor(None, {"client_id": state.get("client_id")})
