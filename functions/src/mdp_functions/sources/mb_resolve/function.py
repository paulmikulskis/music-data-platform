"""Resolve priority tracks SQL cannot give a recording against the MusicBrainz mirror.

Each input is one priority track and one lookup on the private mirror, never an external call: its
platform album URL to the release, then candidates by title, artist credit and duration within two
seconds, and when no release candidate matches, title and credit by trigram. Every input lands one
row, negative results included, with the generation and replication sequence it read; a resolved answer
also lands the spine rows it touched in raw.mb_resolve_closure, which reference staging joins for keys
the landed generation lacks. The input
version carries `retry_week`, so every unresolved track is looked up again once a week, about 1/168
of the set each hour; inputs page oldest first and the run ends `partial` at its 20-minute budget.
"""

import asyncio
from typing import Any

import psycopg
from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx, gold
from mdp_functions.musicbrainz import (
    MbResolveClosure,
    lookup_connection,
    reset_lookup_connection,
    resolve,
)
from pydantic import BaseModel


class Resolution(BaseModel):
    platform: str
    platform_track_id: str
    status: str
    method: str | None = None
    recording_id: int | None = None
    recording_gid: str | None = None
    isrc: str | None = None
    isrc_count: int
    release_id: int | None = None
    release_gid: str | None = None
    candidate_count: int
    confidence: float | None = None
    evidence: str | None = None
    mb_generation: str | None = None
    mb_sequence: int | None = None
    lookup_ms: float


def lookup(row: dict[str, Any]) -> dict[str, Any]:
    return resolve(lookup_connection(), row)


@gold(
    source_key="mb_resolve",
    reads=["intermediate.int_identity__resolution_inputs"],
    writes=["raw.mb_resolve", "raw.mb_resolve_closure"],
    cadence="hourly",
    external=True,
    schema={"raw.mb_resolve": Resolution, "raw.mb_resolve_closure": MbResolveClosure},
    output_keys={"raw.mb_resolve_closure": ["mb_table", "mb_key"]},
    input_key=["platform", "platform_track_id"],
    input_version=["fields_hash", "reference_version", "retry_week"],
    input_order=["retry_week", "first_landed_seq"],
    time_budget_s=1200,
    # The invoke waits for the paged read and the budget, so its timeout exceeds both.
    # Starts disabled: enable through control_rt once apply-mdp-schema.py has run on the mirror and
    # MDP_MB_DB_URL is set on mdp-functions (ops/fly/SECRETS.md).
    knobs={"enabled": False, "allow_partial": True, "timeout_s": 1800},
)
async def mb_resolve(ctx: Ctx, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        try:
            result = await asyncio.to_thread(lookup, row)
        except psycopg.Error as exc:
            reset_lookup_connection()
            raise ServiceError("vendor_retryable", "A MusicBrainz mirror lookup failed") from exc
        touched = result.pop("closure") or []
        ctx.emit("raw.mb_resolve", {**result, **ctx.input_identity(row)})
        for spine_row in touched:
            ctx.emit("raw.mb_resolve_closure", {**spine_row, "mb_generation": result["mb_generation"],
                                                "mb_sequence": result["mb_sequence"]})
