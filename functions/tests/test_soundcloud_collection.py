"""Tests for soundcloud collection."""


import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from mdp_functions.errors import ServiceError
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx
from mdp_functions.registry import discover
from playlist_collector_fixture import ROOT, TARGETS, run
from test_fetch_runtime import FakeDB, no_page


async def test_soundcloud_hydrates_isrc_and_drops_tokens_and_personal_fields():
    ctx, calls = await run("sc_playlist")
    items = ctx.outputs["raw.playlist_items"]
    tracks = ctx.outputs["raw.sc_tracks"]
    assert [i["position"] for i in items] == list(range(1, 7))
    # Items carry the native ISRC from publisher metadata, body or hydration alike.
    assert {i["platform_item_id"]: i["isrc"] for i in items} == {
        t["track_id"]: t["isrc"] for t in tracks
    }
    assert sum(bool(i["isrc"]) for i in items) >= 3
    assert {t["snapshot_id"] for t in tracks} == {items[0]["snapshot_id"]}
    retained = json.dumps([ctx.outputs, ctx.rejected])
    for sentinel in ("SENTINEL", "sndcdn", "track_authorization", "first_name"):
        assert sentinel not in retained
    # The web client id is runtime state in the cursor, never in a row.
    cursor = ctx.pending_cursors[next(iter(ctx.pending_cursors))]
    assert cursor["client_id"] and cursor["client_id"] not in retained
    hydrations = [c for c in calls if c.url.path == "/tracks"]
    assert len(hydrations) == 1 and len(hydrations[0].url.params["ids"].split(",")) == 4


async def test_soundcloud_hydrates_only_unseen_or_stale_ids():
    first, _ = await run("sc_playlist")
    cursor = deepcopy(next(iter(first.pending_cursors.values())))
    ctx, calls = await run(
        "sc_playlist",
        batch=[TARGETS["sc_playlist"]()],
        cursors=None,
    )
    fixed = TARGETS["sc_playlist"]()
    fixed["id"] = "fixed"
    ctx, calls = await run(
        "sc_playlist", batch=[fixed], cursors={"fixed": {"cursor_value": cursor}}
    )
    assert not [c for c in calls if c.url.path == "/tracks"]
    assert not [c for c in calls if c.url.host == "soundcloud.com"]
    assert len(ctx.outputs["raw.playlist_items"]) == 6
    assert "raw.sc_tracks" not in ctx.outputs or len(ctx.outputs["raw.sc_tracks"]) == 2
    stale = deepcopy(cursor)
    for value in stale["tracks"].values():
        value[-1] = "2020-01-01"
    ctx, calls = await run(
        "sc_playlist", batch=[fixed], cursors={"fixed": {"cursor_value": stale}}
    )
    assert [c for c in calls if c.url.path == "/tracks"]


async def test_soundcloud_401_refetches_client_id_once_then_retries():
    ctx, calls = await run("sc_playlist", "unauthorized")
    assert [c.url.host for c in calls][:3] == [
        "soundcloud.com",
        "api-v2.soundcloud.com",
        "soundcloud.com",
    ]
    assert ctx.outputs["raw.playlist_snapshots"][0]["coverage"] == "full"


async def test_soundcloud_401_through_the_runtime_client(monkeypatch):
    """The runtime raises on a 401; the collector refreshes the client id once, and a
    second 401 pauses the api host as `auth_refused` and stops the run."""
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    blocked = []
    monkeypatch.setattr(
        "mdp_functions.soundcloud.block_host",
        lambda db, run_id, host, sig, seconds: blocked.append((host, sig)),
    )
    manifest = discover()["sc_playlist"]
    run_row = {
        "id": str(uuid4()),
        "cycle_id": str(uuid4()),
        "streamline_id": str(uuid4()),
        "tenant_id": None,
        "resolved_config": {"fixture": True},
    }
    db = FakeDB()
    db.health = {"host_rps": 1000}
    calls = []

    def always_401(request):
        calls.append(request.url.host)
        if request.url.host == "soundcloud.com":
            body = (
                (ROOT / "sc_playlist/fixtures/normal.jsonl").read_text().splitlines()[0]
            )
            return httpx.Response(200, text=json.loads(body)["response"]["body"])
        return httpx.Response(401)

    ctx = Ctx(manifest, run_row)
    async with TracedClient(
        ctx, db, run_row, no_page, transport=httpx.MockTransport(always_401)
    ) as client:
        ctx.http = client
        with pytest.raises(ServiceError, match="blocked:auth_refused"):
            await manifest.function(
                ctx, [TARGETS["sc_playlist"](), TARGETS["sc_playlist"]()]
            )
    assert blocked == [("api-v2.soundcloud.com", "auth_refused")]
    # One hydration, one refresh, and nothing for the second target.
    assert calls == [
        "soundcloud.com",
        "api-v2.soundcloud.com",
        "soundcloud.com",
        "api-v2.soundcloud.com",
    ]
    assert [r["reason"] for r in ctx.rejected] == ["blocked:auth_refused"]
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)


async def test_soundcloud_curator_gate_fetches_only_moved_playlists():
    ctx, calls = await run("sc_curator_playlists")
    assert len(ctx.outputs["raw.playlist_candidates"]) == 4
    assert "raw.playlist_snapshots" not in ctx.outputs  # the first listing only records
    cursor = next(iter(ctx.pending_cursors.values()))
    gate = cursor["gate"]
    moved = next(iter(gate))
    gate[moved] = ["2000-01-01T00:00:00Z", 1]
    listing = [
        json.loads(line)
        for line in (ROOT / "sc_curator_playlists/fixtures/normal.jsonl")
        .read_text()
        .splitlines()
    ]
    body = [
        json.loads(line)
        for line in (ROOT / "sc_playlist/fixtures/normal.jsonl")
        .read_text()
        .splitlines()
    ]

    def route(request):
        for item in listing + body:
            url = httpx.URL(
                item["request"]["url"], params=item["request"].get("params")
            )
            if url.path == request.url.path and (
                url.params == request.url.params or "/playlists/" in url.path
            ):
                answer = deepcopy(item["response"]["body"])
                if url.path.startswith("/playlists/"):
                    answer["id"] = int(moved)
                return httpx.Response(
                    item["response"]["status"],
                    content=answer.encode()
                    if isinstance(answer, str)
                    else json.dumps(answer).encode(),
                )
        if request.url.path == f"/playlists/{moved}":
            answer = deepcopy(body[1]["response"]["body"])
            answer["id"] = int(moved)
            return httpx.Response(200, json=answer)
        raise AssertionError(request.url)

    curator = TARGETS["sc_curator_playlists"]()
    curator["id"] = "curator"
    ctx, calls = await run(
        "sc_curator_playlists",
        batch=[curator],
        cursors={"curator": {"cursor_value": cursor}},
        transport=httpx.MockTransport(route),
    )
    fetched = [c.url.path for c in calls if c.url.path.startswith("/playlists/")]
    assert fetched == [f"/playlists/{moved}"]
    assert ctx.outputs["raw.playlist_snapshots"][0]["playlist_id"] == moved
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)


async def test_soundcloud_hubs_stop_on_repeated_page_and_keep_kinds():
    ctx, calls = await run("sc_hubs")
    mixed = [c for c in calls if c.url.path == "/mixed-selections"]
    assert len(mixed) == 2  # the second page repeats the first, so paging stops
    candidates = ctx.outputs["raw.playlist_candidates"]
    assert {c["discovered_via"] for c in candidates} == {"sc_hub"}
    assert any(
        c["playlist_id"].startswith("soundcloud:system-playlists:") for c in candidates
    )
    keys = [(c["playlist_id"], c["snapshot_id"], c["position"]) for c in candidates]
    assert len(keys) == len(set(keys))
    ctx, _ = await run("sc_hubs", "partial")
    assert [r["reason"] for r in ctx.rejected] == ["not_a_playlist:kind:user"]


async def test_soundcloud_live_collection_refuses_before_http():
    from mdp_functions.soundcloud import Session

    ctx = Ctx(discover()["sc_playlist"], {"id": uuid4(), "cycle_id": uuid4()})
    # No HTTP client is installed: refusal must happen before any network access,
    # even when a legacy cursor already holds a client identifier.
    with pytest.raises(ServiceError, match="official, credentialed"):
        Session(ctx, {"client_id": "synthetic-cached-identifier"})
