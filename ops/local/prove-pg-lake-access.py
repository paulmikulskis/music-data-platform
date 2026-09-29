#!/usr/bin/env python3
"""Prove extension access on a disposable pg_lake warehouse; set MDP_PG_LAKE_TEST_URL."""

import os
from uuid import uuid4

import psycopg
from mdp_functions.settings import REPO
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def main():
    url = os.environ["MDP_PG_LAKE_TEST_URL"]
    assert conninfo_to_dict(url).get("host") in {"localhost", "127.0.0.1", "::1"}, (
        "This proof changes grants and fixture tables. Set MDP_PG_LAKE_TEST_URL to a disposable local warehouse."
    )
    relations = (
        "lake_iceberg.tables", "lake_iceberg.tables_external", "lake_iceberg.tables_internal",
        "lake_table.row_id_mappings", "__pg_lake_table_writes.row_id_mappings",
    )
    table = "preview_" + uuid4().hex[:12]
    with psycopg.connect(url, autocommit=True) as admin:
        version = admin.execute("SELECT extversion FROM pg_extension WHERE extname='pg_lake'").fetchone()
        assert version, "Install pg_lake in the disposable warehouse, then rerun."
        # Prove repair of an existing volume as well as repeatability.
        for relation in relations:
            admin.execute(sql.SQL("GRANT SELECT ON {} TO PUBLIC").format(sql.Identifier(*relation.split('.'))))
        for _ in range(2):
            admin.execute((REPO / "ops/fly/postgres/boot/init/10-warehouse-grants.sql").read_text())
        for role in ("analyst_ro", "explorer_ro"):
            for relation in relations:
                with admin.transaction():
                    admin.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                    try:
                        admin.execute(sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier(*relation.split('.'))))
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError(f"{role} reads {relation}; inspect warehouse grants.")
            print(f"PASS {role}: all five extension relations refuse SELECT")
        for role in ("dbt_transform", "loader_wh", "service_read", "postgres"):
            with psycopg.connect(make_conninfo(url, user=role, password=role), autocommit=True) as conn:
                for relation in relations:
                    conn.execute(sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier(*relation.split('.'))))
            print(f"PASS {role}: all five extension relations remain readable")
        raw = sql.Identifier("raw", table)
        transformed = sql.Identifier("intermediate", table)
        try:
            with psycopg.connect(make_conninfo(url, user="loader_wh", password="loader_wh")) as loader:
                loader.execute(sql.SQL("CREATE TABLE {}(id bigint, value text) USING iceberg").format(raw))
                with loader.cursor().copy(sql.SQL("COPY {} FROM STDIN").format(raw)) as copy:
                    for n in range(3):
                        copy.write_row((n, "fixture"))
                loader.execute(sql.SQL("UPDATE {} SET value='updated' WHERE id=1").format(raw))
                loader.execute(sql.SQL("DELETE FROM {} WHERE id=2").format(raw))
            print("PASS loader_wh: Iceberg CREATE, COPY, UPDATE and DELETE commit")
            for role in ("dbt_transform", "service_read", "postgres"):
                with psycopg.connect(make_conninfo(url, user=role, password=role)) as conn:
                    assert conn.execute(sql.SQL("SELECT * FROM {} ORDER BY id").format(raw)).fetchall() == [
                        (0, "fixture"), (1, "updated")]
                    if role == "dbt_transform":
                        conn.execute(sql.SQL("CREATE TABLE {} AS SELECT * FROM {}").format(transformed, raw))
                        conn.execute(sql.SQL("CREATE TEMP TABLE batch AS SELECT * FROM {}").format(raw))
                        conn.execute(sql.SQL("DELETE FROM {} WHERE id IN (SELECT id FROM batch)").format(transformed))
                        conn.execute(sql.SQL("INSERT INTO {} SELECT * FROM batch").format(transformed))
                        assert conn.execute(sql.SQL("SELECT count(*) FROM {}").format(transformed)).fetchone()[0] == 2
                    if role == "postgres":
                        conn.execute(sql.SQL("INSERT INTO {} VALUES (3,'superuser')").format(raw))
                        conn.execute(sql.SQL("DELETE FROM {} WHERE id=3").format(raw))
                print(f"PASS {role}: Iceberg reads" + (" and transform writes" if role == "dbt_transform" else ""))
        finally:
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(transformed))
            admin.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(raw))
    print(f"PASS pg_lake {version[0]}: human metadata boundary and warehouse operations; rerun this script after extension upgrades")


if __name__ == "__main__":
    main()
