"""Tests for workbench query boundaries."""


import psycopg
import pytest
from mdp_functions.settings import Settings
from mdp_functions.workbench import Workbench, diff_rows
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def test_workbench_database_roles_and_bounded_query(catalog_databases, tmp_path):
    databases = catalog_databases
    wb_url = conninfo_to_dict(databases["warehouse_url"])
    wb_url.update(user="workbench_wh", password="workbench_wh")
    wb = Workbench(
        Settings(
            control_url=databases["control_url"],
            control_rt_url=databases["admin_control"],
            workbench_wh_url=make_conninfo(**wb_url),
            workbench_admin_url=databases["admin_warehouse"],
            dump_root=tmp_path.as_uri(),
            workbench_row_cap=3,
        )
    )
    sessions = []
    try:
        for _ in range(2):
            session = wb.create_session("fixture")
            sessions.append(wb.session(session["sessionId"], "fixture"))
        first, second = sessions
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            conn.execute(
                sql.SQL("SET LOCAL ROLE {}").format(
                    sql.Identifier(second["scratch_schema"])
                )
            )
            conn.execute(
                sql.SQL("CREATE TABLE {}.private(value int)").format(
                    sql.Identifier(second["scratch_schema"])
                )
            )
            conn.execute(
                sql.SQL("INSERT INTO {}.private VALUES (42)").format(
                    sql.Identifier(second["scratch_schema"])
                )
            )
        for role in [None, first["scratch_schema"]]:
            escaped = second["scratch_schema"].replace("w", r"\0077", 1)
            for statement in [
                f'SELECT * FROM "{second["scratch_schema"]}".private',
                f'SELECT * FROM U&"{escaped}".private',
                f"SELECT query_to_xml('select * from ' || '{second['scratch_schema']}' || '.private',true,false,'')",
            ]:
                with psycopg.connect(make_conninfo(**wb_url)) as conn:
                    if role:
                        conn.execute(
                            sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role))
                        )
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        conn.execute(statement)
                    conn.rollback()
        assert (
            len(wb.query(first, "select generate_series(1,1000000) as n")["rows"]) == 3
        )
        schema = first["scratch_schema"]
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(schema)))
            for name in ["origin_a", "origin_b"]:
                conn.execute(
                    sql.SQL("CREATE TABLE {}.{} (id int NOT NULL)").format(
                        sql.Identifier(schema), sql.Identifier(name)
                    )
                )
                conn.execute(
                    sql.SQL("INSERT INTO {}.{} VALUES (1)").format(
                        sql.Identifier(schema), sql.Identifier(name)
                    )
                )
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {}.materialized (left_id int, right_id int, derived int)"
                ).format(sql.Identifier(schema))
            )
            conn.execute(
                sql.SQL("INSERT INTO {}.materialized VALUES (1,1,2)").format(
                    sql.Identifier(schema)
                )
            )
        lineage = wb.query(
            first,
            f'SELECT * FROM "{schema}".materialized',
            preview=True,
            provenance_query=f'SELECT a.id AS left_id,b.id AS right_id,a.id+b.id AS derived FROM "{schema}".origin_a a JOIN "{schema}".origin_b b USING (id)',
        )
        assert [column["source"] for column in lineage["columns"]] == [
            f"{schema}.origin_a",
            f"{schema}.origin_b",
            None,
        ]
        assert lineage["rows"] == [{"left_id": 1, "right_id": 1, "derived": 2}]
        draft = wb.draft(first, "mart_new_regression", "select 1 as id")
        assert wb.draft(first) == draft
        assert (
            wb.db.one(
                "SELECT count(*) AS n FROM control.workbench_session WHERE user_id=%s",
                ("fixture",),
            )["n"]
            == 2
        )
    finally:
        with psycopg.connect(databases["admin_warehouse"]) as conn:
            for session in sessions:
                role = sql.Identifier(session["scratch_schema"])
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(role))
                conn.execute(sql.SQL("DROP OWNED BY {}").format(role))
                conn.execute(sql.SQL("DROP ROLE {}").format(role))
        wb.db.close()
        wb.owner.close()


def test_business_diff_ignores_build_lineage():
    assert diff_rows(
        [{"id": 1, "value": 2, "_built_by": "a", "_cycle_id": "a"}],
        [{"id": 1, "value": 2, "_built_by": "b", "_cycle_id": "b"}],
        ["id"],
    ) == ([], [], [])
