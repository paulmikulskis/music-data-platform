"""Tests for playlist collectors."""


import csv
import html
import json
import re
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx
from mdp_functions.playlist import UA
from mdp_functions.registry import discover
from playlist_collector_fixture import (
    EXPECTED,
    REPO,
    ROOT,
    SCENARIOS,
    TARGETS,
    run,
    target,
)


@pytest.mark.parametrize(("key", "scenario"), SCENARIOS)
async def test_every_scenario_accounts_and_lands_snapshot_last(key, scenario):
    ctx, calls = await run(key, scenario)
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected) + sum(ctx.exclusions.values())
    assert calls and all(c.headers["User-Agent"] == UA for c in calls)
    assert not ctx.payloads and ctx.manifest.keep_payload is False
    # The embed route creates state on a GET; audio and signed URLs are never fetched.
    assert not any("/embed" in c.url.path or ".mp3" in c.url.path for c in calls)
    snapshots = ctx.outputs.get("raw.playlist_snapshots", [])
    if scenario in ("drift", "not_found", "blocked-cloudflare"):
        assert not snapshots and ctx.rejected
        assert all(
            r["reason"].startswith("envelope_mismatch:") for r in ctx.rejected
        ), ctx.rejected
    elif snapshots:
        snapshot = snapshots[-1]
        items = ctx.outputs.get("raw.playlist_items", [])
        assert list(ctx.outputs)[-1] == "raw.playlist_snapshots"
        assert snapshot["coverage"] == EXPECTED[scenario]
        assert snapshot["items_observed"] == len(items)
        assert {i["snapshot_id"] for i in items} <= {snapshot["snapshot_id"]}
        assert all(i["stream"] == "full" for i in items)
        assert snapshot["cadence"] == "daily" and snapshot["membership_hash"]


def test_every_new_source_carries_public_surface_rights():
    with (REPO / "dbt/seeds/rights_registry.csv").open() as stream:
        rights = {r["source_key"]: r for r in csv.DictReader(stream)}
    for key, manifest in discover().items():
        if key.split("_")[0] in ("sc", "bc") or key.endswith("_weekly"):
            row = rights[key]
            assert (
                row["category"],
                row["license_ref"],
                row["learning_eligible"],
                row["resale_permitted"],
            ) == ("platform playlists" if "playlist" in key else "public charts and catalogs", "unverified", "false", "false"), key
            served = "weekly" if key.endswith("_weekly") else manifest.cadence
            assert row["refresh_cadence"] == served, key


async def test_refused_market_rejects_its_target_and_the_run_goes_on():
    """A knob that cannot serve a target's frozen market (raised by the runtime per
    request) rejects that target; later targets in the batch are still fetched."""
    body = json.loads(
        (ROOT / "bc_radio/fixtures/normal.jsonl").read_text().splitlines()[0]
    )["response"]["body"]

    def answer(request):
        if json.loads(request.content)["item_id"] == 993:
            raise ServiceError("variant_mismatch", "Proxy country disagrees")
        return httpx.Response(200, json=body)

    ctx, _ = await run(
        "bc_radio",
        batch=[target("bandcamp", "radio:993"), target("bandcamp", "radio:994")],
        transport=httpx.MockTransport(answer),
    )
    assert [r["reason"] for r in ctx.rejected] == ["variant_mismatch"]
    assert ctx.rejected[0]["record"] == {"playlist_id": "radio:993"}
    assert ctx.outputs["raw.playlist_snapshots"][0]["playlist_id"] == "radio:994"
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected) + sum(ctx.exclusions.values())


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("sp_playlist_page", "Episode"),
        ("sp_playlist_embed", "episode"),
        ("am_playlist", "musicVideo"),
    ],
)
async def test_non_track_rows_are_excluded_without_rejections_or_drift(source, kind):
    from test_playlist_publication import envelope

    ctx, target_, _entity, tracks, body = envelope(source)
    odd = deepcopy(tracks[0])
    if source == "sp_playlist_page":
        odd["itemV2"]["data"]["__typename"] = "Episode"
    elif source == "sp_playlist_embed":
        odd["uri"] = "spotify:episode:4rOoJ6Egrf8K2IrywzwOMk"
    else:
        odd["contentDescriptor"]["kind"] = "musicVideo"
    tracks.insert(1, odd)
    misses = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body()))
    ) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await ctx.manifest.function(ctx, [target_])
    assert not ctx.rejected
    assert ctx.exclusions == {f"unsupported_item:{kind}": 1}
    assert not misses
    items = ctx.outputs["raw.playlist_items"]
    assert 2 not in [i["position"] for i in items]
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected) + sum(ctx.exclusions.values())


async def test_drift_counts_only_when_most_rows_fail():
    from test_playlist_publication import envelope

    ctx, target_, _entity, tracks, body = envelope("am_playlist")
    for track in tracks[: len(tracks) // 2 + 1]:
        del track["contentDescriptor"]
    misses = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body()))
    ) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await ctx.manifest.function(ctx, [target_])
    assert misses == [("am_playlist", "item")]


@pytest.mark.parametrize(
    ("kind", "path", "reason"),
    [
        # A renamed track kind is not a known non-track kind: every row is rejected.
        ("track", "item", "envelope_mismatch:am_playlist:item"),
        # Every row a known non-track kind: no supported row remains.
        ("musicVideo", "empty", "unsupported_item:musicVideo"),
    ],
)
async def test_list_with_no_supported_row_lands_partial_and_counts(kind, path, reason):
    from test_playlist_publication import envelope

    ctx, target_, _entity, tracks, body = envelope("am_playlist")
    for track in tracks:
        track["contentDescriptor"]["kind"] = kind
    misses = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body()))
    ) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await ctx.manifest.function(ctx, [target_])
    assert misses == ([("am_playlist", path)] if path else [])
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["coverage"] == "partial" and snapshot["items_observed"] == 0
    assert not ctx.outputs.get("raw.playlist_items")
    assert {r["reason"] for r in ctx.rejected} | set(ctx.exclusions) == {reason}
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected) + sum(ctx.exclusions.values())


async def test_list_the_platform_counts_as_empty_lands_full_without_a_miss():
    """A cleared list (count 0, no rows) records its removals and never counts as drift."""
    fixture = [
        json.loads(line)
        for line in (ROOT / "bc_fan_playlist/fixtures/normal.jsonl")
        .read_text()
        .splitlines()
    ]
    page = fixture[0]["response"]["body"]
    blob = re.search(r'data-blob="([^"]*)"', page).group(1)
    app = json.loads(html.unescape(blob))["appData"]
    app["tracklist"].update(tracks=[], nextCursor=None)
    app["tracklist"]["tracksSummary"]["totalCount"] = 0
    cleared = page.replace(blob, html.escape(json.dumps({"appData": app}), quote=True))
    manifest = discover()["bc_fan_playlist"]
    ctx = Ctx(manifest, {"id": uuid4(), "cycle_id": uuid4()}, None)
    misses = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=cleared))
    ) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await manifest.function(ctx, [TARGETS["bc_fan_playlist"]()])
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["coverage"] == "full" and snapshot["items_observed"] == 0
    assert snapshot["track_count_reported"] == 0 and misses == []


async def test_spotify_unavailable_track_is_excluded_but_unknown_kind_is_rejected():
    from test_playlist_publication import envelope

    ctx, member, _, tracks, body = envelope('sp_playlist_page')
    tracks[0] = {'itemV2': {'__typename': 'TrackResponseWrapper', 'data': {'__typename': 'NotFound'}}}
    tracks[1] = {'itemV2': {'data': {'__typename': 'RenamedTrack'}}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body()))) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [member])
    assert ctx.exclusions == {'unavailable_track': 1}
    assert len(ctx.rejected) == 1
    assert ctx.rejected[0]['record']['position'] == 2
    assert ctx.rejected[0]['reason'] == 'envelope_mismatch:sp_playlist_page:item'
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected) + sum(ctx.exclusions.values())


@pytest.mark.parametrize("location", ["data", "wrapper", "item", "nested", "wrong_wrapper", "missing_wrapper"])
async def test_spotify_not_found_needs_verified_wrapper_and_no_track_uri(location):
    from test_playlist_publication import envelope

    ctx, member, entity, tracks, body = envelope("sp_playlist_page")
    item = {"itemV2": {"__typename": "TrackResponseWrapper", "data": {"__typename": "NotFound"}}}
    uri = "spotify:track:fixture"
    if location == "data":
        item["itemV2"]["data"]["uri"] = uri
    elif location == "wrapper":
        item["itemV2"]["uri"] = uri
    elif location == "item":
        item["uri"] = uri
    elif location == "nested":
        item["metadata"] = [{"track": {"uri": uri}}]
    elif location == "wrong_wrapper":
        item["itemV2"]["__typename"] = "OtherWrapper"
    else:
        del item["itemV2"]["__typename"]
    tracks[0] = item
    entity["content"]["totalCount"] = len(tracks)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body()))) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [member])
    assert not ctx.exclusions
    assert len(ctx.rejected) == 1
    assert ctx.rejected[0]["reason"] == "envelope_mismatch:sp_playlist_page:item"
    assert ctx.outputs["raw.playlist_snapshots"][0]["coverage"] == "partial"
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)


@pytest.mark.parametrize("excluded", [1, 2, 3])
@pytest.mark.parametrize("complete", [False, True])
async def test_spotify_mostly_excluded_page_signals_drift(excluded, complete):
    from test_playlist_publication import envelope

    ctx, member, entity, tracks, body = envelope("sp_playlist_page")
    tracks[:] = [deepcopy(tracks[0]) for _ in range(3)]
    entity["content"]["totalCount"] = 3 if complete else 100
    for position in range(excluded):
        tracks[position] = {"itemV2": {"__typename": "TrackResponseWrapper", "data": {"__typename": "NotFound"}}}
    misses = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body()))) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await ctx.manifest.function(ctx, [member])
    assert not ctx.rejected
    assert ctx.exclusions == {"unavailable_track": excluded}
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["items_observed"] == 3 - excluded
    assert snapshot["coverage"] == ("full" if complete and excluded == 1 else "partial")
    assert misses == ([] if excluded == 1 else [("sp_playlist_page", "empty" if excluded == 3 else "exclusions")])
    assert ctx.observed_count == ctx.yielded_count + excluded
