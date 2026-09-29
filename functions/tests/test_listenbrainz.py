"""Tests for listenbrainz."""

import csv
import json
import os
from datetime import date
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import bound
from free_source_fixture import (
    ACTS,
    ALGORITHM,
    LB_KEYS,
    REPO,
    SIMILARITY,
    collect,
    global_input,
    on_lb_allowlist,
    registry,
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import forbidden
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx
from mdp_functions.registry import discover
from test_fetch_runtime import FakeDB, no_page


def test_listenbrainz_keys_ship_disabled_on_their_registry_rows():
    catalog, rights = discover(), registry()
    for key in LB_KEYS:
        manifest = catalog[key]
        assert manifest.knobs["enabled"] is False and manifest.hosts == ["api.listenbrainz.org"], key
        row = rights[key]
        assert (row["license_ref"], row["learning_eligible"], row["resale_permitted"]) == ("unverified", "false", "false"), key
    popularity = catalog["lb_popularity"]
    assert popularity.layer == "gold" and not popularity.tenant_bound and popularity.knobs["allow_partial"] is True
    assert popularity.reads == ["intermediate.int_lb__popularity_inputs"] and popularity.provider is None
    assert popularity.input_version == ["mb_artist_gid", "popularity_day"]
    assert (rights["lb_popularity"]["category"], rights["lb_sitewide"]["category"]) == ("public charts and catalogs", "public charts and catalogs")
    assert (catalog["lb_sitewide"].cadence, catalog["lb_fresh_releases"].cadence) == ("weekly", "daily")


@pytest.mark.parametrize("scenario", ["normal", "partial", "not_found", "drift"])
async def test_lb_popularity_posts_one_act_and_lands_its_totals(scenario):
    ctx, calls, refused = await collect("lb_popularity", scenario, ACTS)
    rows = ctx.outputs.get("raw.lb_popularity", [])
    assert all(c.method == "POST" and on_lb_allowlist(c.url) for c in calls)
    assert [json.loads(c.content)["artist_mbids"] for c in calls] == [[a["mb_artist_gid"]] for a in ACTS]
    totals = [(r["mbid"][-1], r["total_listen_count"], r["total_user_count"]) for r in rows]
    if scenario == "normal":
        assert totals == [("1", 12000, 340), ("2", 24000, 680)] and not refused
    elif scenario == "partial":
        # No data for the artist lands as nulls: an observation, never a zero.
        assert totals == [("1", 12000, 340), ("2", None, None)]
    elif scenario == "not_found":
        assert totals == [("1", 12000, 340)] and refused == ["vendor_4xx"]
    else:
        assert not rows and [r["reason"] for r in ctx.rejected] == ["envelope_mismatch:lb_popularity:0"] * 2


@pytest.mark.parametrize("scenario", ["normal", "partial", "drift"])
async def test_lb_sitewide_lands_each_top_list_with_its_window(scenario):
    ctx, calls, _ = await collect("lb_sitewide", scenario, [{}])
    rows = ctx.outputs.get("raw.lb_sitewide", [])
    assert [c.url.path for c in calls] == ["/1/stats/sitewide/artists", "/1/stats/sitewide/recordings",
                                           "/1/stats/sitewide/release-groups"]
    assert all(on_lb_allowlist(c.url) and c.url.params["range"] == "week" for c in calls)
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)
    if scenario == "drift":
        assert not rows and len(ctx.rejected) == 3
        return
    assert [(r["entity_type"], r["rank"]) for r in rows] == [("artist", 1), ("artist", 2), ("recording", 1), ("release_group", 1)]
    assert {r["last_updated"].isoformat() for r in rows} == {"2026-09-23T04:00:39+00:00"}
    assert rows[2]["artist_mbids"] == json.dumps(["00000000-0000-4000-a000-000000000001"])
    assert [r["reason"] for r in ctx.rejected] == ([] if scenario == "normal" else ["drift:lb_sitewide:recordings:listen_count"])


@pytest.mark.parametrize("scenario", ["normal", "partial", "not_found", "drift"])
async def test_lb_fresh_releases_never_land_tags(scenario):
    ctx, calls, refused = await collect("lb_fresh_releases", scenario, [{}])
    rows = ctx.outputs.get("raw.lb_fresh_releases", [])
    assert all(on_lb_allowlist(c.url) and c.url.params["days"] == "3" for c in calls)
    assert not any("tag" in json.dumps(r, default=str) for r in rows)
    if scenario in ("normal", "partial"):
        assert [r["release_name"] for r in rows] == ["Fixture Single", "Fixture Album"]
        assert rows[0]["release_date"] == date(2026, 9, 23) and rows[0]["artist_mbids"] == json.dumps(["00000000-0000-4000-a000-000000000001"])
        assert len(ctx.rejected) == (0 if scenario == "normal" else 1)
    elif scenario == "not_found":
        assert refused == ["stale_target"] and not rows
    else:
        assert not rows and [r["reason"] for r in ctx.rejected] == ["envelope_mismatch:lb_fresh_releases:payload.releases"]


async def test_disabled_listenbrainz_keys_return_paused_receipts(rt, databases):

    from conftest import url_database
    from mdp_functions.registry import REGISTRY
    from mdp_functions.streamline_defaults import seed_streamline_defaults
    from psycopg.conninfo import conninfo_to_dict

    control = url_database(os.environ.get("MDP_CONTROL_RT_URL") or os.environ["MDP_CONTROL_RT_DATABASE_URL"],
                           conninfo_to_dict(databases["control_url"])["dbname"])
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("DELETE FROM control.audit_log WHERE action='streamline_defaults' AND subject=ANY(%s)", (list(LB_KEYS),))
    with psycopg.connect(databases["control_url"]) as functions, psycopg.connect(control) as rt_conn:
        seed_streamline_defaults(functions, rt_conn, {k: REGISTRY[k] for k in LB_KEYS})
    for key in ("lb_sitewide", "lb_fresh_releases"):
        _, run = await bound(rt, key)
        await rt.execute(run["id"])
        result = rt.receipts(run["id"])
        assert result["run"]["error_class"] == "paused" and [(r["status"], r["coverage"]) for r in result["receipts"]] == [
            ("paused", "empty")], key
    _, dbt_id, relation = await global_input(
        rt, databases, "lb_fixture", "int_lb__popularity_inputs",
        "mb_artist_gid text, popularity_day date",
        [(ACTS[0]["mb_artist_gid"], date(2026, 9, 24), '["mb_spine"]', "ref-1", "ver-1")])
    run = rt.admit("lb_popularity", dbt_run_id=dbt_id, input_relation=relation)
    await rt.execute(run["id"])
    assert rt.receipts(run["id"])["run"]["error_class"] == "paused"
    assert rt.db.one("SELECT count(*) AS n FROM control.call_ledger WHERE run_id=%s", (run["id"],))["n"] == 0


def test_lb_similar_artists_stays_learn_false_and_disabled_under_its_seeded_algorithm():
    manifest, row = discover()["lb_similar_artists"], registry()["lb_similar_artists"]
    assert manifest.knobs["enabled"] is False and manifest.hosts == ["labs.api.listenbrainz.org"] and not manifest.tenant_bound
    assert (row["category"], row["license_ref"], row["learning_eligible"]) == ("public charts and catalogs", "unverified", "false")
    assert manifest.input_version == ["mb_artist_gid", "algorithm", "similarity_week"]
    with (REPO / "dbt/seeds/lb_similarity_algorithms.csv").open() as stream:
        seeded = [r for r in csv.DictReader(stream) if r["active"] == "true"]
    assert [r["algorithm"] for r in seeded] == [ALGORITHM]


@pytest.mark.parametrize("scenario", ["normal", "partial", "not_found", "drift"])
async def test_lb_similar_artists_land_ranked_neighbours_without_labs_names(scenario):
    ctx, calls, refused = await collect("lb_similar_artists", scenario, SIMILARITY)
    rows = ctx.outputs.get("raw.lb_similar_artists", [])
    assert all(on_lb_allowlist(c.url) and c.url.params["algorithm"] == ALGORITHM for c in calls)
    for row in rows:
        assert not {"name", "type", "gender", "comment"} & set(row)
    neighbours = [(r["neighbour_mbid"][-1], r["rank"], r["score"]) for r in rows]
    if scenario == "drift":
        assert not rows and [r["reason"] for r in ctx.rejected] == ["envelope_mismatch:lb_similar_artists:0"] * 2
        return
    assert neighbours == [("1", 1, 800), ("2", 2, 700)]
    assert {r["algorithm"] for r in rows} == {ALGORITHM}
    if scenario == "partial":
        assert [r["reason"] for r in ctx.rejected] == ["drift:lb_similar_artists:neighbour"]
    elif scenario == "not_found":
        assert refused == ["vendor_4xx"]
    else:
        # The act below the listen threshold answers an empty list: nothing lands, nothing is rejected.
        assert not ctx.rejected and not refused


async def test_every_listenbrainz_request_passes_the_runtime_allowlist_and_a_listener_path_is_refused():
    requested = []
    for key, batch in (("lb_popularity", ACTS), ("lb_sitewide", [{}]), ("lb_fresh_releases", [{}]),
                       ("lb_similar_artists", SIMILARITY)):
        _, calls, refused = await collect(key, "normal", batch)
        assert calls and not refused, key
        requested += calls
    assert {c.url.host for c in requested} == {"api.listenbrainz.org", "labs.api.listenbrainz.org"}
    assert len(requested) == len(ACTS) + 3 + 1 + len(SIMILARITY)
    for request in requested:
        assert on_lb_allowlist(request.url), request.url
    # A listener endpoint names users: the same traced client refuses it before the wire.
    listeners = f"/1/stats/release-group/{ACTS[0]['mb_artist_gid']}/listeners"
    assert forbidden("api.listenbrainz.org", listeners)
    manifest = discover()["lb_sitewide"]
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None}
    ctx, sent = Ctx(manifest, run), []
    transport = httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200, json={}))
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=transport) as client:
        with pytest.raises(ServiceError) as caught:
            await client.get(f"https://api.listenbrainz.org{listeners}", params={"count": 100})
    assert caught.value.error_class == "forbidden_path" and sent == []
