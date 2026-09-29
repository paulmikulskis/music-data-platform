"""Tests for bandcamp collection."""

import asyncio
import json
import time
from itertools import pairwise
from uuid import uuid4

import httpx
from mdp_functions import bandcamp
from mdp_functions.layers import Ctx
from mdp_functions.registry import discover
from playlist_collector_fixture import ROOT, SPEC, TARGETS, run, target


async def test_bandcamp_discover_keeps_typed_items_and_featured_tracks():
    ctx, calls = await run("bc_discover")
    body = json.loads(calls[0].content)
    assert body["size"] == 500 and body["tag_norm_names"] == ["electronic"]
    items = ctx.outputs["raw.playlist_items"]
    assert {i["item_type"] for i in items} == {"album", "package"}
    assert all(i["platform_track_id"] is None for i in items)
    assert all(
        i["featured_track_id"] != i["platform_item_id"]
        for i in items
        if i["featured_track_id"]
    )
    assert items[0]["occurrence_key"] == f"album:{items[0]['platform_item_id']}#1"
    wrong = TARGETS["bc_discover"]()
    wrong["params_json"] = {"spec": {**SPEC, "slice": "new"}}
    ctx, calls = await run("bc_discover", batch=[wrong])
    assert not calls and [r["reason"] for r in ctx.rejected] == ["variant_mismatch"]


async def test_bandcamp_daily_positions_come_from_entry_headings():
    ctx, _ = await run("bc_daily_list")
    items = ctx.outputs["raw.playlist_items"]
    # The intro embed has a player but no entry heading, so it is not a list entry.
    assert [i["position"] for i in items] == [1, 2, 3]
    assert "intro" not in json.dumps(items)
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert (
        snapshot["platform_version"]
        and snapshot["attributes"][0]["key"] == "date_published"
    )
    assert all(i["featured_track_id"] for i in items if i["item_type"] == "album")


async def test_bandcamp_daily_multi_part_lists_fold_into_one():
    body = json.loads(
        (ROOT / "bc_daily_list/fixtures/normal.jsonl").read_text().splitlines()[0]
    )["response"]["body"]
    parts = [
        "https://daily.bandcamp.com/best-of-2025/day-1",
        "https://daily.bandcamp.com/best-of-2025/day-2",
    ]
    list_target = target(
        "bandcamp", "daily:best-of-2025:albums", urls=parts, owner_class="editorial"
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=body))
    ctx, calls = await run("bc_daily_list", batch=[list_target], transport=transport)
    assert [str(c.url) for c in calls] == parts
    items = ctx.outputs["raw.playlist_items"]
    assert [i["position"] for i in items] == list(range(1, 7))
    # The same release twice keeps two occurrences, as any duplicate does.
    assert items[3]["occurrence_key"].endswith("#2")


async def test_bandcamp_fan_playlist_follows_cursor_and_partial_never_shrinks():
    ctx, calls = await run("bc_fan_playlist")
    assert [c.method for c in calls] == ["GET", "POST"]
    assert json.loads(calls[1].content) == {
        "item_type": "playlist",
        "item_id": 500001,
        "next_cursor": 1,
    }
    assert len(ctx.outputs["raw.playlist_items"]) == 3
    ctx, _ = await run("bc_fan_playlist", "partial")
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["coverage"] == "partial" and snapshot["continuation"] is True
    wrong = TARGETS["bc_fan_playlist"]()
    wrong["platform_account_id"] = "playlist:1"
    ctx, _ = await run("bc_fan_playlist", batch=[wrong])
    assert [r["reason"] for r in ctx.rejected] == [
        "envelope_mismatch:bc_fan_playlist:record"
    ]


async def test_bandcamp_release_pages_gate_on_lastmod_and_isrc_only_from_track_page():
    ctx, calls = await run("bc_tralbum")
    assert {c.url.host for c in calls} == {"synthetic-label.bandcamp.com"}
    releases = {r["item_type"]: r for r in ctx.outputs["raw.bc_releases"]}
    assert releases["album"]["upc"] and releases["album"]["isrc"] is None
    assert releases["track"]["isrc"] == "USXXX2600003"
    tracks = ctx.outputs["raw.bc_tracks"]
    assert [t["isrc"] for t in tracks if t["page_item_type"] == "album"] == [
        None
    ] * sum(t["page_item_type"] == "album" for t in tracks)
    assert "file" not in json.dumps(tracks)
    cursor = next(iter(ctx.pending_cursors.values()))
    band = TARGETS["bc_tralbum"]()
    band["id"] = "band"
    ctx, calls = await run(
        "bc_tralbum", batch=[band], cursors={"band": {"cursor_value": cursor}}
    )
    assert [c.url.path for c in calls] == ["/sitemap.xml"]
    assert not ctx.outputs


async def test_large_bandcamp_parse_leaves_event_loop_responsive(monkeypatch):
    """Every collector parses through `off_loop`; an 8,000-item ranking parses off the
    loop while another request keeps answering."""
    item = json.loads(
        (ROOT / "bc_discover/fixtures/normal.jsonl").read_text().splitlines()[0]
    )["response"]["body"]
    # Lean rows: per-item validation and hashing dominate, as on real rankings. A JSON
    # decode is one C call that holds the GIL, so its share stays small (a real
    # 500-item ranking decodes in milliseconds).
    keep = ("item_type", "title", "band_name", "band_id", "featured_track", "duration")
    results = [
        {**{k: item["results"][i % 3][k] for k in keep}, "item_id": 10_000 + i}
        for i in range(8000)
    ]
    body = json.dumps({**item, "results": results, "batch_result_count": 8000})
    spans = []
    parse = bandcamp.parse_discover

    def timed(*args):
        start = time.perf_counter()
        try:
            return parse(*args)
        finally:
            spans.append((start, time.perf_counter()))

    monkeypatch.setattr(bandcamp, "parse_discover", timed)

    async def service(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def serve_during_parse():
        answered = []
        manifest = discover()["bc_discover"]
        ctx = Ctx(manifest, {"id": uuid4(), "cycle_id": uuid4()})
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))
            ) as source,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=service), base_url="http://svc"
            ) as other,
        ):
            ctx.http = source
            task = asyncio.create_task(
                manifest.function(ctx, [TARGETS["bc_discover"]()])
            )
            answered.append(time.perf_counter())
            while not task.done():
                await asyncio.sleep(0.005)
                assert (await other.get("/health")).status_code == 200
                answered.append(time.perf_counter())
            await task
        assert len(ctx.outputs["raw.playlist_items"]) == 8000
        start, end = spans[-1]
        gap = max(b - a for a, b in pairwise(answered))
        return sum(start < t < end for t in answered), gap, end - start

    during, gap, parsed = await serve_during_parse()
    assert during >= 2 and gap < 0.6 * parsed, (during, gap, parsed)

    async def inline(fn, *args):
        return fn(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline)
    during, gap, parsed = await serve_during_parse()
    assert during == 0 and gap >= parsed, (during, gap, parsed)


async def test_daily_page_without_entry_headings_lands_partial_and_counts():
    body = json.loads(
        (ROOT / "bc_daily_list/fixtures/normal.jsonl").read_text().splitlines()[0]
    )["response"]["body"]
    body = body.replace("<h3", "<h4").replace("</h3>", "</h4>")
    manifest = discover()["bc_daily_list"]
    ctx = Ctx(manifest, {"id": uuid4(), "cycle_id": uuid4()}, None)
    misses = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))
    ) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await manifest.function(ctx, [TARGETS["bc_daily_list"]()])
    assert misses == [("bc_daily_list", "empty")]
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["coverage"] == "partial" and snapshot["items_observed"] == 0


async def test_fan_playlist_lands_no_handle_and_short_lists_are_partial():
    ctx, _ = await run("bc_fan_playlist")
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["owner_name"] is None and snapshot["owner_id"] == "900000001"
    assert snapshot["canonical_url"] == "https://bandcamp.com/playlist/500001"
    assert snapshot["coverage"] == "full" and snapshot["track_count_reported"] == 3
    assert "fan-example-a" not in json.dumps([ctx.outputs, ctx.rejected], default=str)
    # One empty cursor page on a list of 3: two rows arrive, so the list is partial.
    fixture = [
        json.loads(line)
        for line in (ROOT / "bc_fan_playlist/fixtures/normal.jsonl")
        .read_text()
        .splitlines()
    ]
    empty = {"tracklist": {**fixture[1]["response"]["body"]["tracklist"], "tracks": []}}

    def answer(request):
        if request.method == "GET":
            return httpx.Response(200, text=fixture[0]["response"]["body"])
        return httpx.Response(200, json=empty)

    ctx, _ = await run("bc_fan_playlist", transport=httpx.MockTransport(answer))
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["items_observed"] == 2 and snapshot["coverage"] == "partial"
