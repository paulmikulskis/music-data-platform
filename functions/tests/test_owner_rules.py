"""The shared owner rules (mdp_functions.owners): the older-row evidence agrees with what each collector
observes on its fixture, staging's generated macro is the owners.py rendering, and re-landing an old
dump leaves the names the repair leaves."""

import json
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mdp_functions import owners
from mdp_functions.http import FixtureTransport
from mdp_functions.playlist import PlaylistCandidate, PlaylistSnapshot
from mdp_functions.schemas import LINEAGE, declared
from mdp_functions.warehouse.duckdb import DuckDBWarehouse
from mdp_functions.warehouse.postgres import PostgresWarehouse
from playlist_collector_fixture import run
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from test_playlists import ROOT, setup

REPO = Path(__file__).parents[2]
COLUMNS = ("platform", "playlist_id", "fetch_surface", "owner_id", "owner_name", "attributes", "owner_class",
           "platform_version")


async def landed(key):
    if key in ("am_playlist", "sp_playlist"):
        ctx, target, _ = setup(key)
        fixture = ROOT / key / "fixtures/normal.jsonl"
        async with httpx.AsyncClient(transport=FixtureTransport([fixture])) as client:
            ctx.http = client
            await ctx.manifest.function(ctx, [target])
    else:
        ctx, _ = await run(key)
    return ctx.outputs["raw.playlist_snapshots"]


@pytest.mark.parametrize(
    "key",
    ["am_playlist", "sp_playlist", "sc_playlist", "bc_discover",
     "bc_daily_list", "bc_radio", "bc_fan_playlist"],
)
async def test_older_row_evidence_agrees_with_the_parser(key):
    """With `owner_class_observed` absent, the payload evidence yields the class the parser observed,
    or `unknown` for an owner the evidence cannot place, which is never a platform owner either."""
    rows = await landed(key)
    assert rows
    conn = duckdb.connect()
    conn.execute("create table raw_rows (" + ", ".join(
        f"{c} {'json' if c == 'attributes' else 'varchar'}" for c in COLUMNS) + ", owner_class_observed varchar)")
    for row in rows:
        conn.execute(
            f"insert into raw_rows values ({', '.join('?' for _ in COLUMNS)}, null)",
            [json.dumps(row[c]) if c == "attributes" else row[c] for c in COLUMNS],
        )
        derived = conn.execute(
            f"select {owners.observed_class('duckdb')} from raw_rows where playlist_id = ? and fetch_surface = ?",
            [row["playlist_id"], row["fetch_surface"]],
        ).fetchone()[0]
        observed = row["owner_class_observed"]
        assert (derived in owners.PUBLIC_OWNERS) == (observed in owners.PUBLIC_OWNERS), (row["fetch_surface"], derived, observed)
        assert derived == observed or derived == "unknown", (row["fetch_surface"], derived, observed)
        conn.execute("delete from raw_rows")


def test_generated_macro_is_the_owners_rendering():
    assert (REPO / "dbt/macros/mdp_owner_rules.sql").read_text() == owners.dbt_macros()


def test_no_served_mart_reads_the_owner_pseudonym():
    """Staging keeps a non-platform owner's pseudonym in `owner_key` for joins; a served mart reads only
    `owner_id`, which staging nulls for that owner (playlist_dbt_fixture.py checks the values)."""
    marts = sorted((REPO / "dbt/models/marts").rglob("*.sql"))
    assert marts
    assert [m.name for m in marts if "owner_key" in m.read_text()] == []


def test_platform_names_and_constants_are_shared():
    from mdp_functions.bandcamp import BANDCAMP_DAILY
    from mdp_functions.soundcloud import SOUNDCLOUD_USER

    assert BANDCAMP_DAILY == owners.PLATFORM_OWNER_NAMES["bc_daily_list"]
    assert str(SOUNDCLOUD_USER) == owners.SOUNDCLOUD_ACCOUNT_ID


CHART_DATE = "2026-09-23T07:00:13Z"


@pytest.mark.parametrize(
    ("case", "playlist_id", "label", "owner_id", "name", "version", "observed", "served"),
    [
        # Landed by the parser that read the bare "Apple Music" link as a user's: name dropped, no owner
        # id, a JSON-LD date. The label may still refine it to a chart.
        ("top 100 labelled chart", "pl.043a2c9876114d95a4659988497567be", "chart", None, None, CHART_DATE, "editorial", "chart"),
        ("top 100 global unlabelled", "pl.d25f5d1181894928af76c85c967f8f31", "user", None, None, CHART_DATE, "editorial", "editorial"),
        ("new music daily", "pl.2b0e6e332fdf4b7a91164da3162127b5", "user", None, None, "2026-09-23T16:00:45Z", "editorial", "editorial"),
        # Landed earlier with its "Apple Music" name, class unknown.
        ("named unknown", "pl.11ac7cc7d09741c5822e8c66e5c7edbb", "unknown", None, "Apple Music", CHART_DATE, "editorial", "editorial"),
        # A listener's library list is a user's whatever it is named or labelled.
        ("library list", "pl.u-qxylEY6s3ar1kVv", "editorial", None, "Apple Music Fan", CHART_DATE, "user", "user"),
        # A catalog list with a listener's profile id, or without a date, is not the platform's.
        ("catalog with a profile", "pl.0123456789abcdef0123456789abcdef", "editorial", "sp.1", None, CHART_DATE, "user", "user"),
        # An Apple curator's store id with its name dropped (an earlier parser's `curator`) is the platform's.
        ("catalog with a curator id", "pl.28926c578a80475c904026ea97646ad5", "curator", "976439539", None, None, "editorial", "editorial"),
        # With neither, the evidence is unknown and the label fills it.
        ("catalog without a date", "pl.fedcba9876543210fedcba9876543210", "curator", None, None, None, "unknown", "curator"),
    ],
)
def test_older_apple_rows_take_the_payload_evidence(case, playlist_id, label, owner_id, name, version, observed, served):
    conn = duckdb.connect()
    conn.execute("create table raw_rows (platform varchar, playlist_id varchar, fetch_surface varchar, owner_id varchar, "
                 "owner_name varchar, attributes json, owner_class varchar, platform_version varchar, owner_class_observed varchar)")
    conn.execute("insert into raw_rows values ('apple_music', ?, 'am_playlist', ?, ?, '[]', ?, ?, null)",
                 [playlist_id, owner_id, name, label, version])
    derived = conn.execute(f"select {owners.observed_class('duckdb')} from raw_rows").fetchone()[0]
    assert derived == observed, case
    assert conn.execute(f"select {owners.served_class(repr(derived))} from raw_rows").fetchone()[0] == served, case


@pytest.mark.parametrize(
    ("observed", "label", "served"),
    [
        ("user", "editorial", "user"),  # a label never promotes an observed user
        ("user", "chart", "user"),
        ("dsp_algorithmic", "editorial", "dsp_algorithmic"),  # nor hides an algotorial list
        ("unknown", "editorial", "editorial"),  # it fills only an observed unknown
        ("unknown", None, "unknown"),
        ("editorial", "chart", "chart"),  # and may refine editorial to chart
        ("chart", "editorial", "chart"),
        ("editorial", "curator", "editorial"),
    ],
)
def test_served_class_precedence(observed, label, served):
    conn = duckdb.connect()
    conn.execute("create table raw_rows (owner_class varchar)")
    conn.execute("insert into raw_rows values (?)", [label])
    assert conn.execute(f"select {owners.served_class(repr(observed))} from raw_rows").fetchone()[0] == served


# Rows from a dump landed before the rule: no `owner_class_observed`, the names as the parser kept them,
# and the name each keeps once landed.
OLD_ROWS = [
    ("spotify", "37i9dQZF1DXcBWIGoYBM5M", "sp_playlist_page", "spotify", "Spotify", "Spotify"),
    ("spotify", "0userlist0000000000000", "sp_playlist_page", "listener", "A Listener", None),
    ("bandcamp", "daily-list", "bc_daily_list", None, "A Writer", owners.PLATFORM_OWNER_NAMES["bc_daily_list"]),
    ("apple_music", "pl.28926c578a80475c904026ea97646ad5", "am_playlist", "976439539", "Apple Music Hip-Hop",
     "Apple Music Hip-Hop"),
    ("apple_music", "pl.u-qxylEY6s3ar1kVv", "am_playlist", None, "Apple Music Fan", None),
]
OBSERVED_AT = datetime(2026, 9, 22, tzinfo=timezone.utc)


def old_dump(table: str, rows: list[dict], columns: dict[str, str]) -> tuple[dict, list[bytes]]:
    dump_id = str(uuid4())
    stream = BytesIO()
    pq.write_table(pa.Table.from_pylist([{**row, "_dump_id": dump_id} for row in rows]), stream)
    return {"id": dump_id, "target_table": table, "columns": {**columns, "_dump_id": "uuid"}}, [stream.getvalue()]


def relanded_names(warehouse, query) -> None:
    """Land old dumps into tables that have the observed column (as bootstrap leaves them), then
    re-land them as a repair does: each landing leaves the names the repair leaves."""
    snapshots = {**declared(PlaylistSnapshot), **LINEAGE}
    candidates = {**declared(PlaylistCandidate), **LINEAGE}
    warehouse.ensure("raw.playlist_snapshots", snapshots)
    warehouse.ensure("raw.playlist_candidates", candidates)
    rows = [{"platform": p, "playlist_id": i, "variant": "", "snapshot_id": "s", "observed_at": OBSERVED_AT,
             "owner_id": o, "owner_name": n, "owner_class": "unknown", "coverage": "full", "fetch_surface": f}
            for p, i, f, o, n, _ in OLD_ROWS]
    hints = [{"platform": "spotify", "playlist_id": "x", "snapshot_id": "s", "position": 1, "observed_at": OBSERVED_AT,
              "discovered_via": "search", "via_ref": "q", "hint_owner_name": "A Listener", "fetch_surface": "sp_search"}]
    dumps = [
        (*old_dump("raw.playlist_snapshots", rows, {k: snapshots[k] for k in rows[0]}), len(rows)),
        (*old_dump("raw.playlist_candidates", hints, {k: candidates[k] for k in hints[0]}), 1),
    ]
    warehouse_id = uuid4()
    for op, generation in (("load", 1), ("repair", 2)):
        for manifest, parts, count in dumps:
            claim = {"warehouse_id": warehouse_id, "generation": generation, "claim_token": uuid4(), "op": op}
            assert warehouse.land(manifest, claim, parts) == count
        names = query("SELECT playlist_id, owner_name FROM raw.playlist_snapshots ORDER BY 1")
        assert names == sorted((i, kept) for _, i, _, _, _, kept in OLD_ROWS), op
        assert query("SELECT hint_owner_name FROM raw.playlist_candidates") == [(None,)], op


def test_relanding_an_old_dump_keeps_the_repaired_names_duckdb(tmp_path):
    path = str(tmp_path / "warehouse.duckdb")

    def query(statement):
        with duckdb.connect(path) as conn:
            return [tuple(r) for r in conn.execute(statement).fetchall()]

    relanded_names(DuckDBWarehouse(path), query)


@pytest.mark.docker
def test_relanding_an_old_dump_keeps_the_repaired_names_postgres():
    if not os.environ.get("MDP_CONTROL_ADMIN_URL"):
        pytest.skip("Requires the isolated integration stack")
    options = conninfo_to_dict(os.environ["MDP_WAREHOUSE_URL"])
    admin = os.environ["MDP_CONTROL_ADMIN_URL"]
    database = "mdp_relanding_" + uuid4().hex[:12]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(database), sql.Identifier(options["user"])))
    try:
        options["dbname"] = database
        url = make_conninfo(**options)

        def query(statement):
            with psycopg.connect(url) as conn:
                return [tuple(r) for r in conn.execute(statement).fetchall()]

        relanded_names(PostgresWarehouse(url), query)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
