"""Migration 0011: existing closed cycles keep their lists and take close_no 0; pre-existing dumps
committed to their pinned warehouse are stamped 0; the first catch-up mirrors both before
mirrored_close_no reaches 0."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import duckdb
import psycopg
import pytest
from mdp_functions.control_db import ControlDB
from mdp_functions.cycles import Cycles
from mdp_functions.settings import REPO, Settings
from mdp_functions.warehouse.duckdb import DuckDBWarehouse
from psycopg.conninfo import make_conninfo

pytestmark = pytest.mark.docker

MIGRATIONS = sorted((REPO / "control/packages/control-db/drizzle").glob("00*.sql"))


def apply(target: str, path: Path) -> None:
    subprocess.run(
        ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "--dbname", target, "-f", str(path)],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def scratch():
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    name = "control_stamp_" + uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}" OWNER migrator')
    try:
        yield make_conninfo(os.environ["MDP_CONTROL_DATABASE_URL"], dbname=name), make_conninfo(admin, dbname=name)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_existing_cycles_keep_lists_and_history_is_stamped_zero(scratch, tmp_path):
    migrator, admin = scratch
    for path in MIGRATIONS:
        if path.name >= "0011":
            break
        apply(migrator, path)
    ids: dict[str, object] = {}
    tenant = "tenant:00000000-0000-4000-8000-000000000001"
    with psycopg.connect(admin) as conn:
        warehouse = conn.execute(
            "INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres','warehouse','X',true) RETURNING id"
        ).fetchone()[0]
        streamline = conn.execute(
            "INSERT INTO control.streamline(source_key,layer) VALUES ('probe','bronze') RETURNING id"
        ).fetchone()[0]
        destination = conn.execute(
            "INSERT INTO control.warehouse(adapter,database,dsn_secret_ref) VALUES ('postgres','migrate','Y') RETURNING id"
        ).fetchone()[0]

        def cycle(label, scope, status, minute=None):
            ids[label] = conn.execute(
                "INSERT INTO control.cycle(cadence,scope,opened_by_dbt_run_id,status,closed_at) VALUES "
                "('daily',%s,%s,%s,CASE WHEN %s::int IS NULL THEN NULL ELSE timestamptz '2026-09-01' + %s * interval '1 minute' END) RETURNING id",
                (scope, label, status, minute, minute),
            ).fetchone()[0]

        def dump(label, scope, status="loaded", migrate=None):
            run = conn.execute(
                "INSERT INTO control.run(kind,work_key,scope,warehouse_id,streamline_id) VALUES ('invoke',%s,%s,%s,%s) RETURNING id",
                (label, scope, warehouse, streamline),
            ).fetchone()[0]
            ids[label] = conn.execute(
                "INSERT INTO control.dump(kind,run_id,streamline_id,uri_prefix) VALUES ('output',%s,%s,'x') RETURNING id",
                (run, streamline),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO control.load(dump_id,warehouse_id,target_table,status) VALUES (%s,%s,'raw.probe',%s)",
                (ids[label], warehouse, status),
            )
            if migrate:
                # A load to a migrate destination never decides the stamp.
                conn.execute(
                    "INSERT INTO control.load(dump_id,warehouse_id,target_table,status) VALUES (%s,%s,'raw.probe',%s)",
                    (ids[label], destination, migrate),
                )

        def listed(label, members, phase="bronze"):
            for member in members:
                conn.execute(
                    "INSERT INTO control.cycle_input(cycle_id,dump_id,phase) VALUES (%s,%s,%s)",
                    (ids[label], ids[member], phase),
                )

        for label in ("g1", "g2", "g3"):
            dump(label, "global")
        dump("late", "global", status="pending")
        dump("migrating", "global", migrate="pending")
        dump("elsewhere", "global", status="pending", migrate="loaded")
        dump("t1", tenant)
        cycle("C1", "global", "closed", 1)
        cycle("C2", "global", "closed", 3)
        cycle("T1", tenant, "closed", 2)
        cycle("open", "global", "open")
        listed("C1", ["g1"])
        listed("C1", ["g2"], "derived")
        listed("C2", ["g1", "g2", "g3"])
        listed("T1", ["t1"])
        listed("open", ["g3"], "derived")
        lists = {
            label: {str(r[0]) for r in conn.execute("SELECT dump_id FROM control.cycle_input WHERE cycle_id=%s", (ids[label],))}
            for label in ("C1", "C2", "T1")
        }
    apply(migrator, next(p for p in MIGRATIONS if p.name.startswith("0011")))
    # Later migrations only add columns and tables; the current catch-up reads them.
    for later in (p for p in MIGRATIONS if p.name >= "0012"):
        apply(migrator, later)
    with psycopg.connect(admin) as conn:
        modes =dict(conn.execute("SELECT opened_by_dbt_run_id,manifest_mode::text FROM control.cycle").fetchall())
        stamps = {
            label: conn.execute("SELECT close_no FROM control.dump WHERE id=%s", (ids[label],)).fetchone()[0]
            for label in ("g1", "g2", "g3", "late", "t1", "migrating", "elsewhere")
        }
        closes = dict(conn.execute("SELECT opened_by_dbt_run_id,close_no FROM control.cycle").fetchall())
        scopes = dict(conn.execute("SELECT scope,(last_close_no,mirrored_close_no)::text FROM control.scope_close").fetchall())
        unmirrored = conn.execute("SELECT count(*) FROM control.cycle_input WHERE mirrored_at IS NULL").fetchone()[0]
        manifests = {
            label: {str(r[0]) for r in conn.execute("SELECT dump_id FROM control.cycle_manifest(%s)", (ids[label],))}
            for label in lists
        }
        assert conn.execute("SELECT count(*) FROM control.dump WHERE scope IS NULL").fetchone()[0] == 0
    assert modes == {"C1": "list", "C2": "list", "T1": "list", "open": "list"}
    assert manifests == lists
    assert stamps == {"g1": 0, "g2": 0, "g3": 0, "late": None, "t1": 0, "migrating": 0, "elsewhere": None}
    assert closes == {"C1": 0, "C2": 0, "T1": 0, "open": None}
    assert scopes == {"global": "(0,-1)", tenant: "(0,-1)"}
    assert unmirrored == 1  # the open cycle's derived row; list-mode rows were already mirrored

    # The first stamp-mode close sees full history, stamps the late dump once it commits, and leaves
    # every list-mode manifest untouched. Its catch-up writes the close-0 stamps and raw.cycles rows.
    with psycopg.connect(admin) as conn:
        conn.execute("UPDATE control.load SET status='loaded' WHERE dump_id=%s", (ids["late"],))
    db = ControlDB(admin)
    path = str(tmp_path / "warehouse.duckdb")
    try:
        cycles = Cycles(db, DuckDBWarehouse(path), Settings())
        closed = cycles.close(ids["open"])
        assert closed["close_no"] == 1 and closed["manifest_mode"] == "stamp"
        first = {str(r["dump_id"]) for r in db.all("SELECT dump_id FROM control.cycle_manifest(%s)", (ids["open"],))}
        assert first == {str(ids[label]) for label in ("g1", "g2", "g3", "late", "migrating")}
        assert {
            label: {str(r["dump_id"]) for r in db.all("SELECT dump_id FROM control.cycle_manifest(%s)", (ids[label],))}
            for label in lists
        } == lists
        assert db.one("SELECT mirrored_close_no FROM control.scope_close WHERE scope='global'")["mirrored_close_no"] == 1
        cycles.mirror()  # recovery's catch-up reaches the tenant scope
        assert db.one("SELECT mirrored_close_no FROM control.scope_close WHERE scope=%s", (tenant,))["mirrored_close_no"] == 0
        with duckdb.connect(path) as warehouse:
            rows = {
                str(r[0]): r[1:] for r in warehouse.execute("SELECT id,status,manifest_mode,close_no FROM raw.cycles").fetchall()
            }
            stamps = {str(r[0]): r[1] for r in warehouse.execute("SELECT dump_id,close_no FROM raw.dump_stamps").fetchall()}
        assert {label: rows[str(ids[label])] for label in ("C1", "C2", "T1", "open")} == {
            "C1": ("closed", "list", 0), "C2": ("closed", "list", 0), "T1": ("closed", "list", 0),
            "open": ("closed", "stamp", 1),
        }
        assert stamps == {
            str(ids["g1"]): 0, str(ids["g2"]): 0, str(ids["g3"]): 0, str(ids["t1"]): 0,
            str(ids["migrating"]): 0, str(ids["late"]): 1,
        }
    finally:
        db.pool.close()


def test_step_1b_moves_configuration_out_of_work_keys(scratch):
    migrator, admin = scratch
    for path in MIGRATIONS:
        if path.name >= "0010":
            break
        apply(migrator, path)
    with psycopg.connect(admin) as conn:
        warehouse = conn.execute(
            "INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres','warehouse','X',true) RETURNING id"
        ).fetchone()[0]
        streamline = conn.execute(
            "INSERT INTO control.streamline(source_key,layer) VALUES ('fixture_enrichment','gold') RETURNING id"
        ).fetchone()[0]

        def run(key, minute):
            return conn.execute(
                "INSERT INTO control.run(kind,work_key,scope,warehouse_id,streamline_id,created_at) "
                "VALUES ('invoke',%s,'global',%s,%s,timestamptz '2026-09-01' + %s * interval '1 minute') RETURNING id",
                (key, warehouse, streamline, minute),
            ).fetchone()[0]

        base, bare = '["fixture_enrichment","global",null,null,"c1"]', '["fixture_enrichment","global",null,null,"c2"]'
        first, rerun = run(base + ":config:aaa", 1), run(base + ":config:bbb", 2)
        scheduled, configured = run(bare, 3), run(bare + ":config:ccc", 4)
    apply(migrator, next(p for p in MIGRATIONS if p.name.startswith("0010")))
    with psycopg.connect(admin) as conn:
        keys = dict(conn.execute("SELECT id,work_key FROM control.run").fetchall())
    # The first configured run of a cycle takes the bare key; later ones become manual reruns.
    assert keys[first] == base and keys[scheduled] == bare
    assert keys[rerun] == f'manual:["config:{rerun}","fixture_enrichment","global"]'
    assert keys[configured] == f'manual:["config:{configured}","fixture_enrichment","global"]'
