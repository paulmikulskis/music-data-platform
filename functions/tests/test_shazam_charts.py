"""Tests for shazam charts."""

import csv
from datetime import date
from uuid import uuid4

import psycopg
import pytest
from free_source_fixture import (
    CHARTS,
    MEMBERSHIP,
    MISSES,
    REPO,
    collect,
    frozen_cycle,
    refused,
    registry,
    seed_charts,
)
from free_source_fixture import (
    no_ledger as no_ledger,  # noqa: PLC0414 - pytest fixture
)
from mdp_functions import chart_targets, shazam
from mdp_functions.fetch.hosts import DEFAULT_USER_AGENT
from mdp_functions.registry import discover


@pytest.mark.parametrize("scenario", ["normal", "partial", "not_found", "drift"])
async def test_sz_chart_accounts_honestly_in_every_scenario(scenario):
    ctx, calls, refused = await collect("sz_chart", scenario, CHARTS)
    rows = ctx.outputs.get("raw.shazam_chart_entries", [])
    assert ctx.observed_count == ctx.yielded_count + len(ctx.rejected)
    assert all(c.headers["User-Agent"] == DEFAULT_USER_AGENT for c in calls)
    assert {c.url.host for c in calls} == {"www.shazam.com"}
    # No song page and no internal API is ever requested.
    assert not [c for c in calls if c.url.path.startswith(("/song/", "/shazam/"))]
    for row in rows:
        shazam.ShazamChartEntry.model_validate(row)
    if scenario == "normal":
        assert len(rows) == 10 and not ctx.rejected and not refused
    elif scenario == "partial":
        assert len(rows) == 8
        assert sorted(r["reason"] for r in ctx.rejected) == ["csv_page_mismatch", "envelope_mismatch:sz_chart_page:songTitle"]
    elif scenario == "not_found":
        assert refused == ["stale_target"] and {r["chart"] for r in rows} == {
            "shazam:top-200:united-states", "shazam:top-50:united-states:boston"}
    else:
        assert not rows and MISSES == [("sz_chart_page", "songItem")] * 3
        assert {r["reason"] for r in ctx.rejected} == {"envelope_mismatch:sz_chart_page:songItem"}


async def test_sz_chart_lands_ids_rank_date_and_credit_as_printed():
    ctx, calls, _ = await collect("sz_chart", "normal", CHARTS)
    rows = ctx.outputs["raw.shazam_chart_entries"]
    # The CSV first (rank, date, credit), then the page (ids), per chart.
    assert [c.url.path for c in calls[:2]] == ["/services/charts/csv/top-200/united-states/", "/charts/top-200/united-states"]
    assert [c.url.path for c in calls[2:4]] == ["/services/charts/csv/top-50/united-states/boston", "/charts/top-50/united-states/boston"]
    top = [r for r in rows if r["chart"] == "shazam:top-200:united-states"]
    assert [r["position"] for r in top] == [1, 2, 3, 4, 5]
    assert {r["chart_date"] for r in rows} == {date(2026, 9, 24)}
    # The first row's artist link streams in through a template; every row carries both ids.
    assert top[0]["apple_primary_artist_id"] == "9609916580" and all(r["apple_primary_artist_id"] for r in rows)
    assert top[1]["artist_text"] == "Fixture Act 102 & Fixture Guest" and top[1]["apple_primary_artist_id"] == "1000000102"
    # Only the video-highlight song carries an ISRC.
    assert [r["isrc"] for r in top] == [None, None, None, None, "QZFXA2600105"]


def test_sz_chart_csv_parse_tolerates_its_unescaped_inner_quotes():
    text = '\ufeff\n"Thursday, 24 September 2026 [performance over the past 7 days]"\nRank,Artist,Title\n' \
           '1,"A","Fixture Theme, Reprise (From Fixture Film")"\n2,"B, C & D","Plain"\n'
    day, rows, unparsed = shazam.parse_csv(text)
    assert day == date(2026, 9, 24) and not unparsed
    assert shazam.folded(rows[1]["title_text"]) == shazam.folded('Fixture Theme, Reprise (From "Fixture Film")')
    assert rows[2]["artist_text"] == "B, C & D"
    with pytest.raises(shazam.EnvelopeError):
        shazam.parse_csv("<html>challenge</html>")


def test_sz_chart_declares_its_own_table_and_billboard_keeps_chart_entries():
    catalog = discover()
    manifest = catalog["sz_chart"]
    assert manifest.writes == ["raw.shazam_chart_entries"] and manifest.targets.kind == "chart"
    assert manifest.cadence == "daily" and not manifest.tenant_bound and manifest.hosts == ["www.shazam.com"]
    writers = {t: sorted(k for k, m in catalog.items() if t in m.writes)
               for t in ("raw.shazam_chart_entries", "raw.chart_entries")}
    assert writers == {"raw.shazam_chart_entries": ["sz_chart"], "raw.chart_entries": ["billboard_hot100"]}
    staging = (REPO / "dbt/models/staging/stg_billboard__chart_entries.sql").read_text()
    assert "shazam" not in staging and "source('raw', 'chart_entries')" in staging
    row = registry()["sz_chart"]
    assert (row["category"], row["license_ref"], row["learning_eligible"], row["resale_permitted"]) == (
        "public charts and catalogs", "unverified", "false", "false")


def test_the_chart_seed_is_capped_and_freezes_both_urls(tmp_path):
    with (REPO / "inputs/chart_seed.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    cap = chart_targets.chart_cap()
    assert cap == 60 and len(rows) <= cap
    specs = [chart_targets.chart_spec(row) for row in rows]
    assert len({canonical for _, canonical, _ in specs}) == len(rows)
    kinds = {params["chart_type"] for _, _, params in specs}
    assert kinds == {"top-200", "top-50", "discovery"}
    markets = {params["priority_market"] for _, _, params in specs}
    assert markets == {"global", "US"}
    by_id = {chart_id: params for chart_id, _, params in specs}
    assert by_id["top-200:world"]["csv_url"] == "https://www.shazam.com/services/charts/csv/top-200/world/"
    city = by_id["top-50:united-states:boston"]
    assert city["page_url"] == "https://www.shazam.com/charts/top-50/united-states/boston"
    assert city["csv_url"] == "https://www.shazam.com/services/charts/csv/top-50/united-states/boston"
    # A seed past its cap is refused before any write.
    longer = tmp_path / "seed.csv"
    longer.write_text((REPO / "inputs/chart_seed.csv").read_text())
    with pytest.raises(ValueError, match="caps sz_chart at 3"):
        chart_targets.seed_chart_targets(None, longer, cap=3)
    with pytest.raises(ValueError):
        chart_targets.chart_spec({"chart_type": "top-50", "country": "united-states", "city": ""})


async def test_sz_chart_lands_through_the_runtime_on_the_fixture_seed(rt, databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        assert chart_targets.seed_chart_targets(conn, REPO / "inputs/chart_fixture_seed.csv") == 3
        spec = conn.execute(
            "SELECT resource_kind,canonical_key,params_json FROM control.target_spec s JOIN control.target t ON t.id=s.target_id "
            "WHERE t.platform_account_id='top-50:united-states:boston'").fetchone()
    assert spec[0] == "chart" and spec[1] == "sz:chart:top-50:united-states:boston" and spec[2]["city"] == "boston"
    dbt_id = "local:" + uuid4().hex
    await rt.cycles.bind_cycle("daily", "global", dbt_id, "scheduled", "local:daily", runner="core")
    export = rt.admit("targets_export", dbt_run_id=dbt_id, target_kinds=["chart"])
    await rt.execute(export["id"])
    run = rt.admit("sz_chart", dbt_run_id=dbt_id)
    await rt.execute(run["id"])
    result = rt.receipts(run["id"])["run"]
    assert result["status"] == "succeeded" and result["rows_written"] == 10, result
    with psycopg.connect(rt.settings.service_read_url) as conn:
        rows = conn.execute(
            "SELECT chart,chart_date,position,apple_song_id,apple_primary_artist_id,_source_key FROM raw.shazam_chart_entries "
            "WHERE _run_id=%s ORDER BY chart,position", (run["id"],)).fetchall()
    assert len(rows) == 10 and {r[5] for r in rows} == {"sz_chart"} and all(r[3] and r[4] for r in rows)
    calls = rt.db.all("SELECT endpoint FROM control.call_ledger WHERE run_id=%s", (run["id"],))
    assert len(calls) == 6 and not [c for c in calls if c["endpoint"].startswith(("/song/", "/shazam/"))]


async def test_a_global_source_on_a_rebuilt_tenant_cycle_stays_scope_mismatch(rt, databases):
    seed_charts(databases)
    tenant = uuid4()
    with psycopg.connect(databases["admin_control"]) as conn:
        conn.execute("INSERT INTO control.tenant(id,name,slug) VALUES (%s,'Scope','scope-check')", (tenant,))
        conn.execute("INSERT INTO control.dbt_job(job_id,runner,cadence,scope) VALUES ('tenant-scope','core','daily',%s)",
                     ("tenant:" + str(tenant),))
    _, rebuild, _ = await frozen_cycle(rt, ["account"], scope="tenant:" + str(tenant), job="tenant-scope")
    assert refused(rt, "sz_chart", rebuild) == MEMBERSHIP
