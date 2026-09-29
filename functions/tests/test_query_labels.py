"""Input scope survives aliases, CTEs, aggregates and bounded result previews."""

import importlib.util

import psycopg
import pytest
from mdp_functions.explore import install
from mdp_functions.query_labels import describe, inputs, record
from mdp_functions.relation_labels import combine, load, relation_label
from mdp_functions.settings import REPO


def test_aliases_and_ctes_are_not_tenants():
    relations, unresolved = inputs(
        'WITH x AS (SELECT * FROM "tenant_one_marts".a) SELECT count(*) FROM x JOIN "tenant_two-co_marts".b y ON true'
    )
    assert relations == [("tenant_one_marts", "a"), ("tenant_two-co_marts", "b")]
    assert not unresolved
    assert inputs("SELECT f() FROM tenant_one_marts.a")[1]
    assert inputs("SELECT * FROM (")[1]


def test_rights_and_categories_use_all_inputs():
    base = dict(
        relation_label("raw", "account_snapshots"), category="public platform data"
    )
    restrictive = dict(base, category="tenant-private", learning=False, resale=False)
    result = combine([dict(base, learning=True, resale=True), restrictive])
    assert result["category"] == "tenant-private"
    assert not result["learning"] and not result["resale"]
    assert (
        relation_label("tenant_two-co_marts", "mart_scoped_fixture")["tenant"]
        == "two-co"
    )
    assert not combine([])["learning"]


def test_privacy_is_declared_and_generated():
    assert load()["raw.playlist_snapshots"]["privacy"]["owner_id"] == "pseudonym"
    assert load()["raw.playlist_items"]["privacy"]["added_by"] == "pseudonym"
    for relation, labels in load().items():
        if relation.startswith("raw."):
            assert set(labels["privacy"]) == set(labels["columns"])
            assert set(labels["privacy"].values()) <= {"non_personal", "pseudonym", "omit"}


def audit_module():
    spec = importlib.util.spec_from_file_location(
        "query_audit", REPO / "ops/fly/postgres/boot/query-audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalized_audit_never_keeps_literals_comments_or_utility():
    module = audit_module()
    query = module.normalized(
        "SELECT 'private-value' FROM tenant_one_marts.a /* secret-comment */ WHERE n=42"
    )
    assert (
        "private-value" not in query
        and "secret-comment" not in query
        and "42" not in query
    )
    assert (
        module.normalized("ALTER ROLE explorer_fixture PASSWORD 'private-password'")
        is None
    )
    assert module.normalized("SELECT * FROM (") is None


@pytest.fixture
def catalog_db(catalog_databases):
    with psycopg.connect(catalog_databases["admin_warehouse"]) as conn:
        yield conn


def test_shared_role_and_result_scope(catalog_db):
    conn = catalog_db
    conn.execute(
        "CREATE TABLE raw.playlist_items (added_by text, tenant_id uuid, _extra jsonb)"
    )
    conn.execute(
        "INSERT INTO raw.playlist_items VALUES ('private-identifier','00000000-0000-0000-0000-000000000001','{\"name\":\"private-identifier\"}'), ('other-identifier','00000000-0000-0000-0000-000000000002','{}')"
    )
    install(conn)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE explorer_ro")
        rows = conn.execute("SELECT * FROM explore_raw.playlist_items").fetchall()
        assert len(rows) == 2 and all(len(r[0]) == 64 and r[2] is None for r in rows)
        for query in (
            "SELECT * FROM raw.playlist_items",
            "DELETE FROM explore_raw.playlist_items",
            "CREATE TABLE public.forbidden(n int)",
            "SELECT * FROM mdp.pseudonym_key",
        ):
            with (
                pytest.raises(psycopg.errors.InsufficientPrivilege),
                conn.transaction(),
            ):
                conn.execute(query)
    conn.execute("RESET ROLE")
    labels = describe(
        conn,
        "WITH x AS (SELECT * FROM explore_raw.playlist_items) SELECT count(*) FROM x LIMIT 1",
    )
    assert labels["cross_tenant"] and len(labels["tenants"]) == 2
    assert labels["category"] == "personal" and not labels["learning"]
    record(
        conn,
        "fixture",
        "select count(*) from explore_raw.playlist_items",
        labels,
        event_id="proof",
    )
    record(
        conn,
        "fixture",
        "select count(*) from explore_raw.playlist_items",
        labels,
        event_id="proof",
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM catalog.query_audit WHERE event_id='proof'"
        ).fetchone()[0]
        == 1
    )
    for slug in ("one", "two-co"):
        conn.execute(
            psycopg.sql.SQL("CREATE SCHEMA {}").format(
                psycopg.sql.Identifier(f"tenant_{slug}_marts")
            )
        )
        conn.execute(
            psycopg.sql.SQL("CREATE TABLE {}.example(n int)").format(
                psycopg.sql.Identifier(f"tenant_{slug}_marts")
            )
        )
    labels = describe(
        conn,
        'SELECT count(*) FROM tenant_one_marts.example CROSS JOIN "tenant_two-co_marts".example',
    )
    assert labels["cross_tenant"] and labels["tenants"] == ["one", "two-co"]
    with conn.transaction():
        conn.execute("SET LOCAL ROLE explorer_ro")
        conn.execute('SELECT * FROM "explore_tenant_two-co_marts".example')
        assert conn.execute(
            "SELECT readable FROM catalog.relations WHERE schema='raw' AND name='playlist_items'"
        ).fetchone() == (False,)
    conn.execute("RESET ROLE")
    with conn.transaction():
        conn.execute("SET LOCAL ROLE analyst_ro")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute('SELECT * FROM "explore_tenant_two-co_marts".example')


def test_statistics_checkpoints_without_duplicate_or_sql_storage(catalog_db):
    conn = catalog_db
    module = audit_module()
    conn.execute(
        "CREATE TABLE catalog.audit_counters(userid oid,queryid bigint,stats_since timestamptz,calls bigint,PRIMARY KEY(userid,queryid,stats_since))"
    )
    conn.execute(
        "CREATE TABLE catalog.pg_stat_statements(userid oid,dbid oid,queryid bigint,stats_since timestamptz,calls bigint,query text,toplevel boolean)"
    )
    conn.execute(
        "INSERT INTO catalog.pg_stat_statements SELECT 'explorer_ro'::regrole,oid,1,now(),2,%s,true FROM pg_database WHERE datname=current_database()",
        (
            "SELECT 'private-value' FROM tenant_one_marts.example CROSS JOIN tenant_two_marts.example",
        ),
    )
    module.consume(conn)
    module.consume(conn)
    assert conn.execute(
        "SELECT count(*),bool_and(cross_tenant) FROM catalog.query_audit WHERE channel='postgres'"
    ).fetchone() == (1, True)
    conn.execute("UPDATE catalog.pg_stat_statements SET calls=3")
    module.consume(conn)
    assert conn.execute(
        "SELECT sum((labels->>'calls')::int) FROM catalog.query_audit WHERE channel='postgres'"
    ).fetchone() == (3,)
    assert "private-value" not in str(
        conn.execute("SELECT * FROM catalog.query_audit").fetchall()
    )
    # Reuse of a ring slot and expiry are enforced by Postgres even if the worker stops.
    conn.execute("UPDATE catalog.query_audit SET occurred_at=now()-interval '31 days'")
    conn.execute("UPDATE catalog.pg_stat_statements SET calls=4")
    module.consume(conn)
    assert conn.execute(
        "SELECT count(*) FROM catalog.query_audit WHERE channel='postgres'"
    ).fetchone() == (1,)
    conn.execute("INSERT INTO catalog.query_audit(event_id,actor,occurred_at,channel,query_hash,tenants,labels,cross_tenant,unresolved,slot) SELECT 'slot-replacement',actor,occurred_at,channel,query_hash,tenants,labels,cross_tenant,unresolved,slot FROM catalog.query_audit WHERE channel='postgres'")
    assert conn.execute("SELECT event_id FROM catalog.query_audit WHERE channel='postgres'").fetchall() == [('slot-replacement',)]
    conn.execute("DROP TABLE catalog.pg_stat_statements,catalog.audit_counters")
    conn.execute("DELETE FROM catalog.query_audit WHERE channel='postgres'")
    conn.commit()


def test_materialized_dbt_inputs_keep_tenant_scope(catalog_db):
    labels = describe(
        catalog_db,
        "select count(*) from wb_fixture.aggregate",
        input_queries={
            (
                "wb_fixture",
                "aggregate",
            ): "select count(*) from tenant_first_marts.hidden cross join tenant_second_marts.hidden",
        },
    )
    assert labels["cross_tenant"] and labels["tenants"] == ["first", "second"]


def test_labeling_never_executes_a_user_view(catalog_db):
    conn = catalog_db
    conn.execute(
        "CREATE FUNCTION public.audit_trap() RETURNS text LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'must never execute'; END $$"
    )
    conn.execute(
        "CREATE VIEW staging.audit_trap AS SELECT public.audit_trap() AS tenant_id"
    )
    labels = describe(conn, "SELECT * FROM staging.audit_trap")
    assert labels["unresolved"]


def test_every_relation_and_column_has_a_comment(catalog_db):
    conn = catalog_db
    assert conn.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT LIKE 'pg_%' AND n.nspname NOT IN ('information_schema','control') AND obj_description(c.oid,'pg_class') IS NULL"
    ).fetchone() == (0,)
    assert conn.execute(
        "SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p','v','m','f') AND n.nspname NOT LIKE 'pg_%' AND n.nspname NOT IN ('information_schema','control') AND a.attnum>0 AND NOT a.attisdropped AND col_description(c.oid,a.attnum) IS NULL"
    ).fetchone() == (0,)
    conn.execute("CREATE TABLE staging.label_probe(n int)")
    conn.execute("COMMENT ON TABLE staging.label_probe IS '{invalid'")
    conn.execute("SELECT * FROM catalog.relations").fetchall()


def test_empty_existing_schema_gets_usage_before_table_creation(catalog_db):
    conn = catalog_db
    conn.execute("CREATE SCHEMA IF NOT EXISTS explore_empty_probe")
    conn.execute("REVOKE USAGE ON SCHEMA explore_empty_probe FROM explorer_ro")
    conn.execute(
        (REPO / "ops/fly/postgres/boot/init/10-warehouse-grants.sql").read_text()
    )
    try:
        assert conn.execute(
            "SELECT has_schema_privilege('explorer_ro','explore_empty_probe','USAGE')"
        ).fetchone() == (True,)
    finally:
        conn.execute("DROP SCHEMA explore_empty_probe")
        conn.commit()


def test_unknown_raw_values_are_hidden_and_loader_widens_views(catalog_db, catalog_databases):
    from mdp_functions.warehouse.postgres import PostgresWarehouse

    conn = catalog_db
    conn.execute(
        "CREATE TABLE raw.playlist_items(added_by text, duration_ms bigint, unknown_person text, fields jsonb, _extra jsonb)"
    )
    conn.execute("ALTER TABLE raw.playlist_items OWNER TO loader_wh")
    conn.execute(
        "INSERT INTO raw.playlist_items VALUES ('private-identifier',12,'private-name','{\"private\":\"identifier\"}','{}')"
    )
    install(conn)
    conn.commit()
    try:
        with conn.transaction():
            conn.execute("SET LOCAL ROLE explorer_ro")
            row = conn.execute("SELECT * FROM explore_raw.playlist_items").fetchone()
            assert len(row[0]) == 64 and row[1:] == (12, None, None, None)
        PostgresWarehouse(catalog_databases["warehouse_url"]).ensure(
            "raw.playlist_items", {"duration_ms": "double"}
        )
        with conn.transaction():
            conn.execute("SET LOCAL ROLE explorer_ro")
            assert conn.execute(
                "SELECT pg_typeof(duration_ms)::text,added_by FROM explore_raw.playlist_items"
            ).fetchone() == ("double precision", row[0])
    finally:
        conn.execute("DROP VIEW explore_raw.playlist_items")
        conn.execute("DROP TABLE raw.playlist_items")
        conn.commit()


def test_global_explore_copy_uses_selected_column_labels(catalog_db):
    conn = catalog_db
    conn.execute('CREATE TABLE staging.stg_playlist__items(track_title text, duration_ms bigint, _extra jsonb)')
    install(conn)
    for query in (
        'select track_title, duration_ms from explore_staging.stg_playlist__items',
        'with accounts as (select * from explore_staging.stg_playlist__items) select upper(track_title) as track_title from accounts',
        'select * from explore_staging.stg_playlist__items',
        'with stg_playlist__items as (select track_title from stg_playlist__items) select * from stg_playlist__items',
    ):
        labels = describe(conn, query, search_path=('explore_staging',))
        assert labels['tenant'] == 'global'
        assert labels['tenants'] == [] and not labels['cross_tenant']
        assert not labels['unresolved'], labels
        assert labels['category'] != 'personal'
    labels = describe(conn, 'select _extra from staging.stg_playlist__items')
    assert labels['category'] == 'personal'


def test_one_tenant_is_resolved_and_missing_input_is_named(catalog_db):
    conn = catalog_db
    conn.execute('CREATE SCHEMA tenant_fixture_marts')
    conn.execute('CREATE TABLE tenant_fixture_marts.mart_scoped_fixture(platform_account_id text)')
    labels = describe(conn, 'select platform_account_id from tenant_fixture_marts.mart_scoped_fixture')
    assert labels['tenants'] == ['fixture'] and not labels['cross_tenant']
    assert not labels['unresolved'], labels
    labels = describe(conn, 'select handle from staging.missing_input')
    assert labels['unresolved'] and 'staging.missing_input' in labels['unresolved_inputs']
    assert labels['category'] == 'personal' and not labels['learning']


@pytest.mark.parametrize("projection", [
    "platform_track_id AS x, added_by AS x",
    "added_by AS x, platform_track_id AS x",
    "upper(platform_track_id) AS x, upper(added_by) AS x",
    "platform_track_id AS x, added_by AS y",
    "*",
])
def test_each_selected_expression_keeps_its_category(catalog_db, projection):
    conn = catalog_db
    conn.execute("CREATE TABLE staging.stg_playlist__items(platform_track_id text, added_by text)")
    install(conn)
    query = f"SELECT {projection} FROM explore_staging.stg_playlist__items"
    # These are valid result columns even when their names repeat.
    conn.execute(query)
    labels = describe(conn, query)
    assert labels["category"] == "personal"
    assert not labels["unresolved"], labels
