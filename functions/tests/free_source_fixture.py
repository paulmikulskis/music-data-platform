"""Shared fixtures for public source collectors and their SQL gates."""

import csv
import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from mdp_functions import chart_targets
from mdp_functions.errors import ServiceError
from mdp_functions.fetch.forbidden import forbidden, listed
from mdp_functions.http import FixtureTransport, TracedClient
from mdp_functions.layers import Ctx, Target
from mdp_functions.registry import discover
from test_fetch_runtime import FakeDB, no_page
from test_identity_functions import load_mirror

REPO = Path(__file__).parents[2]


ROOT = REPO / "functions/src/mdp_functions/sources"


TARGET_ERRORS = {"vendor_4xx", "vendor_retryable", "scrape_blocked", "envelope_mismatch", "stale_target"}


MISSES: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def no_ledger(monkeypatch):
    monkeypatch.setattr("mdp_functions.http.draw", lambda *args: None)
    MISSES.clear()
    monkeypatch.setattr(
        "mdp_functions.http.envelope_miss",
        lambda db, run_id, target_id, surface, path, decide=True: MISSES.append((surface, path)),
    )


def registry():
    with (REPO / "dbt/seeds/rights_registry.csv").open() as stream:
        return {row["source_key"]: row for row in csv.DictReader(stream)}


def chart_targets_from(path):
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    targets = []
    for row in rows:
        chart_id, canonical, params = chart_targets.chart_spec(row)
        targets.append(Target(id=str(uuid4()), platform="shazam", platform_account_id=chart_id,
                              handle=chart_id.replace(":", "/"), canonical_key=canonical, params_json=params))
    return targets


async def collect(key, scenario="normal", batch=None, transport=None):
    """One target (bronze) or one input (gold) at a time through the traced client, as the runtime runs
    a batch."""
    manifest = discover()[key]
    run = {"id": str(uuid4()), "cycle_id": str(uuid4()), "streamline_id": str(uuid4()), "tenant_id": None}
    ctx = Ctx(manifest, run)
    calls, refused = [], []

    async def seen(request):
        calls.append(request)

    fixture = transport or FixtureTransport([ROOT / key / "fixtures" / f"{scenario}.jsonl"])
    async with TracedClient(ctx, FakeDB(), run, no_page, transport=fixture, event_hooks={"request": [seen]}) as client:
        ctx.http = client
        for item in batch:
            ctx.target, ctx.target_id = (item, str(item.get("id") or "")) if manifest.targets else (None, None)
            try:
                if manifest.layer == "gold":
                    ctx.input_row = {"input_ref": "ref", "input_version": "ver", **item}
                    await manifest.function(ctx, [item])
                else:
                    # A function with no target set takes the context alone, as the runtime calls it.
                    body = manifest.function(ctx, [item]) if manifest.targets else manifest.function(ctx)
                    async for row in body:
                        ctx.yield_row(row)
            except ServiceError as exc:
                if exc.error_class not in TARGET_ERRORS:
                    raise
                refused.append(exc.error_class)
    return ctx, calls, refused


CHARTS = chart_targets_from(REPO / "inputs/chart_fixture_seed.csv")


async def frozen_cycle(rt, kinds, cadence="daily", scope="global", job=None, close=True):
    """A scheduled cycle whose export froze `kinds` (closed unless `close` is false: a run that died after
    its export), then the deploy's rebuild bound to it (reason other: the newest scheduled cycle). Returns
    the scheduled run's id, the rebuild's id and the cycle id."""
    job = job or "local:" + cadence
    opened = "local:" + uuid4().hex
    await rt.cycles.bind_cycle(cadence, scope, opened, "scheduled", job, runner="core")
    export = rt.admit("targets_export", dbt_run_id=opened, target_kinds=kinds)
    await rt.execute(export["id"])
    if close:
        await rt.execute(rt.admit("cycle_close", dbt_run_id=opened)["id"])
    rebuild = "core:" + uuid4().hex
    binding = await rt.cycles.bind_cycle(cadence, scope, rebuild, "other", job, runner="core")
    assert rt.db.one("SELECT status FROM control.cycle WHERE id=%s", (binding["cycle_id"],))["status"] == (
        "closed" if close else "open")
    return opened, rebuild, binding["cycle_id"]


def seed_charts(databases):
    with psycopg.connect(databases["admin_control"]) as conn:
        assert chart_targets.seed_chart_targets(conn, REPO / "inputs/chart_fixture_seed.csv") == 3


def refused(rt, source, dbt_run_id, **kwargs):
    with pytest.raises(ServiceError) as caught:
        rt.admit(source, dbt_run_id=dbt_run_id, **kwargs)
    return caught.value.error_class, caught.value.message


MEMBERSHIP = ("scope_mismatch", "Export target membership before invoking this source")


FREE_SOURCE_KEYS = ("sz_chart", "kexp_plays", "lb_fresh_releases", "lb_sitewide", "lb_popularity", "lb_similar_artists",
            "wiki_sitelinks", "wiki_pageviews")


QIDS = [{"qid": "Q9000001", "sitelinks_week": date(2026, 9, 21)}, {"qid": "Q9000002", "sitelinks_week": date(2026, 9, 21)}]


ARTICLES = [
    {"qid": "Q9000001", "project": "en.wikipedia", "title": "Fixture Act (band)", "pageview_day": date(2026, 9, 24)},
    {"qid": "Q9000001", "project": "de.wikipedia", "title": "Fixture Act", "pageview_day": date(2026, 9, 24)},
]


async def global_input(rt, databases, slug, relation, ddl, rows):
    """A public enrichment input and the daily cycle that reads it."""
    schema = "intermediate"
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(f"DROP TABLE IF EXISTS {schema}.{relation}")
        conn.execute(f"CREATE TABLE {schema}.{relation} ({ddl}, _source_keys text, input_ref text, input_version text)")
        for row in rows:
            conn.execute(f"INSERT INTO {schema}.{relation} VALUES ({','.join(['%s'] * len(row))})", row)
        conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO service_read")
        conn.execute(f"GRANT SELECT ON {schema}.{relation} TO service_read")
    dbt_id = "global:" + uuid4().hex
    await rt.cycles.bind_cycle("daily", "global", dbt_id, "scheduled", "local:daily", runner="core")
    return None, dbt_id, f"{schema}.{relation}"


@pytest.fixture
def label_mirror():

    from conftest import url_database
    from psycopg import sql

    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    name = "mbt_free_sources_" + uuid4().hex[:10]
    url = load_mirror(admin, name)
    yield {"url": url, "admin": url_database(admin, name)}
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


LB_KEYS = ("lb_popularity", "lb_sitewide", "lb_fresh_releases")


ACTS = [{"mb_artist_gid": "00000000-0000-4000-a000-000000000001", "popularity_day": date(2026, 9, 24)},
        {"mb_artist_gid": "00000000-0000-4000-a000-000000000002", "popularity_day": date(2026, 9, 24)}]


def on_lb_allowlist(url) -> bool:
    """A ListenBrainz URL the runtime-wide allowlist of design passes (fetch/forbidden.py)."""
    return listed(url.host) and forbidden(url.host, url.path) is None


ALGORITHM = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"


SIMILARITY = [{**a, "algorithm": ALGORITHM, "similarity_week": date(2026, 9, 21)} for a in ACTS]


WINDOW = {"from": "2026-09-22T00:00:00+00:00", "to": "2026-09-23T00:00:00+00:00"}


def kexp_page(plays, next_url=None):
    return {"next": next_url, "results": [
        {"id": n, "airdate": airdate, "play_type": "trackplay", "artist_ids": [], "label_ids": []} for n, airdate in plays]}


def review_gate():
    """ops/ci/review_gate.py, the check dbt-ci runs before the build."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("review_gate", REPO / "ops/ci/review_gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dbt_manifest(tmp_path_factory):
    """dbt parse's manifest.json path (the CI target), as dbt-ci leaves it for ops/ci/review_gate.py."""
    import subprocess

    target = tmp_path_factory.mktemp("dbt-target")
    subprocess.run(
        ["uv", "run", "--project", str(REPO / "dbt"), "dbt", "parse", "--project-dir", str(REPO / "dbt"),
         "--profiles-dir", str(REPO / "dbt/profiles"), "--target", "ci", "--quiet", "--target-path", str(target)],
        capture_output=True, text=True, check=True)
    return target / "manifest.json"


def dbt_models(*select):
    import subprocess

    out = subprocess.run(
        ["uv", "run", "--project", str(REPO / "dbt"), "dbt", "ls", "--project-dir", str(REPO / "dbt"),
         "--profiles-dir", str(REPO / "dbt/profiles"), "--target", "ci", "--quiet", "--resource-type", "model",
         *select, "--output", "json", "--output-keys", "name config"],
        capture_output=True, text=True, check=True).stdout
    return {m["name"]: m for m in (json.loads(line) for line in out.splitlines() if line.startswith("{"))}


def write_rules(path, rules):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rules[0]))
        writer.writeheader()
        writer.writerows(rules)
    return str(path)
