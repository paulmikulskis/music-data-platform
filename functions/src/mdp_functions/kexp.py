"""KEXP plays: every track play by airdate window, with the MusicBrainz ids KEXP supplies and
its rotation, local, request and live flags. The station's terms publish no licence, so the rows stay
operator-only and learning false until the `kexp_plays` registry row's `review_ref` names KEXP's written
permission, and the key ships disabled.

Host and DJ names, the show and every comment are people's words: none lands, and a non-track play (an
air break) is rejected as `not_a_trackplay` with its id and kind only. The daily run asks for oldest-first
pages (`ordering=airdate`) and reads from its airdate watermark, which commits with each page only while every
play so far arrived in airdate order, so a failed later page is reread. A backfill reads its window, at most
one airdate year and none of it older than two years, and projects its write against the pgdata headroom
line before it lands.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from pydantic import BaseModel

from mdp_functions.control_db import alert, event
from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx

PLAYS = "https://api.kexp.org/v2/plays/"
HOSTS = ["api.kexp.org"]
PAGE_LIMIT = 100
# The first daily run reads the last day; history arrives by backfill.
FIRST_WINDOW = timedelta(days=1)
# Late-logged plays: each run rereads this much before its watermark; staging keeps one row per play.
OVERLAP = timedelta(hours=2)
# About 300 to 400 plays a day, and the bytes one play takes landed with its lineage.
PLAYS_PER_DAY = 400
BYTES_PER_PLAY = 700
# A backfill window is at most one airdate year and reaches back at most two years.
BACKFILL_MAX = timedelta(days=366)
BACKFILL_HORIZON = timedelta(days=731)


class RadioPlay(BaseModel):
    station: str
    play_id: int
    airdate: datetime
    play_type: str
    recording_mbid: str | None = None
    artist_mbids: str
    release_group_mbid: str | None = None
    label_mbids: str
    rotation_status: str | None = None
    is_local: bool | None = None
    is_request: bool | None = None
    is_live: bool | None = None
    artist_text: str | None = None
    song_text: str | None = None
    observed_at: datetime


def headroom(warehouse_url: str, volume_bytes: int, ceiling: float, write_bytes: int) -> dict[str, Any]:
    """Whether writing `write_bytes` keeps the warehouse server under `ceiling` of the pgdata volume."""
    with psycopg.connect(warehouse_url) as conn:
        used = conn.execute(
            "SELECT coalesce(sum(pg_database_size(datname)), 0)::bigint FROM pg_database "
            "WHERE datallowconn AND has_database_privilege(datname, 'CONNECT')").fetchone()[0]
    return {"used_bytes": used, "projected_bytes": used + write_bytes, "limit_bytes": int(volume_bytes * ceiling),
            "stop": used + write_bytes > volume_bytes * ceiling}


def backfill_guard(ctx: Ctx, start: datetime, end: datetime, now: datetime | None = None) -> bool:
    """Project a backfill window's write and record it on the run; a window past the headroom line stops
    before it lands, with a `warehouse_disk_high` alert. A window longer than a year, or reaching
    back more than two years, is refused before any request. True when the window may land."""
    settings, db = ctx.http.settings, ctx.http.db
    now = now or datetime.now(UTC)
    if end <= start or end - start > BACKFILL_MAX or start < now - BACKFILL_HORIZON:
        raise ServiceError("backfill_window_refused",
                           "A KEXP backfill window is at most one airdate year within the last two years")
    days = max(1, (end - start).days)
    projected = {"window_from": start.isoformat(), "window_to": end.isoformat(), "projected_plays": days * PLAYS_PER_DAY,
                 **headroom(settings.warehouse_url, settings.pgdata_volume_bytes, settings.pgdata_ceiling,
                            days * PLAYS_PER_DAY * BYTES_PER_PLAY)}
    with db.transaction() as conn:
        event(conn, ctx.run_id, "kexp_backfill_projected", "Backfill window write projected", projected)
        if projected["stop"]:
            alert(conn, ctx.run_id, "warehouse_disk_high", ctx.run_id, "critical")
    return not projected["stop"]


def when(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def ids(value: Any) -> str:
    return json.dumps([str(v) for v in value] if isinstance(value, list) else [])


def shaped(play: dict[str, Any], observed_at: datetime) -> dict[str, Any]:
    """A track play without its people's words: no host, show or comment field is read."""
    return {
        "station": "kexp", "play_id": int(play["id"]), "airdate": when(play["airdate"]), "play_type": "trackplay",
        "recording_mbid": play.get("recording_id") or None, "artist_mbids": ids(play.get("artist_ids")),
        "release_group_mbid": play.get("release_group_id") or None, "label_mbids": ids(play.get("label_ids")),
        "rotation_status": play.get("rotation_status") or None, "is_local": play.get("is_local"),
        "is_request": play.get("is_request"), "is_live": play.get("is_live"),
        "artist_text": play.get("artist") or None, "song_text": play.get("song") or None, "observed_at": observed_at,
    }


async def plays(ctx: Ctx):
    now = datetime.now(UTC)
    if ctx.window:
        start, end = when(ctx.window["from"]), when(ctx.window["to"])
        if not backfill_guard(ctx, start, end):
            return
    else:
        mark = (ctx.cursor(None) or {}).get("airdate")
        start, end = (when(mark) - OVERLAP) if mark else now - FIRST_WINDOW, now
    url: str | None = PLAYS
    params: dict[str, Any] | None = {"airdate_after": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                     "airdate_before": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": PAGE_LIMIT,
                                     "ordering": "airdate"}
    newest, ordered = None, True
    while url:
        response = await ctx.http.get(url, params=params)
        try:
            body = response.json()
        except ValueError:
            body = {}
        try:
            results = ctx.require(body, "results", "kexp_plays")
        except ServiceError as exc:
            if exc.error_class != "envelope_mismatch":
                raise
            return
        observed_at = datetime.now(UTC)
        ctx.observed(len(results))
        for play in results:
            if not isinstance(play, dict) or "id" not in play or "airdate" not in play:
                ctx.reject({"play_id": (play or {}).get("id") if isinstance(play, dict) else None},
                           reason="drift:kexp_plays:play")
                continue
            if play.get("play_type") != "trackplay":
                ctx.exclude({"play_id": play["id"], "play_type": play.get("play_type")}, reason="not_a_trackplay")
                continue
            row = shaped(play, observed_at)
            # A play older than one already read means the pages are not oldest first: plays before the
            # newest seen may still be unread, so the watermark stops advancing for this run.
            ordered = ordered and (newest is None or row["airdate"] >= newest)
            newest = max(newest or row["airdate"], row["airdate"])
            yield row
        if not ctx.window and ordered and newest is not None:
            # Every play up to the newest has been read: the watermark commits with this page's dumps.
            ctx.set_cursor(None, {"airdate": newest.isoformat()})
        url, params = body.get("next"), None
