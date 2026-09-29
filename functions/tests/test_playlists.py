"""Sanitized recon envelopes, output accounting, references and secret exclusion."""

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from bs4 import BeautifulSoup
from mdp_functions.http import FixtureTransport
from mdp_functions.layers import Ctx, Target
from mdp_functions.playlist import UA
from mdp_functions.registry import discover

SOURCES = ["am_playlist", "sp_playlist_embed", "sp_playlist_page"]
ROOT = Path(__file__).parents[1] / "src/mdp_functions/sources"


def setup(source, scenario="normal", cursor=None):
    # The retired surface keys keep their single-surface fixtures under sp_playlist.
    fixtures = {
        "sp_playlist": ROOT / "sp_playlist/fixtures/surfaces/embed",
        "sp_playlist_embed": ROOT / "sp_playlist/fixtures/surfaces/embed",
        "sp_playlist_page": ROOT / "sp_playlist/fixtures/surfaces/page",
    }.get(source, ROOT / source / "fixtures")
    path = fixtures / (scenario + ".jsonl")
    fixture = json.loads(path.read_text())
    url = fixture["request"]["url"]
    target = Target(
        id="fixture-target",
        platform="apple_music" if source == "am_playlist" else "spotify",
        platform_account_id=("us:" if source == "am_playlist" else "")
        + url.split("/")[-1],
        handle=url.split("/")[-2] if source == "am_playlist" else url.split("/")[-1],
    )
    manifest = discover()["sp_playlist" if source.startswith("sp_") else source]
    if source in ("sp_playlist_embed", "sp_playlist_page"):
        from mdp_functions.playlist import collect

        async def surface_only(ctx, batch):
            await collect(ctx, batch, source)

        manifest = replace(manifest, function=surface_only)
    ctx = Ctx(
        manifest,
        {"id": uuid4(), "cycle_id": uuid4()},
        {"fixture-target": {"cursor_value": cursor}} if cursor else None,
    )
    return ctx, target, path


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(
    "scenario", ["normal", "not_modified", "partial", "drift", "not_found"]
)
async def test_scenarios(source, scenario):
    ctx, target, path = setup(
        source,
        scenario,
        {
            "validators": {
                "etag": {
                    "value": "old",
                    "snapshot_id": "previous-content",
                    "fetch_surface": source,
                    "membership_hash": "previous-membership",
                    "content_hash": "previous-content-hash",
                    "track_count_reported": 3,
                    "continuation": False,
                    "coverage": "full",
                }
            },
        },
    )
    binding = {
        "platform": target.platform,
        "playlist_id": target.platform_account_id.split(":")[-1],
        "variant": "us" if source == "am_playlist" else "US",
        "stream": "full" if source == "am_playlist" else "head",
        "fetch_surface": source,
    }
    ctx.cursors[target.id]["cursor_value"]["binding"] = binding
    ctx.cursors[target.id]["cursor_value"]["validators"]["etag"].update(binding)
    calls = []

    async def inspect(request):
        calls.append(request)

    async with httpx.AsyncClient(
        transport=FixtureTransport([path]), event_hooks={"request": [inspect]}
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)
    assert ctx.manifest.keep_payload is False and not ctx.payloads
    assert calls[0].headers["User-Agent"] == UA
    if source == "am_playlist":
        assert calls[0].headers["If-None-Match"] == "old"
    if scenario in ("drift", "not_found") or (
        scenario == "not_modified" and source != "am_playlist"
    ):
        assert not ctx.outputs
        assert ctx.rejected[0]["reason"].startswith("envelope_mismatch:" + source + ":")
    else:
        snapshot = ctx.outputs["raw.playlist_snapshots"][0]
        items = ctx.outputs.get("raw.playlist_items", [])
        assert snapshot["items_observed"] == len(items)
        assert all(i["snapshot_id"] == snapshot["snapshot_id"] for i in items)
        assert all(i["variant"] == snapshot["variant"] for i in items)
        if scenario == "not_modified":
            assert not items and snapshot["observation"] == "unchanged"
            assert snapshot["content_ref"] == "previous-content"
        elif scenario == "partial":
            assert snapshot["coverage"] == (
                "full" if source == "sp_playlist_embed" else "partial"
            )
            if source == "sp_playlist_embed":
                assert len(items) == 100
        if items:
            assert list(ctx.outputs)[-1] == "raw.playlist_snapshots"


async def test_embed_unchanged_and_no_tokens():
    ctx, target, path = setup("sp_playlist_embed")
    fixture = json.loads(path.read_text())
    soup = BeautifulSoup(fixture["response"]["body"], "html.parser")
    tag = soup.select_one("#__NEXT_DATA__")
    data = json.loads(tag.text)
    data["props"]["pageProps"]["state"]["settings"] = {
        "session": {"accessToken": "TOKEN_SENTINEL"}
    }
    entity = data["props"]["pageProps"]["state"]["data"]["entity"]
    entity["accessToken"] = "TOKEN_SENTINEL"
    entity["attributes"].append({"key": "access_token", "value": "TOKEN_SENTINEL"})
    entity["trackList"][0]["audioPreview"] = {
        "url": "https://media.invalid/?token=TOKEN_SENTINEL"
    }
    tag.string = json.dumps(data)
    body = str(soup)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
        previous = ctx.outputs["raw.playlist_snapshots"][0]["snapshot_id"]
        cursor = ctx.pending_cursors[target.id]
        assert "TOKEN_SENTINEL" not in json.dumps(
            [ctx.outputs, ctx.payloads, ctx.rejected, cursor]
        )
        ctx.clear_page()
        ctx.cursors = {target.id: {"cursor_value": cursor}}
        await ctx.manifest.function(ctx, [target])
    assert ctx.observed_count == ctx.yielded_count == 1
    assert ctx.outputs["raw.playlist_snapshots"][0]["content_ref"] == previous
    assert "raw.playlist_items" not in ctx.outputs


async def test_apple_single_redirect_and_duplicate_positions():
    ctx, target, path = setup("am_playlist")
    fixture = json.loads(path.read_text())
    original = fixture["request"]["url"]
    canonical = original.replace("/todays-hits/", "/canonical/")
    soup = BeautifulSoup(fixture["response"]["body"], "html.parser")
    tag = soup.select_one("#serialized-server-data")
    data = json.loads(tag.text)
    data["data"][0]["data"]["canonicalURL"] = canonical
    tracks = data["data"][0]["data"]["sections"][1]["items"]
    tracks.append(tracks[0])
    tag.string = json.dumps(data)

    def respond(request):
        return (
            httpx.Response(301, headers={"location": canonical})
            if str(request.url) == original
            else httpx.Response(200, text=str(soup), headers={"etag": "new"})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    items = ctx.outputs["raw.playlist_items"]
    assert items[-1]["occurrence"] == 2
    assert items[-1]["occurrence_key"] != items[0]["occurrence_key"]
    assert ctx.pending_cursors[target.id]["canonical_url"] == canonical
    assert ctx.pending_cursors[target.id]["validators"]["etag"]["value"] == "new"


@pytest.mark.parametrize("source", SOURCES)
async def test_malformed_row_rejects_whole_observation_without_raw_body(source):
    ctx, target, path = setup(source)
    fixture = json.loads(path.read_text())
    # Wrong envelope carries a simulated token but must never be retained as a rejected record.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text="TOKEN_SENTINEL")
        )
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert ctx.observed_count == len(ctx.rejected) == 1
    assert "TOKEN_SENTINEL" not in json.dumps([ctx.outputs, ctx.payloads, ctx.rejected])
    assert fixture["response"]["status"] == 200


@pytest.mark.docker
async def test_shared_tables_land_through_runtime(rt, databases):
    import psycopg
    from mdp_functions.targets import export_targets

    # One active target per source, because the recon examples name different playlists.
    for source in ["am_playlist", "sp_playlist"]:
        _, target, _ = setup(source)
        with psycopg.connect(databases["admin_control"]) as conn:
            set_id = conn.execute(
                "INSERT INTO control.target_set(kind,name) VALUES ('playlist','playlist-fixture') ON CONFLICT(kind,tenant_id) DO UPDATE SET name=EXCLUDED.name RETURNING id"
            ).fetchone()[0]
            conn.execute(
                "UPDATE control.target SET deactivated_at=now() WHERE target_set_id=%s",
                (set_id,),
            )
            conn.execute(
                "INSERT INTO control.target(target_set_id,platform,platform_account_id,handle,resolution_status,activated_at) VALUES (%s,%s,%s,%s,'resolved',now()) ON CONFLICT(target_set_id,platform,platform_account_id) WHERE resolution_status='resolved' DO UPDATE SET deactivated_at=NULL",
                (set_id, target.platform, target.platform_account_id, target.handle),
            )
        dbt_id = "local:" + uuid4().hex
        binding = await rt.cycles.bind_cycle(
            "daily", "global", dbt_id, "scheduled", "local:daily", runner="core"
        )
        export_targets(rt.db, rt.warehouse, binding["cycle_id"], "playlist")
        run = rt.admit(source, cadence="daily", dbt_run_id=dbt_id)
        await rt.execute(run["id"])
        result = rt.receipts(run["id"])
        assert result["run"]["status"] == "succeeded", result
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            snapshot = conn.execute(
                "SELECT snapshot_id,items_observed FROM raw.playlist_snapshots WHERE _run_id=%s",
                (run["id"],),
            ).fetchone()
            count = conn.execute(
                "SELECT count(*) FROM raw.playlist_items WHERE snapshot_id=%s",
                (snapshot[0],),
            ).fetchone()[0]
            assert snapshot[1] == count > 0
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert (
            conn.execute(
                "SELECT count(distinct _source_key) FROM raw.playlist_snapshots"
            ).fetchone()[0]
            == 2
        )


async def test_304_after_partial_does_not_resurrect_old_full_content():
    cursor = None
    snapshots = []
    for scenario in ("normal", "partial", "not_modified", "not_modified"):
        ctx, target, path = setup("am_playlist", scenario, cursor)
        async with httpx.AsyncClient(transport=FixtureTransport([path])) as client:
            ctx.http = client
            await ctx.manifest.function(ctx, [target])
        snapshot = ctx.outputs["raw.playlist_snapshots"][0]
        snapshots.append(snapshot)
        cursor = ctx.pending_cursors.get(target.id, cursor)
        if scenario != "not_modified":
            for validator in cursor["validators"].values():
                assert validator["snapshot_id"] == snapshot["snapshot_id"]
                assert validator["fetch_surface"] == "am_playlist"
        else:
            assert snapshot["content_ref"] == snapshots[1]["snapshot_id"]
            assert snapshot["content_ref"] != snapshots[0]["snapshot_id"]
            assert not ctx.outputs.get("raw.playlist_items")


@pytest.mark.parametrize(
    "cursor",
    [
        {"etag": "legacy", "etag_content_ref": "old-full"},
        {"validators": {"etag": {"value": "unbound", "fetch_surface": "am_playlist"}}},
        {
            "validators": {
                "etag": {
                    "value": "wrong-surface",
                    "snapshot_id": "old",
                    "fetch_surface": "sp_playlist_page",
                }
            }
        },
    ],
)
async def test_apple_does_not_use_unbound_or_cross_surface_validator(cursor):
    ctx, target, _ = setup("am_playlist", cursor=cursor)

    def respond(request):
        assert "If-None-Match" not in request.headers
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert not ctx.outputs and not ctx.pending_cursors
    assert ctx.observed_count == len(ctx.rejected) == 1


@pytest.mark.parametrize("source", ["sp_playlist_embed", "sp_playlist_page"])
async def test_uidless_reorder_changes_hash_and_never_short_circuits(source):
    ctx, target, path = setup(source)
    soup = BeautifulSoup(
        json.loads(path.read_text())["response"]["body"], "html.parser"
    )
    if source == "sp_playlist_embed":
        tag = soup.select_one("#__NEXT_DATA__")
        data = json.loads(tag.text)
        tracks = data["props"]["pageProps"]["state"]["data"]["entity"]["trackList"]
        for track in tracks:
            track.pop("uid", None)
    else:
        tag = soup.select_one("#initialState")
        data = json.loads(base64.b64decode(tag.text))
        tracks = data["entities"]["items"][
            "spotify:playlist:" + target.platform_account_id
        ]["content"]["items"]
    # Include a duplicate so fallback identity must include its nth appearance.
    tracks.append(tracks[0].copy())
    hashes = []
    for reorder in (False, False, True):
        if reorder:
            tracks[0], tracks[1] = tracks[1], tracks[0]
        tag.string = (
            json.dumps(data)
            if source == "sp_playlist_embed"
            else base64.b64encode(json.dumps(data).encode()).decode()
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=str(soup)))
        ) as client:
            ctx.http = client
            await ctx.manifest.function(ctx, [target])
        snapshot = ctx.outputs["raw.playlist_snapshots"][0]
        items = ctx.outputs["raw.playlist_items"]
        assert snapshot["observation"] == "content"
        assert all(i["occurrence_inferred"] for i in items)
        assert any(i["occurrence"] == 2 for i in items)
        ordered = [(i["position"], i["occurrence_key"]) for i in items]
        digest = hashlib.md5(
            json.dumps(ordered, separators=(",", ":")).encode()
        ).hexdigest()
        assert snapshot["snapshot_hash"] == digest
        hashes.append(digest)
        cursor = ctx.pending_cursors[target.id]
        assert {
            k: cursor["validators"]["snapshot_hash"][k]
            for k in ("value", "snapshot_id", "fetch_surface")
        } == {
            "value": digest,
            "snapshot_id": snapshot["snapshot_id"],
            "fetch_surface": source,
        }
        ctx.clear_page()
        ctx.cursors = {target.id: {"cursor_value": cursor}}
    assert hashes[0] == hashes[1] != hashes[2]


async def test_embed_validator_tracks_exact_partial_content_across_unchanged_checks():
    cursor = None
    content_id = None
    for scenario in ("normal", "partial", "partial", "partial"):
        ctx, target, path = setup("sp_playlist_embed", scenario, cursor)
        async with httpx.AsyncClient(transport=FixtureTransport([path])) as client:
            ctx.http = client
            await ctx.manifest.function(ctx, [target])
        snapshot = ctx.outputs["raw.playlist_snapshots"][0]
        if scenario == "partial" and content_id:
            assert snapshot["observation"] == "unchanged"
            assert snapshot["content_ref"] == content_id
            assert not ctx.outputs.get("raw.playlist_items")
        elif scenario == "partial":
            assert snapshot["observation"] == "content"
            content_id = snapshot["snapshot_id"]
        cursor = ctx.pending_cursors[target.id]
        assert cursor["validators"]["snapshot_hash"]["snapshot_id"] == (
            content_id or snapshot["snapshot_id"]
        )


@pytest.mark.parametrize("binding", ["legacy", "missing_snapshot", "wrong_surface"])
async def test_embed_requires_bound_surface_hash(binding):
    ctx, target, path = setup("sp_playlist_embed")
    async with httpx.AsyncClient(transport=FixtureTransport([path])) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    cursor = ctx.pending_cursors[target.id]
    validator = cursor["validators"]["content_hash"]
    if binding == "legacy":
        cursor = {
            "snapshot_hash": validator["value"],
            "last_full_hash": validator["value"],
            "last_full_content": validator["snapshot_id"],
        }
    elif binding == "missing_snapshot":
        del validator["snapshot_id"]
    else:
        validator["fetch_surface"] = "sp_playlist_page"
    ctx, target, path = setup("sp_playlist_embed", cursor=cursor)
    async with httpx.AsyncClient(transport=FixtureTransport([path])) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert ctx.outputs["raw.playlist_snapshots"][0]["observation"] == "content"
    assert ctx.outputs["raw.playlist_items"]


async def test_apple_disabled_song_still_counts_without_duration():
    ctx, target, path = setup("am_playlist")
    fixture = json.loads(path.read_text())
    soup = BeautifulSoup(fixture["response"]["body"], "html.parser")
    tag = soup.select_one("#serialized-server-data")
    data = json.loads(tag.text)
    track = data["data"][0]["data"]["sections"][1]["items"][0]
    del track["duration"]
    track["isDisabled"] = True
    tag.string = json.dumps(data)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=str(soup)))
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert ctx.outputs["raw.playlist_items"][0]["duration_ms"] is None
    assert not ctx.rejected


async def test_null_apple_section_is_a_balanced_envelope_rejection():
    ctx, target, _ = setup("am_playlist")
    body = '<script id="serialized-server-data">{"data":[{"data":{"sections":[null]}}]}</script>'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body))
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    assert ctx.observed_count == len(ctx.rejected) == 1
    assert ctx.rejected[0]["reason"] == "envelope_mismatch:am_playlist:record"
    assert not ctx.outputs
