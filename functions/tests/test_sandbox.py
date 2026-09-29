"""Real login privileges, portable snapshots and sandbox-to-model boundaries."""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import duckdb
import psycopg
import pytest
import yaml
from mdp_functions.sandbox import create_sandbox
from mdp_functions.warehouse_lift import lift, rewrite
from mdp_functions.warehouse_snapshot import snapshot
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def sandbox_databases(databases):
    # Keep fixture tables and event triggers away from other files in the CI shard.
    name = "mdp_sandbox_" + uuid4().hex[:12]
    admin = databases["admin_warehouse"]
    isolated = dict(databases)
    for key in ("admin_warehouse", "warehouse_url", "service_read_url"):
        options = conninfo_to_dict(databases[key])
        options["dbname"] = name
        isolated[key] = make_conninfo(**options)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield isolated
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest.fixture
def sandbox_logins(sandbox_databases):
    handle = "test_" + uuid4().hex[:10]
    roles = ["analyst_" + handle, "explorer_" + handle]
    schema = "sandbox_" + handle
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        conn.execute(
            (ROOT / "ops/fly/postgres/boot/init/10-warehouse-grants.sql").read_text()
        )
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname='explorer_ro'"
        ).fetchone():
            conn.execute("CREATE ROLE explorer_ro NOLOGIN")
        for role, group in zip(roles, ("analyst_ro", "explorer_ro"), strict=True):
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'fixture' IN ROLE {}").format(
                    sql.Identifier(role), sql.Identifier(group)
                )
            )
        create_sandbox(conn, handle, roles[0])
    urls = []
    for role in roles:
        options = conninfo_to_dict(sandbox_databases["admin_warehouse"])
        options.update(user=role, password="fixture")
        urls.append(make_conninfo(**options))
    try:
        yield schema, urls
    finally:
        with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
            for role in roles:
                conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_sandbox_owner_team_and_service_boundaries(sandbox_databases, sandbox_logins):
    schema, (owner, teammate) = sandbox_logins
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {}.results (id int)").format(sql.Identifier(schema))
        )
        conn.execute(
            sql.SQL("INSERT INTO {}.results VALUES (1)").format(sql.Identifier(schema))
        )
        conn.execute(
            sql.SQL("ALTER TABLE {}.results ADD COLUMN score numeric").format(
                sql.Identifier(schema)
            )
        )
        for statement in (
            f"GRANT USAGE ON SCHEMA {schema} TO dbt_transform",
            f"GRANT USAGE ON SCHEMA {schema} TO reader_wh",
            f"GRANT USAGE ON SCHEMA {schema} TO PUBLIC",
            f"GRANT SELECT ON {schema}.results TO api_key_reader",
            f"GRANT CREATE ON SCHEMA {schema} TO analyst_ro",
            f"GRANT UPDATE (id) ON {schema}.results TO analyst_ro",
            "CREATE TABLE marts.sandbox_escape(id int)",
            "CREATE SCHEMA sandbox_escape",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
        conn.execute(
            sql.SQL(
                "CREATE FUNCTION {}.private_function() RETURNS integer LANGUAGE SQL AS 'SELECT 1'"
            ).format(sql.Identifier(schema))
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                sql.SQL(
                    "GRANT EXECUTE ON FUNCTION {}.private_function() TO analyst_ro"
                ).format(sql.Identifier(schema))
            )
    with psycopg.connect(teammate, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                sql.SQL("SELECT {}.private_function()").format(sql.Identifier(schema))
            )
        assert conn.execute(
            sql.SQL("SELECT id FROM {}.results").format(sql.Identifier(schema))
        ).fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                sql.SQL("INSERT INTO {}.results VALUES (2,3)").format(
                    sql.Identifier(schema)
                )
            )
    for role in (
        "reader_wh",
        "api_key_reader",
        "dbt_transform",
        "service_read",
        "workbench_wh",
    ):
        options = conninfo_to_dict(sandbox_databases["admin_warehouse"])
        options.update(user=role, password=role)
        with (
            psycopg.connect(make_conninfo(**options)) as conn,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            conn.execute(
                sql.SQL("SELECT * FROM {}.results").format(sql.Identifier(schema))
            )

    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        conn.execute("SET LOCAL ROLE dbt_transform")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("CREATE SCHEMA sandbox_service_escape")


def test_snapshots_keep_flags_labels_types_and_stamps(
    sandbox_databases, sandbox_logins, tmp_path
):
    schema, (owner, _) = sandbox_logins
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as admin:
        admin.execute(
            "CREATE TABLE IF NOT EXISTS marts._build (relation text PRIMARY KEY, cycle_id text, close_no bigint, built_at timestamptz)"
        )
        # Build storage stays private even when the exported relation is readable.
        admin.execute("REVOKE SELECT ON marts._build FROM analyst_ro, explorer_ro")
        admin.execute(
            "INSERT INTO marts._build VALUES (%s,'fixture-build',7,now())",
            (f"{schema}.results",),
        )
    with psycopg.connect(owner, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(
            sql.SQL(
                "CREATE TABLE {}.results AS SELECT 'pseudonym'::text AS owner_key, "
                "false AS learning_eligible,true AS resale_permitted,'[\"fixture\"]'::text AS source_keys, "
                "'global'::text AS tenant_label,12.34::numeric(8,2) AS score, NULL::integer AS empty, ARRAY[1,2] AS numbers, '\\N'::text AS literal_null, 1.23456789::numeric AS precise"
            ).format(sql.Identifier(schema))
        )
        out = tmp_path / "warehouse.duckdb"
        assert "1 relations, 1 rows" in snapshot(conn, out=out, schemas=[schema])
        with duckdb.connect(str(out), read_only=True) as db:
            row = db.execute(f"SELECT * FROM {schema}.results").fetchone()
            assert row[:5] == ("pseudonym", False, True, '["fixture"]', "global")
            assert str(row[5]) == "12.34" and row[6] is None
            assert row[7] == "[1,2]" and row[8] == "\\N"
            assert row[9] == "1.23456789"
            manifest = json.loads(
                db.execute("SELECT manifest FROM _mdp_snapshot").fetchone()[0]
            )
            assert manifest["relations"][0]["build"]["cycle_id"] == "fixture-build"
            assert manifest["relations"][0]["build"]["close_no"] == 7
            assert manifest["relations"][0]["captured_at"]
            assert "labels" in manifest["relations"][0]
            assert manifest["scope_unresolved"] is True
        folder = tmp_path / "parquet"
        snapshot(conn, parquet=folder, schemas=[schema])
        assert (folder / "_mdp_snapshot.json").exists()
        with duckdb.connect() as db:
            assert db.execute(
                "SELECT learning_eligible,resale_permitted,tenant_label FROM read_parquet(?)",
                [str(folder / schema / "results.parquet")],
            ).fetchone() == (False, True, "global")
        with pytest.raises(ValueError, match="already exists"):
            snapshot(conn, out=out, schemas=[schema])


def test_lift_copies_definition_and_keeps_restrictive_flags(
    sandbox_databases, sandbox_logins, tmp_path
):
    schema, (owner, _) = sandbox_logins
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS marts.mart_lift_input (id text, label varchar, source_keys text, learning_eligible boolean, resale_permitted boolean)"
        )
    manifest = {
        "nodes": {
            "model.fixture": {
                "name": "mart_lift_input",
                "resource_type": "model",
                "schema": "marts",
                "alias": "mart_lift_input",
                "config": {"schema": "marts", "tags": ["cadence:daily"]},
            }
        }
    }
    folder = tmp_path / "dbt/models/marts/global"
    folder.mkdir(parents=True)
    with psycopg.connect(owner, autocommit=True, row_factory=dict_row) as conn:
        conn.execute(
            sql.SQL(
                "CREATE VIEW {}.draft AS SELECT * FROM marts.mart_lift_input"
            ).format(sql.Identifier(schema))
        )
        contract, model = lift(
            conn, tmp_path, "mart_lifted", f"{schema}.draft", manifest
        )
    text = model.read_text()
    assert "ref('mart_lift_input')" in text
    assert "sandbox_" not in text
    assert "learning_inputs=['learning_eligible']" in text
    assert "resale_inputs=['resale_permitted']" in text
    assert "input.source_keys as text) as _source_keys" in text
    parsed = yaml.safe_load(contract.read_text())["models"][0]
    assert parsed["config"]["contract"]["enforced"]
    assert all(c["description"] for c in parsed["columns"])
    assert {c["name"]: c["data_type"] for c in parsed["columns"]}["label"] == "text"
    for query, message in [
        (f"select * from {schema}.draft", "Inline sandbox"),
        ("select * from explore_raw.personal", "Cannot resolve"),
    ]:
        with pytest.raises(ValueError, match=message):
            rewrite(query, manifest)


@pytest.mark.parametrize(
    "path,content",
    [
        ("models/bad.sql", "select * from sandbox_demo.results"),
        ("models/bad.sql", "select * from {{ source('sandbox_demo', 'results') }}"),
        ("models/sources.yml", "sources:\n- name: scratch\n  schema: sandbox_demo"),
        (
            "macros/bad.sql",
            "{% macro x() %}select * from sandbox_demo.results{% endmacro %}",
        ),
    ],
)
def test_sandbox_lint_covers_models_sources_and_macros(tmp_path, path, content):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(content)
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(ROOT / "dbt"),
            "python",
            str(ROOT / "ops/ci/dbt_relations.py"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "sandbox relations cannot feed dbt" in result.stdout


async def test_explorer_snapshot_and_direct_results_keep_privacy(
    sandbox_databases, sandbox_logins, tmp_path
):
    # This integration runs when the exploration catalog is present in the checkout.
    explore = pytest.importorskip("mdp_functions.explore")
    from mdp_functions.settings import Settings
    from mdp_functions.workbench import Workbench

    schema, (_, explorer) = sandbox_logins
    scratch = schema + "_explore"
    with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
        for filename in (
            "10-warehouse-grants.sql",
            "15-label-catalog.sql",
            "16-label-definitions.sql",
            "17-explore-views.sql",
        ):
            conn.execute((ROOT / "ops/fly/postgres/boot/init" / filename).read_text())
        conn.execute(
            "CREATE TABLE raw.playlist_items (added_by text, tenant_id uuid, _extra jsonb)"
        )
        conn.execute(
            "INSERT INTO raw.playlist_items VALUES ('private-fixture-a','00000000-0000-0000-0000-000000000001','{}'), ('private-fixture-b','00000000-0000-0000-0000-000000000002','{}')"
        )
        explore.install(conn)
        create_sandbox(
            conn, scratch.removeprefix("sandbox_"), conninfo_to_dict(explorer)["user"]
        )
    options = conninfo_to_dict(sandbox_databases["admin_warehouse"])
    options.update(user="workbench_wh", password="workbench_wh")
    wb = Workbench(
        Settings(
            control_url=sandbox_databases["control_url"],
            control_rt_url=sandbox_databases["admin_control"],
            workbench_admin_url=sandbox_databases["admin_warehouse"],
            workbench_wh_url=make_conninfo(**options),
            dump_root=tmp_path.as_uri(),
        )
    )
    session = None
    try:
        with psycopg.connect(explorer, autocommit=True, row_factory=dict_row) as conn:
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {}.copied AS SELECT added_by, row_number() over ()::text AS tenant_id FROM explore_raw.playlist_items"
                ).format(sql.Identifier(scratch))
            )
            original = conn.execute(
                "SELECT added_by FROM explore_raw.playlist_items ORDER BY added_by"
            ).fetchall()
            assert all(
                r["added_by"] and "private-fixture" not in r["added_by"]
                for r in original
            )
            out = tmp_path / "pseudonyms.duckdb"
            assert "cross-tenant=true" in snapshot(conn, out=out, schemas=[scratch])
        with duckdb.connect(str(out), read_only=True) as db:
            assert db.execute(
                f"SELECT added_by FROM {scratch}.copied ORDER BY added_by"
            ).fetchall() == [(r["added_by"],) for r in original]
            manifest = json.loads(
                db.execute("SELECT manifest FROM _mdp_snapshot").fetchone()[0]
            )
            assert (
                manifest["cross_tenant"] is True and manifest["relations"][0]["labels"]
            )
        created = wb.create_session("fixture-sandbox")
        session = wb.session(created["sessionId"], "fixture-sandbox")
        result = await wb.direct(
            str(uuid4()),
            session,
            {"sql": f"SELECT count(*) AS n FROM {scratch}.copied"},
        )
        assert result["rows"] == [{"n": 2}]
        assert result["labels"]["cross_tenant"] is True
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            wb.query(session, "SELECT * FROM raw.playlist_items")
    finally:
        wb.db.close()
        wb.owner.close()
        with psycopg.connect(sandbox_databases["admin_warehouse"]) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(scratch))
            )
            if session:
                role = sql.Identifier(session["scratch_schema"])
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(role))
                conn.execute(sql.SQL("DROP OWNED BY {}").format(role))
                conn.execute(sql.SQL("DROP ROLE {}").format(role))
