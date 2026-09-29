"""Spotify track artist identities from public server-rendered pages."""

from typing import Any

from bs4 import BeautifulSoup
from pydantic import BaseModel

from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx
from mdp_functions.playlist import EnvelopeError, off_loop, script

ITUNES = "https://itunes.apple.com/lookup"
SPOTIFY = "https://open.spotify.com"
ITUNES_HOSTS = ["itunes.apple.com"]
SPOTIFY_HOSTS = ["open.spotify.com"]
LOOKUP_LIMIT = 200




class SpTrackArtist(BaseModel):
    """One credited artist of a Spotify track, in page order (0 is the first artist)."""

    track_id: str
    artist_id: str
    artist_name: str | None = None
    position: int
    album_id: str | None = None
    title: str | None = None
    duration_ms: int | None = None














def page_state(html: str, entity: str) -> dict[str, Any]:
    """The server-rendered `initialState` entity for one page; nothing else is read from it."""
    soup = BeautifulSoup(html, "html.parser")
    state = script(soup, "script#initialState", encoded=True)
    # A state of another shape is an envelope miss, never an exception.
    entities = state.get("entities") if isinstance(state, dict) else None
    items = entities.get("items") if isinstance(entities, dict) else None
    found = items.get(entity) if isinstance(items, dict) else None
    if not isinstance(found, dict):
        raise EnvelopeError("entities.items")
    return found


def spotify_id(item: dict[str, Any]) -> str | None:
    value = item.get("id") or str(item.get("uri") or "").rsplit(":", 1)[-1]
    return str(value) if value else None


def track_artists(
    track: dict[str, Any], track_id: str
) -> tuple[list[dict[str, Any] | None], int]:
    """Artist rows in credit order and the number of credits read (for accounting)."""
    credits = [
        *((track.get("firstArtist") or {}).get("items") or []),
        *((track.get("otherArtists") or {}).get("items") or []),
    ]
    album = track.get("albumOfTrack") or {}
    duration = (track.get("duration") or {}).get("totalMilliseconds")
    rows: list[dict[str, Any] | None] = []
    seen: set[str] = set()
    for position, artist in enumerate(credits):
        artist_id = spotify_id(artist) if isinstance(artist, dict) else None
        if artist_id in seen:
            # The same credit listed twice is one fact; it lands once.
            continue
        seen.add(artist_id or "")
        rows.append(
            None
            if not artist_id
            else {
                "track_id": track_id,
                "artist_id": artist_id,
                "artist_name": ((artist.get("profile") or {}).get("name")),
                "position": position,
                "album_id": spotify_id(album) if isinstance(album, dict) else None,
                "title": track.get("name"),
                "duration_ms": int(duration) if isinstance(duration, int) else None,
            }
        )
    return rows, len(credits)


def missing(ctx: Ctx, path: str) -> None:
    """Reject this one track for a page without the expected state; the envelope check records
    the miss. A soft block that the same check detects still stops the run."""
    try:
        ctx.require({}, path, "sp_track_artists")
    except ServiceError as exc:
        if exc.error_class != "envelope_mismatch":
            raise


async def sp_track_artists(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    """Gold, per input track: the track page's artist ids. A per-track failure rejects that one
    input, which stays incomplete so the next cycle asks again, and the run goes on: a removed
    track (404) and a page without its state here, a vendor error in the runtime."""
    for row in rows:
        track_id = str(row["platform_track_id"])
        try:
            response = await ctx.http.get(f"{SPOTIFY}/track/{track_id}")
        except ServiceError as exc:
            if exc.error_class == "stale_target":
                continue
            raise
        try:
            track = await off_loop(page_state, response.text, f"spotify:track:{track_id}")
        except EnvelopeError as exc:
            missing(ctx, str(exc))
            continue
        artists, credits = track_artists(track, track_id)
        if not credits:
            missing(ctx, "firstArtist.items")
            continue
        for artist in artists:
            if artist is None:
                ctx.observed(1)
                ctx.reject({"track_id": track_id}, reason="missing_artist_id")
                continue
            ctx.emit("raw.sp_track_artists", artist)
