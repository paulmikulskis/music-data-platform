"""Tests for wikimedia."""

import json
from datetime import date
from uuid import uuid4

import httpx
import psycopg
import pytest
from free_source_fixture import (
    ARTICLES,
    QIDS,
    REPO,
    collect,
    global_input,
    registry,
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)
from mdp_functions import wikimedia
from mdp_functions.http import TracedClient
from mdp_functions.layers import Ctx
from mdp_functions.registry import discover
from test_fetch_runtime import FakeDB, no_page


def test_wikimedia_keys_are_global_gold_on_their_own_hosts_and_cc0():
    catalog, rights = discover(), registry()
    for key, host, relation in (("wiki_sitelinks", "www.wikidata.org", "intermediate.int_wiki__artist_qids"),
                                ("wiki_pageviews", "wikimedia.org", "intermediate.int_wiki__articles")):
        manifest = catalog[key]
        assert manifest.layer == "gold" and manifest.external and not manifest.tenant_bound, key
        assert manifest.hosts == [host] and manifest.reads == [relation] and manifest.knobs["allow_partial"] is True
        # The provider defaults to the source key, so a row's eligibility reads the key's own registry row.
        assert manifest.provider is None
        row = rights[key]
        assert (row["category"], row["license_ref"], row["learning_eligible"], row["resale_permitted"]) == (
            "public charts and catalogs", "unverified", "false", "false")
    assert catalog["wiki_sitelinks"].input_version == ["qid", "sitelinks_week"]
    assert catalog["wiki_pageviews"].input_version == ["title", "pageview_day"]


def test_wikipedia_projects_come_from_their_site_ids():
    assert wikimedia.project("enwiki") == "en.wikipedia" and wikimedia.project("zh_min_nanwiki") == "zh-min-nan.wikipedia"
    assert wikimedia.project("be_x_oldwiki") == "be-tarask.wikipedia"
    assert {wikimedia.project(s) for s in ("commonswiki", "wikidatawiki", "enwikiquote", "enwiktionary", "wiki")} == {None}


@pytest.mark.parametrize("scenario", ["normal", "partial", "not_found", "drift"])
async def test_wiki_sitelinks_land_wikipedia_articles_only(scenario):
    ctx, calls, refused = await collect("wiki_sitelinks", scenario, QIDS)
    rows = ctx.outputs.get("raw.wiki_sitelinks", [])
    lookups = [(r["qid"], r["articles"]) for r in ctx.outputs.get("raw.wiki_lookups", [])]
    assert [c.url.params["ids"] for c in calls] == ["Q9000001", "Q9000002"] and not refused
    assert all(c.url.params["props"] == "sitelinks" for c in calls)
    for row in rows:
        wikimedia.WikiSitelink.model_validate(row)
    if scenario in ("normal", "partial"):
        assert [(r["qid"], r["site"], r["project"], r["title"]) for r in rows] == [
            ("Q9000001", "dewiki", "de.wikipedia", "Fixture Act"),
            ("Q9000001", "enwiki", "en.wikipedia", "Fixture Act (band)")]
        # An item with no Wikipedia article lands its lookup with 0 articles; a missing item is rejected and
        # lands no lookup, so SQL never reads it as having no article.
        assert [r["reason"] for r in ctx.rejected] == ([] if scenario == "normal" else ["not_found"])
        assert lookups == ([("Q9000001", 2), ("Q9000002", 0)] if scenario == "normal" else [("Q9000001", 2)])
    elif scenario == "not_found":
        assert not rows and [r["reason"] for r in ctx.rejected] == ["not_found"] and lookups == [("Q9000002", 0)]
    else:
        assert not rows and [r["reason"] for r in ctx.rejected] == ["envelope_mismatch:wiki_sitelinks:entities"] * 2
        assert not lookups


@pytest.mark.parametrize("scenario", ["normal", "partial", "not_found", "drift"])
async def test_wiki_pageviews_read_human_views_for_the_three_days_before_the_cycle(scenario):
    ctx, calls, refused = await collect("wiki_pageviews", scenario, ARTICLES)
    rows = ctx.outputs.get("raw.wiki_pageviews", [])
    assert not refused
    assert calls[0].url.raw_path.decode().split("/")[-5:] == ["user", "Fixture_Act_%28band%29", "daily", "2026092100", "2026092300"]
    assert all("/all-access/user/" in c.url.path for c in calls)
    for row in rows:
        wikimedia.WikiPageview.model_validate(row)
    english = [(r["date"], r["views"]) for r in rows if r["project"] == "en.wikipedia"]
    if scenario == "drift":
        assert not rows and [r["reason"] for r in ctx.rejected] == ["envelope_mismatch:wiki_pageviews:items"] * 2
        return
    assert english == [(date(2026, 9, 21), 100), (date(2026, 9, 22), 101), (date(2026, 9, 23), 102)]
    german = [(r["date"], r["views"]) for r in rows if r["project"] == "de.wikipedia"]
    if scenario == "normal":
        # The day Wikimedia left out had no views: every fetched day lands.
        assert german == [(date(2026, 9, 21), 10), (date(2026, 9, 22), 0), (date(2026, 9, 23), 12)] and not ctx.rejected
    elif scenario == "partial":
        # A crawler's item rejects its whole window, so no part of it lands beside the retry.
        assert not german and [r["reason"] for r in ctx.rejected] == ["drift:wiki_pageviews:item"]
    else:
        # Wikimedia answers 404 for an article with no views in the window: each of its days lands as 0.
        assert german == [(date(2026, 9, d), 0) for d in (21, 22, 23)] and not ctx.rejected


def test_wiki_pageviews_fit_their_time_budget_and_read_every_act_s_largest_editions_first():
    from mdp_functions.streamline_defaults import HOST_RATES

    manifest = discover()["wiki_pageviews"]
    rate, budget, timeout = HOST_RATES["wikimedia.org"], manifest.time_budget_s, manifest.knobs["timeout_s"]
    # 120 acts at PROJECTS_PER_ACT articles each are 1,200 requests: 600 s at 2 requests a second.
    assert (120 * wikimedia.PROJECTS_PER_ACT, rate, budget) == (1200, 2, 600)
    assert 120 * wikimedia.PROJECTS_PER_ACT / rate == budget
    # Past the budget the run ends partial and the invoke passes: its deadline (the timeout less 30 s)
    # covers the budget and one more part of batch_size inputs, so the timeout never cancels the invoke
    # and previously landed observations remain readable.
    assert budget + manifest.knobs["batch_size"] / rate < timeout - 30
    assert manifest.knobs["allow_partial"] is True and manifest.input_order == ["project_rank", "qid", "project"]
    # A star with 156 Wikipedia articles is read in 10 of them; the SQL applies the same cap.
    assert min(156, wikimedia.PROJECTS_PER_ACT) == 10
    model = (REPO / "dbt/models/intermediate/int_wiki__articles.sql").read_text()
    assert f"{{% set projects_per_act = {wikimedia.PROJECTS_PER_ACT} %}}" in model


def test_wiki_sitelinks_fit_their_time_budget():
    from mdp_functions.streamline_defaults import HOST_RATES

    manifest = discover()["wiki_sitelinks"]
    rate, budget, timeout = HOST_RATES["www.wikidata.org"], manifest.time_budget_s, manifest.knobs["timeout_s"]
    # 600 s at 2 lookups a second is 1,200 QIDs a run; past it the run ends partial and the invoke passes.
    assert (rate, budget, budget * rate) == (2, 600, 1200)
    assert budget + manifest.knobs["batch_size"] / rate < timeout - 30 and manifest.knobs["allow_partial"] is True


async def test_pageviews_zero_fill_stops_at_the_newest_published_day():
    """Wikimedia leaves out a day it has not loaded yet: the run fills 0 only through the newest day any
    response carried, and before any has, a 404 lands nothing."""

    manifest = discover()["wiki_pageviews"]
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None}
    ctx = Ctx(manifest, run)
    item = {"project": "en.wikipedia", "article": "A", "granularity": "daily", "access": "all-access", "agent": "user"}
    answers = iter([httpx.Response(404, json={"title": "Not found."}),
                    httpx.Response(200, json={"items": [{**item, "timestamp": "2026092100", "views": 9},
                                                        {**item, "timestamp": "2026092200", "views": 4}]}),
                    httpx.Response(404, json={"title": "Not found."})])
    transport = httpx.MockTransport(lambda request: next(answers))
    rows = [{"qid": q, "project": "en.wikipedia", "title": q, "pageview_day": date(2026, 9, 24)} for q in ("Q1", "Q2", "Q3")]
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=transport) as client:
        ctx.http = client
        for row in rows:
            ctx.input_row = {"input_ref": row["qid"], "input_version": "v", **row}
            await manifest.function(ctx, [row])
    landed = [(r["qid"], r["date"], r["views"]) for r in ctx.outputs.get("raw.wiki_pageviews", [])]
    # Q1's 404 came before anything was known published; 09-23 is not published yet for Q2 or Q3.
    assert landed == [("Q2", date(2026, 9, 21), 9), ("Q2", date(2026, 9, 22), 4),
                      ("Q3", date(2026, 9, 21), 0), ("Q3", date(2026, 9, 22), 0)], landed


async def test_wiki_sitelinks_land_global_rows_through_the_runtime(rt, databases):
    _tenant, dbt_id, relation = await global_input(
        rt, databases, "wiki_fixture", "int_wiki__artist_qids",
        "qid text, sitelinks_week date",
        [("Q9000001", date(2026, 9, 21), '["mb_spine"]', "ref-q1", "ver-q1-w39"),
         ("Q9000002", date(2026, 9, 21), '["mb_spine"]', "ref-q2", "ver-q2-w39")],
    )
    from mdp_functions.rights import sync_rights

    # The deployed bootstrap mirrors the registry into control.rights_source as rights_sync; the session's
    # other rows come back afterwards.
    with psycopg.connect(databases["admin_control"]) as conn:
        before = conn.execute("SELECT * FROM control.rights_source").fetchall()
        conn.execute("SET ROLE rights_sync")
        assert sync_rights(conn, REPO / "dbt/seeds/rights_registry.csv") == len(registry())
    try:
        run = rt.admit("wiki_sitelinks", dbt_run_id=dbt_id, input_relation=relation)
        await rt.execute(run["id"])
    finally:
        with psycopg.connect(databases["admin_control"]) as conn:
            conn.execute("DELETE FROM control.rights_source")
            for row in before:
                conn.execute(f"INSERT INTO control.rights_source VALUES ({','.join(['%s'] * len(row))})", row)
    result = rt.receipts(run["id"])["run"]
    assert result["status"] == "succeeded" and result["rows_written"] == 4, result
    with psycopg.connect(rt.settings.service_read_url) as conn:
        rows = conn.execute(
            "SELECT qid,site,project,sitelinks_week,_source_key,_source_keys::text,learning_eligible "
            "FROM raw.wiki_sitelinks WHERE _run_id=%s ORDER BY site", (run["id"],)).fetchall()
        lookups = conn.execute("SELECT qid,articles,learning_eligible FROM raw.wiki_lookups WHERE _run_id=%s ORDER BY qid",
                               (run["id"],)).fetchall()
    assert [r[:3] for r in rows] == [("Q9000001", "dewiki", "de.wikipedia"), ("Q9000001", "enwiki", "en.wikipedia")]
    assert lookups == [("Q9000001", 2, False), ("Q9000002", 0, False)]
    # The input_version component rides beside the row; the lineage names the spine.
    assert {str(r[3])[:10] for r in rows} == {"2026-09-21"}
    assert {r[4] for r in rows} == {"wiki_sitelinks"} and {json.loads(r[5])[0] for r in rows} == {"mb_spine"}
    # The public snapshot grants no inherited learning rights; landed rows preserve that flag.
    assert {r[6] for r in rows} == {False}
    # A second run in the same week finds both inputs complete and makes no request.
    again = rt.admit("wiki_sitelinks", dbt_run_id=dbt_id, input_relation=relation, manual=True)
    await rt.execute(again["id"])
    assert rt.db.one("SELECT count(*) AS n FROM control.call_ledger WHERE run_id=%s", (again["id"],))["n"] == 0
