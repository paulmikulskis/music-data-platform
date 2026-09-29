"""Tests for playlist collector failures."""

import html
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from mdp_functions import bandcamp, soundcloud
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.hosts import host_key
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx
from mdp_functions.registry import discover
from playlist_collector_fixture import ROOT, TARGETS, run, target
from test_fetch_runtime import FakeDB, context, no_page


def fixture(key, scenario="normal"):
    lines = (ROOT / key / "fixtures" / f"{scenario}.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def respond(item):
    body = item["response"]["body"]
    return httpx.Response(
        item["response"]["status"],
        content=body.encode() if isinstance(body, str) else json.dumps(body).encode(),
    )


def new_ctx(key, cursor=None):
    cursors = {"t": {"cursor_value": cursor}} if cursor is not None else None
    return Ctx(discover()[key], {"id": uuid4(), "cycle_id": uuid4(), "resolved_config": {"fixture": True}}, cursors)


async def drive(ctx, batch, route):
    """Run one collector against `route`; returns requests sent and envelope misses."""
    calls, misses = [], []

    def handler(request):
        calls.append(request)
        return route(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        if batch is None:
            await ctx.manifest.function(ctx)
        else:
            await ctx.manifest.function(ctx, batch)
    return calls, misses


def cursor_of(ctx, default=None):
    return next(iter(ctx.pending_cursors.values()), default)


def keyed(make):
    member = make()
    member["id"] = "t"
    return member


def test_wildcard_host_keys_every_subdomain_on_its_parent():
    declared = ["*.bandcamp.com"]
    assert host_key("Fixture item 56143.Bandcamp.com", declared) == "bandcamp.com"
    assert host_key("bandcamp.com", declared) is None
    assert host_key("evilbandcamp.com", declared) is None
    assert host_key("daily.bandcamp.com", ["daily.bandcamp.com"]) == (
        "daily.bandcamp.com"
    )
    assert host_key("any.test", []) == "any.test"
    assert discover()["bc_tralbum"].hosts == declared


class HostDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.hosts = []

    def one(self, query, params=()):
        if "host_health" in query:
            self.hosts.append(params[0])
        return super().one(query, params)


async def test_artist_hosts_share_the_bandcamp_pause_and_permits(monkeypatch):
    vendors = []
    monkeypatch.setattr(
        "mdp_functions.http.draw", lambda db, run, vendor, *args: vendors.append(vendor)
    )
    ctx, run_row = context(hosts=["*.bandcamp.com"])
    db = HostDB()
    db.health = {"host_rps": 1000}
    sent = []

    def transport(request):
        sent.append(request.url.host)
        return httpx.Response(200, text="ok")

    async with TracedClient(
        ctx, db, run_row, no_page, transport=httpx.MockTransport(transport)
    ) as client:
        await client.get("https://synthetic-label.bandcamp.com/sitemap.xml")
        with pytest.raises(ServiceError, match="allowlist"):
            await client.get("https://example.com/")
        # A block recorded for any artist host is the block for every artist host.
        db.health = {
            "host_rps": 1000,
            "blocked_until": datetime.now(timezone.utc) + timedelta(hours=1),
            "last_signature": "cloudflare",
        }
        with pytest.raises(ServiceError, match="blocked:cloudflare"):
            await client.get("https://other.bandcamp.com/sitemap.xml")
    assert sent == ["synthetic-label.bandcamp.com"]
    assert set(db.hosts) == {"bandcamp.com"}
    assert vendors == ["bandcamp.com"]
    assert [row[2] for row in db.rows if len(row) == 14] == ["bandcamp.com"]


async def test_release_handles_off_bandcamp_are_rejected_not_skipped():
    band = target("bandcamp", "band:1", "music.example.com")
    ctx, calls = await run(
        "bc_tralbum",
        batch=[band],
        transport=httpx.MockTransport(lambda r: pytest.fail(str(r.url))),
    )
    assert not calls
    assert [r["reason"] for r in ctx.rejected] == [
        "envelope_mismatch:bc_tralbum:target"
    ]
    assert ctx.observed_count == len(ctx.rejected) == 1


def sitemap(urls):
    entries = "".join(
        f"<url><loc>{u}</loc><lastmod>2026-09-01</lastmod></url>" for u in urls
    )
    return f"<urlset>{entries}</urlset>"


def release_page(item_id, rows, broken=0):
    tralbum = {
        "id": item_id,
        "item_type": "album",
        "artist": "Fixture item 56143",
        "current": {"title": f"Album {item_id}", "band_id": 7},
        "trackinfo": [
            {
                "track_id": None if n < broken else 100 + n,
                "id": None,
                "title": f"Track {n}",
                "track_num": n + 1,
            }
            for n in range(rows)
        ],
    }
    return f'<div data-tralbum="{html.escape(json.dumps(tralbum))}"></div>'


@pytest.mark.parametrize(("broken", "drift"), [(1, False), (3, True)])
async def test_release_page_drift_follows_the_share_rule(broken, drift):
    url = "https://synthetic-label.bandcamp.com/album/a"

    def route(request):
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap([url]))
        return httpx.Response(200, text=release_page(1, 4, broken))

    ctx = new_ctx("bc_tralbum")
    _, misses = await drive(ctx, [keyed(TARGETS["bc_tralbum"])], route)
    assert misses == ([("bc_tralbum", "item")] if drift else [])
    assert len(ctx.outputs["raw.bc_tracks"]) == 4 - broken
    assert [r["reason"] for r in ctx.rejected] == [
        "envelope_mismatch:bc_tralbum:item"
    ] * broken
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)
    # A page with rejected rows is fetched again, as a failure.
    assert cursor_of(ctx)["failures"] == {url: 1} and not cursor_of(ctx)["lastmod"]


async def test_failing_release_pages_wait_behind_pages_that_have_not(monkeypatch):
    monkeypatch.setattr(bandcamp, "MAX_RELEASE_PAGES", 2)
    urls = [f"https://synthetic-label.bandcamp.com/album/{c}" for c in "abcd"]

    def route(request):
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap(urls))
        if request.url.path == "/album/a":
            return httpx.Response(503)
        return httpx.Response(200, text=release_page(ord(request.url.path[-1]), 2))

    cursor, fetched = None, []
    for _ in range(3):
        ctx = new_ctx("bc_tralbum", cursor)
        calls, _ = await drive(ctx, [keyed(TARGETS["bc_tralbum"])], route)
        fetched.append([c.url.path for c in calls[1:]])
        cursor = cursor_of(ctx)
    assert fetched == [["/album/a", "/album/b"], ["/album/c", "/album/d"], ["/album/a"]]
    assert cursor["failures"] == {urls[0]: 2}
    assert set(cursor["lastmod"]) == set(urls[1:])


def soundcloud_route(tracks=None, body=None):
    hydration, playlist, hydrated = fixture("sc_playlist")

    def route(request):
        if request.url.host == "soundcloud.com":
            return respond(hydration)
        if request.url.path.startswith("/playlists/"):
            return body(request) if body else respond(playlist)
        if request.url.path == "/tracks":
            return tracks() if tracks else respond(hydrated)
        pytest.fail(str(request.url))

    return route


@pytest.mark.parametrize(
    ("answer", "drift"),
    [
        (lambda: httpx.Response(503), False),
        (lambda: httpx.Response(200, text="<html>not json"), True),
    ],
)
async def test_unhydrated_rows_are_typed_rejects_and_only_parse_errors_drift(
    answer, drift
):
    ctx = new_ctx("sc_playlist")
    _, misses = await drive(
        ctx, [keyed(TARGETS["sc_playlist"])], soundcloud_route(answer)
    )
    assert misses == ([("sc_tracks", "item")] if drift else [])
    reasons = [r["reason"] for r in ctx.rejected]
    # Four of the six rows arrive as stubs that only a hydration fills.
    assert reasons == ["unhydrated:sc_tracks"] * 4
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["coverage"] == "partial" and snapshot["items_observed"] == 2
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)


def curator_route(listing):
    playlist = fixture("sc_playlist")[1]

    def body(request):
        answer = deepcopy(playlist["response"]["body"])
        answer["id"] = int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, json=answer)

    fallback = soundcloud_route(body=body)

    def route(request):
        if request.url.path.endswith("/playlists_without_albums"):
            return httpx.Response(200, json={"collection": listing, "next_href": None})
        return fallback(request)

    return route


async def test_curator_gate_advances_only_for_bodies_that_landed(monkeypatch):
    monkeypatch.setattr(soundcloud, "GATED_BODIES", 1)
    new, old = ["2026-09-20T00:00:00Z", 6], ["2026-09-01T00:00:00Z", 6]
    listing = [
        {
            "id": n,
            "kind": "playlist",
            "title": f"Playlist {n}",
            "track_count": 6,
            "last_modified": new[0],
            "user": {"id": 5, "permalink": "curator"},
        }
        for n in (11, 12, 13)
    ]
    curator = keyed(TARGETS["sc_curator_playlists"])
    curator["params_json"] = {"owner_class": "editorial"}
    cursor = {
        "binding": soundcloud.binding(curator, "sc_curator_playlists"),
        "gate": {"11": old, "12": old},
    }
    fetched = []
    for _ in range(2):
        ctx = new_ctx("sc_curator_playlists", cursor)
        calls, _ = await drive(ctx, [curator], curator_route(listing))
        fetched += [c.url.path for c in calls if c.url.path.startswith("/playlists/")]
        cursor = cursor_of(ctx)
        assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)
    # 13 is new since the previous listing, so it is moved as well.
    assert fetched == ["/playlists/11", "/playlists/12"]
    assert cursor["gate"] == {"11": new, "12": new}
    # The body's own owner class lands, never the curator's.
    snapshot = ctx.outputs["raw.playlist_snapshots"][0]
    assert snapshot["owner_class"] == "unknown"


async def test_malformed_curator_ids_are_rejected_not_skipped():
    ctx, calls = await run(
        "sc_curator_playlists",
        batch=[target("soundcloud", "music-charts-us")],
        transport=httpx.MockTransport(lambda r: pytest.fail(str(r.url))),
    )
    assert not calls
    assert [r["reason"] for r in ctx.rejected] == [
        "envelope_mismatch:sc_curator_playlists:target"
    ]
    assert ctx.observed_count == len(ctx.rejected) == 1
