"""Tests for playlist publication."""

import base64
import json
from copy import deepcopy

import httpx
import psycopg
import pytest
from bs4 import BeautifulSoup
from conftest import bound
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.detectors import classify
from mdp_functions.fetch.providers import Webshare, provider_scope_id
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx, Target, Targets, bronze
from mdp_functions.registry import REGISTRY, sync
from pydantic import SecretStr
from test_fetch_runtime import no_page
from test_playlists import SOURCES, setup


def envelope(source, scenario="normal"):
    ctx, target, path = setup(source, scenario)
    soup = BeautifulSoup(
        json.loads(path.read_text())["response"]["body"], "html.parser"
    )
    selector = {
        "am_playlist": "#serialized-server-data",
        "sp_playlist_embed": "#__NEXT_DATA__",
        "sp_playlist_page": "#initialState",
    }[source]
    tag = soup.select_one(selector)
    data = json.loads(
        base64.b64decode(tag.text) if source == "sp_playlist_page" else tag.text
    )
    if source == "am_playlist":
        entity = data["data"][0]["data"]
        tracks = entity["sections"][1]["items"]
    elif source == "sp_playlist_embed":
        entity = data["props"]["pageProps"]["state"]["data"]["entity"]
        tracks = entity["trackList"]
    else:
        entity = data["entities"]["items"][
            "spotify:playlist:" + target.platform_account_id
        ]
        tracks = entity["content"]["items"]

    def body():
        raw = json.dumps(data)
        tag.string = (
            base64.b64encode(raw.encode()).decode()
            if source == "sp_playlist_page"
            else raw
        )
        return str(soup)

    return ctx, target, entity, tracks, body


async def collect(ctx, target, body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, text=body, extensions={"mdp_tier": "residential"}
            )
        )
    ) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    return ctx.outputs["raw.playlist_snapshots"][0]


@pytest.mark.parametrize("source", SOURCES)
async def test_surface_secret_boundary_and_individual_accounting(source, caplog):
    ctx, target, entity, tracks, body = envelope(source)
    caplog.set_level("DEBUG")
    token, cookie, signed = (
        "TOKEN_SENTINEL",
        "session=COOKIE_SENTINEL",
        "https://media.invalid/file?sig=SIGNED_SENTINEL",
    )
    entity.update(
        settings={"session": {"accessToken": token}}, cookie=cookie, preview=signed
    )
    entity["attributes"] = [
        {"key": k, "value": v}
        for k, v in [
            ("preview", signed),
            ("token", token),
            ("cookie", cookie),
            ("artistGid", signed),
            ("isAlgotorial", token),
            ("new_entries_count", cookie),
            ("editorial_series", signed),
        ]
    ]
    for t in tracks:
        t.update(accessToken=token, cookie=cookie, preview=signed)
    bad = tracks[1] if source != "sp_playlist_page" else tracks[1]["itemV2"]["data"]
    bad.pop("name" if source == "sp_playlist_page" else "title")
    snapshot = await collect(ctx, target, body())
    assert snapshot["coverage"] == "partial" and snapshot["tier"] == "residential"
    assert (
        ctx.observed_count == 1 + len(tracks) == ctx.yielded_count + len(ctx.rejected)
    )
    assert len(ctx.outputs["raw.playlist_items"]) == len(tracks) - 1
    assert [i["position"] for i in ctx.outputs["raw.playlist_items"]] == [1, 3]
    assert ctx.rejected[0]["record"]["position"] == 2
    with pytest.raises(ServiceError):
        ctx.require(entity, "missing.path", source)
    retained = (
        json.dumps([ctx.outputs, ctx.rejected, ctx.payloads, ctx.pending_cursors])
        + caplog.text
    )
    assert all(s not in retained for s in (token, cookie, signed, "SIGNED_SENTINEL"))


@pytest.mark.parametrize("counts", [(101, 100), (100, 101)])
async def test_identical_prefix_different_coverage_publishes_content(counts):
    ctx, target, entity, _tracks, body = envelope("sp_playlist_embed", "partial")
    previous = None
    for count in counts:
        entity["totalCount"] = count
        snapshot = await collect(ctx, target, body())
        assert snapshot["observation"] == "content"
        assert len(ctx.outputs["raw.playlist_items"]) == 100
        assert snapshot["coverage"] == "full"  # complete for the head stream
        if previous:
            assert snapshot["content_hash"] == previous["content_hash"]
            assert snapshot["snapshot_id"] != previous["snapshot_id"]
        previous = snapshot
        cursor = deepcopy(ctx.pending_cursors[target.id])
        ctx.clear_page()
        ctx.cursors = {target.id: {"cursor_value": cursor}}


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Changed"),
        ("subtitle", "Changed artist"),
        ("duration", 42),
        ("uri", "spotify:track:other"),
    ],
)
async def test_metadata_change_with_stable_uid_publishes_content(field, value):
    ctx, target, _, tracks, body = envelope("sp_playlist_embed")
    previous = await collect(ctx, target, body())
    cursor = deepcopy(ctx.pending_cursors[target.id])
    ctx.clear_page()
    ctx.cursors = {target.id: {"cursor_value": cursor}}
    tracks[0][field] = value
    snapshot = await collect(ctx, target, body())
    assert snapshot["membership_hash"] == previous["membership_hash"]
    assert snapshot["content_hash"] != previous["content_hash"]
    assert snapshot["observation"] == "content"


@pytest.mark.parametrize("source", ["sp_playlist_embed", "sp_playlist_page"])
@pytest.mark.parametrize(
    "qualified,market,valid",
    [
        (True, "GB", True),
        (True, "US", False),
        (False, "GB", False),
        (False, "US", True),
    ],
)
async def test_market_identity(source, qualified, market, valid):
    ctx, target, path = setup(source)
    bare = target.platform_account_id
    target["platform_account_id"] = "GB:" + bare if qualified else bare
    target["params_json"] = {"market": market}
    calls = []

    def response(r):
        calls.append(str(r.url))
        return httpx.Response(
            200, text=json.loads(path.read_text())["response"]["body"]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        ctx.http = client
        await ctx.manifest.function(ctx, [target])
    if valid:
        assert calls[0].endswith("/" + bare)
        assert all(
            r["variant"] == market for rows in ctx.outputs.values() for r in rows
        )
    else:
        assert not calls and ctx.rejected[0]["reason"] == "variant_mismatch"


def test_checked_in_drift_is_not_soft_shell():
    for source in SOURCES:
        _, _, path = setup(source, "drift")
        body = json.loads(path.read_text())["response"]["body"]
        assert (
            classify(httpx.Response(200, text=body), envelope_missing=True).kind == "ok"
        )


@pytest.mark.docker
async def test_terminal_gone_retry_never_refetches(rt):
    calls = []

    @bronze(
        source_key="gone_review", writes=["raw.gone_review"], targets=Targets("account")
    )
    async def source(ctx, targets):
        await ctx.http.get("https://gone.test/" + str(targets[0].id))

    try:
        sync(rt.db)

        def gone(request):
            calls.append(request)
            return httpx.Response(410)

        rt.transport = httpx.MockTransport(gone)
        dbt_id, run = await bound(rt, "gone_review")
        await rt.execute(run["id"])
        n = len(calls)
        assert n == 2
        retry = rt.admit("gone_review", dbt_run_id=dbt_id)
        await rt.execute(retry["id"])
        result = rt.receipts(retry["id"])["run"]
        assert len(calls) == n and result["coverage"] == "partial"
        assert result["rows_rejected"] == 2
    finally:
        REGISTRY.pop("gone_review", None)


@pytest.mark.docker
async def test_zero_price_byte_budget(rt, databases):
    _, run = await bound(rt)
    ctx = Ctx(REGISTRY["fixture_accounts"], run)
    async with TracedClient(
        ctx, rt.db, run, no_page, provider=Webshare(SecretStr("unused"))
    ) as client:
        client._bytes_cost("zero-tariff", 123)
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute(
                "INSERT INTO control.budget(scope,scope_id,period,cap_cents,cap_bytes,soft_pct,hard_action) VALUES ('provider',%s,'monthly',9999,123,80,'warn')",
                (provider_scope_id("webshare"),),
            )
        with pytest.raises(ServiceError, match="Provider budget exhausted"):
            client._provider_budget()


def test_unrecovered_export_refused():
    with pytest.raises(ServiceError) as error:
        Target.from_export({"target_json": {}, "spec_recovered": False})
    assert error.value.error_class == "export_spec_missing"


@pytest.mark.docker
async def test_runtime_refuses_missing_historical_spec(rt, databases):
    _, run = await bound(rt)
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute(
            "UPDATE control.target_export_member SET spec_recovered=false WHERE revision_id=%s",
            (run["revision_id"],),
        )
    from mdp_functions.targets import export_targets

    with pytest.raises(ServiceError) as missing:
        export_targets(rt.db, rt.warehouse, run["cycle_id"])
    assert missing.value.error_class == "export_spec_missing"
    rt.transport = httpx.MockTransport(
        lambda r: pytest.fail("Unrecovered target fetched")
    )
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    assert result["error_class"] == "export_spec_missing" and result["status"] in {
        "failed",
        "partial",
    }
    assert not rt.db.all(
        "SELECT * FROM control.call_ledger WHERE run_id=%s", (run["id"],)
    )


def test_attribute_allowlist_scalar_types_and_urls():
    from mdp_functions.playlist import sanitize_playlist_output

    good = [
        {"key": "isAlgotorial", "value": True},
        {"key": "new_entries_count", "value": 12},
        {"key": "editorial_series", "value": "Fresh Finds"},
        {"key": "rank_type", "value": "daily"},
        {"key": "last_updated", "value": "2026-09-23"},
        {"key": "artistGid", "value": "abc123"},
        {"key": "autoplay", "value": "spotify:playlist:abc123"},
    ]
    bad = [
        {"key": "preview", "value": "anything"},
        {"key": "new_entries_count", "value": -1},
        {"key": "artistGid", "value": {"token": "secret"}},
        {"key": "editorial_series", "value": "https:secret"},
        {"key": "last_updated", "value": "https://signed.invalid/?token=secret"},
    ]
    result = sanitize_playlist_output({"attributes": good + bad})["attributes"]
    assert len(result) == len(good)
    assert result[0]["value"] == "true" and result[1]["value"] == "12"


async def test_continuation_change_forces_new_content():
    ctx, target, entity, _tracks, body = envelope("sp_playlist_embed")
    entity["totalCount"] = 3
    entity["continuation"] = True
    previous = await collect(ctx, target, body())
    assert previous["coverage"] == "partial"
    cursor = deepcopy(ctx.pending_cursors[target.id])
    ctx.clear_page()
    ctx.cursors = {target.id: {"cursor_value": cursor}}
    entity["continuation"] = False
    current = await collect(ctx, target, body())
    assert current["coverage"] == "full" and current["observation"] == "content"
    assert current["content_hash"] == previous["content_hash"]
