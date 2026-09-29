"""mb_artist_catalog: how deep each credited artist's catalog is, read from the MusicBrainz mirror once a week.

Each input is one MusicBrainz artist credited on a song in the song layer, and one lookup on the private
mirror, never an external call. Every input lands one lookup row, negative answers included, with the generation
it read, and one row per kind of release group that credits the artist (primary type and secondary types) with
its count and earliest dated release year. The input version carries the bound cycle's ISO week, so the daily
job reads each artist once a week and the other six days find every input complete. SQL turns the counts into an
artist stage with dbt/seeds/artist_stage_thresholds.csv.

It ships disabled, like mb_resolve and mb_spine, so a stack without the mirror stays quiet. Enable it through
control_rt once MDP_MB_DB_URL is set on mdp-functions.
"""

import asyncio
import time
from typing import Any

import psycopg
from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx, gold
from mdp_functions.musicbrainz import (
    GID_RE,
    artist_catalog,
    lookup_connection,
    reset_lookup_connection,
)
from pydantic import BaseModel


class ArtistLookup(BaseModel):
    """One completed lookup of an artist: found, not_found or special_purpose; only found has release groups."""

    model_config = {
        "json_schema_extra": {
            "non_personal": [
                "status",
                "artist_id",
                "artist_gid",
                "mb_generation",
                "mb_sequence",
                "catalog_week",
            ]
        }
    }

    status: str
    artist_id: int | None = None
    artist_gid: str | None = None
    mb_generation: str | None = None
    mb_sequence: int | None = None
    catalog_week: str


class ArtistReleaseGroups(BaseModel):
    """The release groups of one kind that credit the artist. `primary_type` is empty when MusicBrainz gives
    none; `secondary_types` is a `;`-joined sorted list, empty when the groups have none."""

    model_config = {
        "json_schema_extra": {
            "non_personal": [
                "primary_type",
                "secondary_types",
                "release_groups",
                "first_release_year",
                "catalog_week",
            ]
        }
    }

    primary_type: str
    secondary_types: str
    release_groups: int
    first_release_year: int | None = None
    catalog_week: str


LOOKUP_TIMEOUT_S = 10


def lookup(gid: str, deadline: float | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    conn = lookup_connection("catalog")
    remaining = deadline - time.monotonic() if deadline is not None else LOOKUP_TIMEOUT_S
    if remaining <= 0:
        raise ServiceError("time_budget", "The catalog budget ended. Read the remaining artists next day.")
    try:
        return artist_catalog(conn, gid, min(LOOKUP_TIMEOUT_S, remaining))
    except psycopg.errors.QueryCanceled as exc:
        # A lookup limit protects the run even when its overall budget has time left.
        # No answer means the artist stays due; it is not a rejected input.
        raise ServiceError(
            "time_budget",
            "The mirror lookup was cancelled or timed out. Check /runbooks/time-budget for the next daily read.",
        ) from exc


@gold(
    source_key="mb_artist_catalog",
    reads=["intermediate.int_artist_catalog__inputs"],
    writes=["raw.mb_artist_catalog", "raw.mb_artist_release_groups"],
    cadence="daily",
    external=True,
    schema={
        "raw.mb_artist_catalog": ArtistLookup,
        "raw.mb_artist_release_groups": ArtistReleaseGroups,
    },
    output_keys={"raw.mb_artist_release_groups": ["primary_type", "secondary_types"]},
    input_key=["mb_artist_gid"],
    input_version=["mb_artist_gid", "catalog_week"],
    # Check before every artist and bound its query by the remaining time. Leave 300 s for landing.
    time_budget_s=900,
    # Starts disabled: enable through control_rt once MDP_MB_DB_URL is set on mdp-functions.
    knobs={
        "enabled": False,
        "allow_partial": True,
        "batch_size": 50,
        "timeout_s": 1200,
    },
)
async def mb_artist_catalog(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        gid = str(row["mb_artist_gid"])
        if not GID_RE.match(gid):
            ctx.observed(1)
            ctx.reject({"mb_artist_gid": gid}, reason="invalid_gid")
            continue
        try:
            found, groups = await asyncio.to_thread(lookup, gid, ctx.deadline)
        except psycopg.Error as exc:
            reset_lookup_connection("catalog")
            raise ServiceError(
                "vendor_retryable", "A MusicBrainz mirror lookup failed. Retry on the next daily run."
            ) from exc
        for group in groups:
            ctx.emit("raw.mb_artist_release_groups", group)
        ctx.emit("raw.mb_artist_catalog", found)
