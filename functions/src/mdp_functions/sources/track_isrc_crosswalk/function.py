"""ISRCs for priority Spotify tracks from public search.

The inputs are the priority Spotify tracks with no ISRC from the platform or the landed MusicBrainz
rows; each is versioned by its own fields and `reference_version`, so a search repeats only when those
change. Deezer's public search returns ISRCs inline; artist, title and duration within two seconds must
agree (and the album title when the input carries one). One call per input, one row per input,
negative results included. Identity SQL reads only rows at or above the per-platform confidence floor.
"""

from collections.abc import AsyncIterator
from typing import Any

from mdp_functions.layers import Ctx, gold
from mdp_functions.musicbrainz import (
    DURATION_MS,
    artist_agrees,
    artist_names,
    normalize,
)
from pydantic import BaseModel

SEARCH = "https://api.deezer.com/search"


class Crosswalk(BaseModel):
    platform: str
    platform_track_id: str
    status: str
    method: str
    isrc: str | None = None
    confidence: float | None = None
    candidate_count: int
    match_id: str | None = None
    evidence: str | None = None


def score(row: dict[str, Any], hit: dict[str, Any]) -> float | None:
    """1.0 for folded-equal title and agreeing artist; 0.9 when one title contains the other; None
    when artist, title or duration disagree."""
    names = artist_names(row.get("artist_names"))
    title, found = normalize(row.get("title")), normalize(hit.get("title"))
    duration = row.get("duration_ms")
    if duration is None or hit.get("duration") is None or abs(int(hit["duration"]) * 1000 - int(duration)) > DURATION_MS + 1000:
        # Deezer reports whole seconds: allow its rounding on top of the two-second window.
        return None
    if not artist_agrees(names, [(hit.get("artist") or {}).get("name")]):
        return None
    album = row.get("album_title")
    if album and normalize(album) != normalize((hit.get("album") or {}).get("title")):
        return None
    if title == found:
        return 1.0
    if title and found and (title in found or found in title):
        return 0.9
    return None


@gold(
    source_key="track_isrc_crosswalk",
    reads=["intermediate.int_identity__crosswalk_inputs"],
    writes=["raw.track_isrc_crosswalk"],
    cadence="hourly",
    external=True,
    hosts=["api.deezer.com"],
    expect={"api.deezer.com": ["application/json"]},
    schema={"raw.track_isrc_crosswalk": Crosswalk},
    input_key=["platform", "platform_track_id"],
    input_version=["fields_hash", "reference_version", "retry_week"],
    input_order=["retry_week", "first_landed_seq"],
    time_budget_s=1800,
    # The invoke waits for the paged read and the budget, so its timeout exceeds both.
    # Starts disabled with the mirror lookups: its inputs need the landed spine to exclude MusicBrainz ISRCs.
    knobs={"enabled": False, "allow_partial": True, "timeout_s": 2700},
)
async def track_isrc_crosswalk(ctx: Ctx, rows: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for row in rows:
        names = artist_names(row.get("artist_names"))
        query = " ".join([*names[:1], str(row.get("title") or "")]).strip()
        response = await ctx.http.get(SEARCH, params={"q": query, "limit": "5"})
        try:
            body = response.json() if response.status_code == 200 else {}
        except ValueError:
            # Unparseable search output leaves the input incomplete for the next run.
            ctx.observed(1)
            ctx.reject(row, reason="search_not_json")
            continue
        hits = [h for h in (body.get("data") or []) if isinstance(h, dict)]
        scored = sorted(((s, h) for h in hits if (s := score(row, h)) is not None and h.get("isrc")),
                        key=lambda pair: -pair[0])
        isrcs = sorted({h["isrc"] for s, h in scored if s == scored[0][0]}) if scored else []
        result = {"platform": row["platform"], "platform_track_id": row["platform_track_id"],
                  "method": "deezer_search", "candidate_count": len(isrcs), "isrc": None,
                  "confidence": None, "match_id": None, "status": "unmatched",
                  "evidence": f"q={query};hits={len(hits)};http={response.status_code}"}
        if len(isrcs) == 1:
            best, hit = scored[0]
            result.update(status="resolved", isrc=isrcs[0], confidence=best, match_id=str(hit.get("id")))
        elif isrcs:
            result["status"] = "ambiguous"
        yield {**result, **ctx.input_identity(row)}
