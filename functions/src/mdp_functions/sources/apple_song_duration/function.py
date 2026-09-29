"""Read durations by exact Apple song id. Enable a small global track set first."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from mdp_functions.layers import Ctx, Targets, bronze
from mdp_functions.releases import ITUNES, ITUNES_HOSTS
from pydantic import BaseModel, Field


class SongDuration(BaseModel):
    model_config = {
        "json_schema_extra": {
            "non_personal": ["apple_song_id", "duration_ms", "status", "observed_at"]
        }
    }

    apple_song_id: str
    duration_ms: int | None = Field(default=None, gt=0, strict=True)
    status: Literal["found", "not_found", "no_duration"]
    observed_at: datetime


@bronze(
    source_key="apple_song_duration",
    writes=["raw.apple_song_durations"],
    cadence="daily",
    targets=Targets("track", platforms=("apple", "apple_music")),
    key=["apple_song_id", "observed_at"],
    schema=SongDuration,
    hosts=ITUNES_HOSTS,
    # Durations are optional: SQL uses them only where the movement input has none, so a missed
    # floor fails this run and opens its alerts but never holds the daily close.
    blocks_cycle=False,
    knobs={
        "enabled": False,
        "batch_size": 10,
        "max_concurrency": 1,
        "allow_partial": True,
        "timeout_s": 300,
    },
)
async def durations(ctx: Ctx, batch: Targets.Batch) -> AsyncIterator[dict[str, Any]]:
    # The runtime isolates each target inside a batch for accounting and retries.
    for target in batch:
        if target.get("platform") not in {"apple", "apple_music"}:
            continue
        song_id = str(target.get("platform_account_id") or "")
        if not song_id.isascii() or not song_id.isdigit():
            ctx.observed(1)
            ctx.reject({"target_id": str(target["id"])}, reason="invalid_input")
            continue
        response = await ctx.http.get(
            ITUNES, params={"id": song_id, "entity": "song", "country": "US"}
        )
        ctx.observed(1)
        try:
            body = response.json()
        except ValueError:
            ctx.reject({"apple_song_id": song_id}, reason="envelope_mismatch")
            continue
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list) or body.get("resultCount") != len(results):
            ctx.reject({"apple_song_id": song_id}, reason="envelope_mismatch")
            continue
        # A changed id, a collection or duplicate answers cannot supply a duration.
        if results and (
            len(results) != 1
            or not isinstance(results[0], dict)
            or str(results[0].get("trackId")) != song_id
            or results[0].get("kind") != "song"
        ):
            ctx.reject({"apple_song_id": song_id}, reason="envelope_mismatch")
            continue
        duration = results[0].get("trackTimeMillis") if results else None
        if duration is not None and (type(duration) is not int or duration <= 0):
            ctx.reject({"apple_song_id": song_id}, reason="envelope_mismatch")
            continue
        yield {
            "apple_song_id": song_id,
            "duration_ms": duration,
            "status": "not_found"
            if not results
            else "no_duration"
            if duration is None
            else "found",
            "observed_at": datetime.now(UTC),
        }
