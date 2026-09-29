"""Tests for playlist transport."""

import asyncio
import gzip
import json
import logging
import time
from copy import deepcopy
from decimal import Decimal
from itertools import pairwise

import httpx
import pytest
from mdp_functions import playlist
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.providers import market_country
from mdp_functions.http import FixtureTransport, TracedClient
from mdp_functions.layers import Target
from mdp_functions.playlist import observe, sanitize_playlist_output
from mdp_functions.registry import discover
from pydantic import SecretStr
from test_fetch_runtime import FakeDB, context, no_page
from test_playlist_publication import collect, envelope
from test_playlists import ROOT, SOURCES, setup


@pytest.mark.parametrize("source", SOURCES)
async def test_header_failure_accounts_for_each_upstream_item(source):
    ctx, target, entity, tracks, body = envelope(source)
    header = entity["sections"][0]["items"][0] if source == "am_playlist" else entity
    del header["name" if source == "sp_playlist_page" else "title"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body()))
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert (
        ctx.observed_count == len(tracks) + 1 == ctx.yielded_count + len(ctx.rejected)
    )
    assert len(ctx.outputs["raw.playlist_items"]) == len(tracks)
    assert len(ctx.rejected) == 1 and not ctx.outputs.get("raw.playlist_snapshots")


async def test_duplicate_identity_precedes_field_validation_and_drift_once():
    ctx, target, _entity, tracks, body = envelope("am_playlist")
    tracks[:] = [deepcopy(tracks[0]), deepcopy(tracks[0])]
    del tracks[0]["title"]
    misses = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body()))
    ) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert ctx.outputs["raw.playlist_items"][0]["occurrence"] == 2
    assert ctx.outputs["raw.playlist_items"][0]["occurrence_key"].endswith("#2")
    # One rejected row of two is not drift; more than half would be.
    assert not misses and ctx.observed_count == ctx.yielded_count + len(ctx.rejected)


@pytest.mark.parametrize("source", SOURCES)
async def test_all_retained_strings_sanitized(source):
    ctx, target, entity, tracks, body = envelope(source)
    bad = "https://media.invalid/file?signature=SIGNED_SENTINEL&token=TOKEN_SENTINEL"
    header = entity["sections"][0]["items"][0] if source == "am_playlist" else entity
    header["name" if source == "sp_playlist_page" else "title"] = bad
    entity["description"] = bad
    t = tracks[0]["itemV2"]["data"] if source == "sp_playlist_page" else tracks[0]
    t["name" if source == "sp_playlist_page" else "title"] = bad
    await collect(ctx, target, body())
    retained = json.dumps([ctx.outputs, ctx.rejected, ctx.pending_cursors])
    assert "SIGNED_SENTINEL" not in retained and "TOKEN_SENTINEL" not in retained
    assert sanitize_playlist_output(
        {"names": ["authorization=SECRET", bad], "description": "?token=SECRET"}
    ) == {"names": ["[redacted]", "[redacted]"], "description": "[redacted]"}


@pytest.mark.parametrize("source", ["am_playlist", "sp_playlist_embed"])
async def test_cursor_variant_binding_discards_canonical_and_validators(source):
    ctx, target, _, _, body = envelope(source)
    await collect(ctx, target, body())
    cursor = deepcopy(ctx.pending_cursors[target.id])
    ctx.clear_page()
    ctx.cursors = {target.id: {"cursor_value": cursor}}
    target["platform_account_id"] = (
        "gb:" if source == "am_playlist" else "GB:"
    ) + target.platform_account_id.split(":")[-1]
    await collect(
        ctx, target, body().replace("music.apple.com/us/", "music.apple.com/gb/")
    )
    assert ctx.outputs["raw.playlist_items"]
    assert ctx.outputs["raw.playlist_snapshots"][0]["observation"] == "content"


async def test_one_registered_spotify_source_two_ordered_surfaces_one_group():
    assert (
        "sp_playlist_embed" not in discover() and "sp_playlist_page" not in discover()
    )
    ctx, target, _ = setup("sp_playlist")
    calls = []

    async def seen(request):
        calls.append(str(request.url))

    async with httpx.AsyncClient(
        transport=FixtureTransport([ROOT / "sp_playlist/fixtures/normal.jsonl"]),
        event_hooks={"request": [seen]},
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    snapshots = ctx.outputs["raw.playlist_snapshots"]
    assert len(snapshots) == 2
    assert "/embed/" in calls[0] and "/embed/" not in calls[1]
    assert snapshots[0]["observation_group"] == snapshots[1]["observation_group"]
    assert {s["stream"] for s in snapshots} == {"head"}
    assert set(ctx.pending_cursors[target.id]["surfaces"]) == {
        "sp_playlist_embed",
        "sp_playlist_page",
    }
    assert ctx.observed_count == ctx.yielded_count


def test_frozen_market_rejects_conflicting_override():
    with pytest.raises(ServiceError, match="new variant"):
        market_country({"market": "GB"}, "US")
    assert market_country({"market": "GB"}, "gb") == "gb"


async def test_real_http_transport_hides_headers_routes_proxy_and_meters_304(
    monkeypatch, caplog
):
    """httpcore HTTP/1.1 parser and proxy path, with only the network replaced."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("mdp_functions.http.draw", lambda *a: None)
    connected = []

    class NetworkStream:
        def __init__(self):
            self.done = False

        async def read(self, max_bytes, timeout=None):
            if self.done:
                return b""
            self.done = True
            return b"HTTP/1.1 304 Not Modified\r\nSet-Cookie: COOKIE_SENTINEL\r\nProxy-Authorization: CREDENTIAL_SENTINEL\r\n\r\n"

        async def write(self, buffer, timeout=None):
            pass

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            connected.append((host, port))
            return NetworkStream()

    real = httpx.AsyncHTTPTransport

    class ActualTransport(real):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._pool._network_backend = Backend()

    monkeypatch.setattr("httpx._client.AsyncHTTPTransport", ActualTransport)

    class Interim(httpx.AsyncBaseTransport):
        def is_synthetic(self, r):
            return False

        async def handle_async_request(self, r):
            pytest.fail("Live routing bypassed proxy")

    class Provider:
        name = "webshare"
        price_microcents_per_byte = Decimal(0)

        async def proxy_url(self, *args):
            return SecretStr("http://u:CREDENTIAL_SENTINEL@proxy.test:80")

        def redact(self, text):
            return text.replace("CREDENTIAL_SENTINEL", "[redacted]")

    ctx, run = context(transport="residential")
    ctx.target = Target(params_json={"market": "GB"})
    db = FakeDB()
    costs = []
    async with TracedClient(
        ctx, db, run, no_page, provider=Provider(), transport=Interim()
    ) as client:
        monkeypatch.setattr(client, "_bytes_cost", lambda rid, n: costs.append(n))
        response = await client.get("http://playlist.test/playlist")
    assert (
        response.status_code == 304 and response.extensions["mdp_tier"] == "residential"
    )
    assert connected == [("proxy.test", 80)]
    assert (
        "COOKIE_SENTINEL" not in caplog.text
        and "CREDENTIAL_SENTINEL" not in caplog.text
    )
    assert (
        db.rows[0][11] > 0 and db.rows[0][12] > 0 and costs == [sum(db.rows[0][11:13])]
    )


def test_header_text_cannot_leave_bearer_value_in_retained_fields():
    for field in ("title", "description", "owner_name", "added_by", "isrc"):
        cleaned = sanitize_playlist_output(
            {field: "Authorization: Bearer CREDENTIAL_SENTINEL"}
        )
        assert "CREDENTIAL_SENTINEL" not in json.dumps(cleaned)


@pytest.mark.parametrize(
    "scenario", ["normal", "partial", "drift", "not_found", "not_modified"]
)
async def test_paired_collector_accounts_for_every_scenario(scenario):
    ctx, target, _ = setup("sp_playlist")
    async with httpx.AsyncClient(
        transport=FixtureTransport([ROOT / f"sp_playlist/fixtures/{scenario}.jsonl"])
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)
    snapshots = ctx.outputs.get("raw.playlist_snapshots", [])
    if scenario in ("normal", "partial"):
        assert [s["fetch_surface"] for s in snapshots] == [
            "sp_playlist_embed",
            "sp_playlist_page",
        ]
        assert len({s["observation_group"] for s in snapshots}) == 1
    else:
        assert not snapshots and len(ctx.rejected) == 2


async def test_unrecoverable_identity_rejects_row_without_advancing_ordinals():
    ctx, target, _entity, tracks, body = envelope("am_playlist")
    tracks[:] = [deepcopy(tracks[0]), deepcopy(tracks[1]), deepcopy(tracks[0])]
    del tracks[0]["contentDescriptor"]
    misses = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body()))
    ) as client:
        client.envelope_miss = lambda *args: misses.append(args)
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    items = ctx.outputs["raw.playlist_items"]
    # The later duplicate keeps #1: whether the lost row was the same track is
    # unknowable, so the snapshot is partial and SQL derives no move from it.
    assert [(i["position"], i["occurrence"]) for i in items] == [(2, 1), (3, 1)]
    assert ctx.outputs["raw.playlist_snapshots"][0]["coverage"] == "partial"
    assert [r["record"]["position"] for r in ctx.rejected] == [1]
    assert not misses and ctx.observed_count == 4 == ctx.yielded_count + 1


def test_sanitizer_keeps_ordinary_text_and_drops_credentials():
    kept = [
        "Session: Live at Abbey Road",
        "Bearer of Bad News",
        "Who? What?",
        "Design = Art",
        "Secret: Love",
        "https://open.spotify.com/playlist/Synth1531769ee345db9a2",
    ]
    assert sanitize_playlist_output({"titles": kept}) == {"titles": kept}
    dropped = [
        "access_token: SENTINELabcdefghijklmnop",
        "Bearer SENTINELabcdefghijklmnopqrstuvwxyz",
        "listen //cdn.invalid/a.mp3?Expires=1&Signature=SENTINEL",
        "https://user:SENTINEL@host.invalid/x",
        "Cookie: sp_t=SENTINEL",
        "see sig=SENTINEL now",
        "api_key=SENTINEL",
    ]
    for text in dropped:
        assert "SENTINEL" not in json.dumps(sanitize_playlist_output({"t": text}))


async def test_large_parse_leaves_event_loop_responsive(monkeypatch):
    """A 1 MB Apple page parses off the loop while another request keeps answering."""
    ctx, target, _entity, tracks, body = envelope("am_playlist")
    tracks[:] = [deepcopy(tracks[i % len(tracks)]) for i in range(300)]
    page = body() + "<div>" + "<p>filler</p>" * 60_000 + "</div>"
    spans = []

    def timed(*args):
        start = time.perf_counter()
        try:
            return observe(*args)
        finally:
            spans.append((start, time.perf_counter()))

    monkeypatch.setattr(playlist, "observe", timed)

    async def service(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def serve_during_parse():
        """Requests answered while the parse ran, and the longest wait between two."""
        ctx.clear_page()
        answered = []
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, text=page))
            ) as source,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=service), base_url="http://svc"
            ) as other,
        ):
            ctx.http = source
            task = asyncio.create_task(ctx.manifest.function(ctx, [target]))
            answered.append(time.perf_counter())
            while not task.done():
                await asyncio.sleep(0.005)
                assert (await other.get("/health")).status_code == 200
                answered.append(time.perf_counter())
            await task
        assert len(ctx.outputs["raw.playlist_items"]) == 300
        start, end = spans[-1]
        gap = max(b - a for a, b in pairwise(answered))
        return sum(start < t < end for t in answered), gap, end - start

    during, gap, parse = await serve_during_parse()
    # Timing on a shared machine varies; the structural claim is that the loop keeps
    # answering through the parse and never waits for the whole of it.
    assert during >= 2 and gap < 0.6 * parse, (during, gap, parse)

    async def inline(fn, *args):
        return fn(*args)

    # The same parse on the loop answers nothing until it finishes.
    monkeypatch.setattr(asyncio, "to_thread", inline)
    during, gap, parse = await serve_during_parse()
    assert during == 0 and gap >= parse, (during, gap, parse)


async def test_unchanged_embed_still_pairs_with_its_page():
    ctx, target, _ = setup("sp_playlist")
    async with httpx.AsyncClient(
        transport=FixtureTransport([ROOT / "sp_playlist/fixtures/normal.jsonl"])
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
        first = ctx.outputs["raw.playlist_snapshots"][0]
        cursor = deepcopy(ctx.pending_cursors[target.id])
        ctx.clear_page()
        ctx.cursors = {target.id: {"cursor_value": cursor}}
        await ctx.manifest.function(ctx, [target])
    embed, page = ctx.outputs["raw.playlist_snapshots"]
    assert embed["observation"] == "unchanged"
    assert embed["content_ref"] == first["snapshot_id"]
    assert page["observation"] == "content"
    assert embed["observation_group"] == page["observation_group"]
    assert embed["observation_group"] != first["observation_group"]


async def test_every_collector_parses_in_a_worker_thread(monkeypatch):
    """Apple, Spotify, and Billboard hand their page parse to asyncio.to_thread."""
    from test_billboard import FIXTURES
    from test_billboard import parse as billboard

    offloaded = []
    real = asyncio.to_thread

    async def record(fn, *args):
        offloaded.append(fn.__name__)
        return await real(fn, *args)

    monkeypatch.setattr(asyncio, "to_thread", record)
    for source in ("am_playlist", "sp_playlist"):
        ctx, target, _ = setup(source)
        fixture = (
            "am_playlist/fixtures"
            if source == "am_playlist"
            else "sp_playlist/fixtures"
        )
        async with httpx.AsyncClient(
            transport=FixtureTransport([ROOT / fixture / "normal.jsonl"])
        ) as client:
            ctx.http = client
            await ctx.manifest.function(ctx, [target])
    html = gzip.decompress(
        (FIXTURES / "synthetic-hot100-2026-09-18.html.gz").read_bytes()
    ).decode()
    rows, _ = await billboard(html)
    assert len(rows) == 100
    assert offloaded == ["observe", "observe", "observe", "shape_chart"]
