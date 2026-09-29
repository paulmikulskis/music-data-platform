"""Tests for warehouse migration mirrors."""


from uuid import uuid4

import psycopg
from conftest import bound, url_database
from mdp_functions.warehouse.postgres import MIRRORS
from psycopg import sql


async def test_migrate_copies_the_control_mirrors(rt, databases, monkeypatch):
    await bound(rt, "fixture_accounts")  # exports a targets revision
    _, run = await bound(rt, "billboard_hot100")
    await rt.execute(run["id"])
    rt.cycles.close(run["cycle_id"])
    name = "mdp_test_mirrors_" + uuid4().hex[:12]
    admin = databases["admin_warehouse"]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(url_database(admin, name)) as conn:
            conn.execute("CREATE SCHEMA raw AUTHORIZATION loader_wh")
            conn.execute(sql.SQL("GRANT TEMPORARY ON DATABASE {} TO loader_wh").format(sql.Identifier(name)))
        monkeypatch.setenv("MDP_MIGRATION_TEST_URL", url_database(rt.settings.warehouse_url, name))
        with psycopg.connect(databases["admin_control"]) as conn:
            destination = conn.execute(
                "INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) "
                "VALUES ('postgres',%s,'MDP_MIGRATION_TEST_URL',false) RETURNING id",
                (name,),
            ).fetchone()[0]
        result = rt.migrate(destination, source_keys=["billboard_hot100"])
        assert result["loaded"] > 0

        def mirrored(url):
            with psycopg.connect(url) as conn:
                return {
                    table: conn.execute(
                        sql.SQL("SELECT count(*) FROM raw.{}").format(sql.Identifier(table))
                    ).fetchone()[0]
                    for table in sorted(MIRRORS)
                }

        source, copied = mirrored(admin), mirrored(url_database(admin, name))
        assert copied == source and copied["cycles"] and copied["dump_stamps"] and copied["targets"]
        # Copying again changes nothing: every mirror upserts by its key.
        rt.migrate(destination, source_keys=["billboard_hot100"])
        assert mirrored(url_database(admin, name)) == source
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
