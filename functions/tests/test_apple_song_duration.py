"""Exact ids supply durations without guessing identity. Run this file with pytest."""

import httpx
import pytest
from free_source_fixture import collect, no_ledger  # noqa: F401
from mdp_functions.fetch.hosts import DEFAULT_USER_AGENT
from mdp_functions.layers import Target
from mdp_functions.registry import discover

TARGET = Target(
    id="duration-target", platform="apple_music", platform_account_id="9188270456"
)


@pytest.mark.parametrize(
    "scenario,status,duration",
    [
        ("normal", "found", 180123),
        ("missing", "not_found", None),
        ("no_duration", "no_duration", None),
    ],
)
async def test_lookup_fixtures(scenario, status, duration):
    ctx, calls, refused = await collect("apple_song_duration", scenario, [TARGET])
    assert not refused
    assert len(calls) == 1
    assert calls[0].headers["User-Agent"] == DEFAULT_USER_AGENT
    assert ctx.observed_count == ctx.yielded_count == 1
    assert not ctx.rejected
    row = ctx.outputs["raw.apple_song_durations"][0]
    assert row["apple_song_id"] == "9188270456"
    assert row["status"] == status
    assert row["duration_ms"] == duration
    assert "trackName" not in row


@pytest.mark.parametrize(
    "hit",
    [
        {"kind": "song", "trackId": 999, "trackTimeMillis": 1000},
        {"kind": "music-video", "trackId": 9188270456, "trackTimeMillis": 1000},
        *[
            {"kind": "song", "trackId": 9188270456, "trackTimeMillis": value}
            for value in [0, -1, True, "180000", 180000.5]
        ],
    ],
)
async def test_changed_or_invalid_answers_are_rejected(hit):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"resultCount": 1, "results": [hit]})
    )
    ctx, _, _ = await collect(
        "apple_song_duration", batch=[TARGET], transport=transport
    )
    assert ctx.observed_count == 1
    assert ctx.yielded_count == 0
    assert len(ctx.rejected) == 1


@pytest.mark.parametrize(
    "body", [{}, [], {"resultCount": 0, "results": [{}]}, {"results": "changed"}]
)
async def test_broken_envelopes_do_not_land_negatives(body):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ctx, _, _ = await collect(
        "apple_song_duration", batch=[TARGET], transport=transport
    )
    assert ctx.yielded_count == 0
    assert len(ctx.rejected) == 1


async def test_other_platforms_make_no_request_and_source_starts_disabled():
    ctx, calls, _ = await collect(
        "apple_song_duration", batch=[Target(TARGET, platform="spotify")]
    )
    assert not calls
    assert ctx.observed_count == 0
    manifest = discover()["apple_song_duration"]
    assert manifest.knobs["enabled"] is False
    assert manifest.cadence == "daily"
