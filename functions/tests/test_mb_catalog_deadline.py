"""Catalog lookups stop within their budget and land under existing staging views."""

import importlib
import time
from uuid import uuid4

import psycopg
import pytest
from conftest import bound
from mdp_functions import dumps
from mdp_functions import musicbrainz as mb
from mdp_functions.exporter import raw_schemas
from mdp_functions.registry import REGISTRY
from test_identity_functions import ACT, CATALOG_ACT, CATALOG_ROWS, GONE, catalog_inputs
from test_identity_functions import mirror as mirror  # noqa: PLC0414 -- shared fixture


@pytest.mark.docker
@pytest.mark.parametrize("retained_date", [False, True])
async def test_catalog_lands_under_views_with_new_and_retained_column_types(
    rt, databases, mirror, monkeypatch, retained_date
):
    with psycopg.connect(mirror["admin"], autocommit=True) as conn:
        conn.execute(CATALOG_ROWS)
    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    mb.reset_lookup_connection()
    catalog_inputs(databases, [ACT, GONE])
    schemas = raw_schemas(REGISTRY, rt.settings.schema_root)
    for table in REGISTRY["mb_artist_catalog"].writes:
        rt.warehouse.ensure(table, schemas[table])
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(
            "CREATE VIEW raw.catalog_lookup_view AS SELECT catalog_week FROM raw.mb_artist_catalog"
        )
        conn.execute(
            "CREATE VIEW raw.catalog_groups_view AS SELECT catalog_week FROM raw.mb_artist_release_groups"
        )
    original = dumps.component_columns
    if retained_date:
        # Published dumps from an older worker carry date. Their bytes and schema stay immutable.
        def old_columns(*args, **kwargs):
            return {**original(*args, **kwargs), "catalog_week": "date"}

        monkeypatch.setattr(dumps, "component_columns", old_columns)
    try:
        _, run = await bound(rt, "mb_artist_catalog")
        await rt.execute(run["id"])
        assert rt.receipts(run["id"])["run"]["status"] == "succeeded"
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            assert conn.execute(
                "SELECT status, catalog_week FROM raw.mb_artist_catalog ORDER BY status"
            ).fetchall() == [("found", "2026-09-21"), ("not_found", "2026-09-21")]
            assert conn.execute(
                "SELECT DISTINCT pg_typeof(catalog_week)::text FROM raw.mb_artist_catalog"
            ).fetchall() == [("text",)]
            assert (
                conn.execute("SELECT count(*) FROM raw.catalog_groups_view").fetchone()[
                    0
                ]
                == 1
            )
    finally:
        mb.reset_lookup_connection()
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            conn.execute("DROP VIEW raw.catalog_lookup_view, raw.catalog_groups_view")


@pytest.mark.docker
@pytest.mark.parametrize(
    "budget,lookup_limit,completed_first",
    [(1.5, 10, True), (900, 0.2, True), (900, 0.2, False)],
    ids=["work-budget", "early-lookup-limit", "first-lookup-limit"],
)
async def test_slow_catalog_stops_within_budget_and_next_day_completes_week(
    rt, databases, mirror, monkeypatch, budget, lookup_limit, completed_first
):
    with psycopg.connect(mirror["admin"], autocommit=True) as conn:
        conn.execute(CATALOG_ROWS)
    monkeypatch.setenv("MDP_MB_DB_URL", mirror["url"])
    mb.reset_lookup_connection()
    catalog_inputs(databases, [ACT, CATALOG_ACT, GONE])
    manifest = REGISTRY["mb_artist_catalog"]
    monkeypatch.setattr(manifest, "time_budget_s", budget)
    schemas = raw_schemas(REGISTRY, rt.settings.schema_root)
    for table in manifest.writes:
        rt.warehouse.ensure(table, schemas[table])
    query = mb.ARTIST_CATALOG
    stalled = CATALOG_ACT if completed_first else ACT
    # Cancel a real query, either at admission or after one answer, with no false completion.
    monkeypatch.setattr(
        mb,
        "ARTIST_CATALOG",
        f"""
        WITH pause AS MATERIALIZED (
            SELECT pg_sleep(CASE WHEN %(gid)s = '{stalled}' THEN 5 ELSE 0 END)
        ) SELECT result.* FROM pause CROSS JOIN LATERAL ({query}) result
    """,
    )
    source = importlib.import_module("mdp_functions.sources.mb_artist_catalog.function")
    monkeypatch.setattr(source, "LOOKUP_TIMEOUT_S", lookup_limit)
    original_lookup = source.lookup
    lookup_seconds = []

    def timed_lookup(*args):
        started = time.monotonic()
        try:
            return original_lookup(*args)
        finally:
            lookup_seconds.append(time.monotonic() - started)

    monkeypatch.setattr(source, "lookup", timed_lookup)
    _, run = await bound(rt, "mb_artist_catalog")
    started = time.monotonic()
    await rt.execute(run["id"])
    elapsed = time.monotonic() - started
    # Admission and input snapshot precede the declared work budget; landing follows it.
    assert sum(lookup_seconds) < 2, lookup_seconds
    assert len(lookup_seconds) == (2 if completed_first else 1)
    assert elapsed < 20, f"Run misses the deadline allowance: {elapsed:.3f}s"
    assert rt.db.one(
        "SELECT status,error_class,rows_rejected FROM control.run WHERE id=%s", (run["id"],)
    ) == {"status": "partial", "error_class": "time_budget", "rows_rejected": 0}
    assert rt.db.one(
        "SELECT count(*) AS n FROM control.dead_letter WHERE run_id=%s", (run["id"],)
    )["n"] == 0
    if budget == 900:
        assert elapsed < budget / 10
        assert "mirror lookup" in rt.receipts(run["id"])["run"]["error_message"]
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert conn.execute(
            "SELECT mb_artist_gid FROM raw.mb_artist_catalog ORDER BY 1"
        ).fetchall() == ([(ACT,)] if completed_first else [])
    rt.cycles.close(run["cycle_id"])
    later = await rt.cycles.bind_cycle(
        "daily",
        "global",
        "local:" + uuid4().hex,
        "scheduled",
        "local:daily",
        runner="core",
    )
    rt.cycles.close(later["cycle_id"])
    monkeypatch.setattr(mb, "ARTIST_CATALOG", query)
    monkeypatch.setattr(manifest, "time_budget_s", 30)
    monkeypatch.setattr(source, "LOOKUP_TIMEOUT_S", 10)
    following = rt.admit(
        "mb_artist_catalog", cadence="daily", dbt_run_id=later["dbt_run_id"]
    )
    await rt.execute(following["id"])
    assert rt.receipts(following["id"])["run"]["status"] == "succeeded"
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        assert conn.execute(
            "SELECT mb_artist_gid,count(*) FROM raw.mb_artist_catalog GROUP BY 1 ORDER BY 1"
        ).fetchall() == [(GONE, 1), (ACT, 1), (CATALOG_ACT, 1)]
    mb.reset_lookup_connection()
