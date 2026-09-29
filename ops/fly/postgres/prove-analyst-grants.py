#!/usr/bin/env python3
"""Local-only analyst grant and documented-query proof; each permission probe rolls back."""

import os
import re
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
DSN = os.environ["MDP_WAREHOUSE_ADMIN_URL"]


def check(conn, label, statement, expected="success", role="analyst_ro"):
    got = "success"
    try:
        conn.execute("BEGIN")
        conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        conn.execute(statement)
    except psycopg.Error as error:
        got = error.sqlstate
    finally:
        conn.execute("ROLLBACK")
    assert got == expected, f"{label}: expected {expected}, got {got}"
    print(f"PASS {label}: {got}")


def account(action, handle, success=True, explore=False):
    result = subprocess.run(
        [
            str(ROOT / f"ops/fly/postgres/analyst-{action}.sh"),
            *(["--explore"] if explore else []),
            handle,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) == success, (
        f"account {action}: unexpected exit {result.returncode}"
    )
    return result.stdout


def main():
    settings = conninfo_to_dict(DSN)
    assert settings["host"] in {"127.0.0.1", "localhost", "::1"}, "local fixture only"
    with psycopg.connect(DSN, autocommit=True) as conn:
        assert conn.execute("SHOW mdp.local_stack").fetchone()[0] == "on"
        assert conn.execute(
            "SELECT rolcanlogin FROM pg_roles WHERE rolname='analyst_ro'"
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT count(*) FROM pg_auth_members WHERE member='analyst_ro'::regrole"
        ).fetchone() == (0,)
        # Existing grants reconcile idempotently after dbt builds.
        for _ in range(2):
            conn.execute(
                (
                    ROOT / "ops/fly/postgres/boot/init/10-warehouse-grants.sql"
                ).read_text()
            )
        for schema in [
            "raw",
            "control",
            "tenant_analystproof_marts",
            "tenant_analystproof_staging",
            "tenant_analystproof_intermediate",
            "wb_analyst_proof",
            "reference",
        ]:
            conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
            )
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS {}.analyst_probe CASCADE").format(
                    sql.Identifier(schema)
                )
            )
            conn.execute(
                sql.SQL("CREATE TABLE {}.analyst_probe (id int)").format(
                    sql.Identifier(schema)
                )
            )
            check(
                conn,
                f"deny {schema} SELECT",
                f"SELECT * FROM {schema}.analyst_probe",
                "42501",
            )
        # Disable the grant event trigger so future-table reads prove DEFAULT PRIVILEGES alone.
        conn.execute("ALTER EVENT TRIGGER mdp_warehouse_grants DISABLE")
        try:
            for schema in ["marts", "intermediate", "staging"]:
                conn.execute("SET ROLE dbt_transform")
                conn.execute(f"DROP TABLE IF EXISTS {schema}.analyst_future_probe CASCADE")
                conn.execute(f"CREATE TABLE {schema}.analyst_future_probe (id int)")
                conn.execute("RESET ROLE")
                check(
                    conn,
                    f"future unreviewed table {schema}",
                    f"SELECT * FROM {schema}.analyst_future_probe",
                    "42501",
                )
                for statement in [
                    f"INSERT INTO {schema}.analyst_future_probe VALUES (1)",
                    f"UPDATE {schema}.analyst_future_probe SET id=2",
                    f"DELETE FROM {schema}.analyst_future_probe",
                    f"TRUNCATE {schema}.analyst_future_probe",
                    f"CREATE TABLE {schema}.analyst_forbidden (id int)",
                ]:
                    check(
                        conn,
                        f"deny {statement.split()[0]} {schema}",
                        statement,
                        "42501",
                    )
        finally:
            conn.execute("RESET ROLE")
            conn.execute("ALTER EVENT TRIGGER mdp_warehouse_grants ENABLE")
        check(
            conn,
            "served mart read",
            "SELECT * FROM marts.mart_shazam_chart_daily LIMIT 1",
        )
        check(conn, "deny schema create", "CREATE SCHEMA analyst_forbidden", "42501")
        check(
            conn,
            "deny temporary table",
            "CREATE TEMP TABLE analyst_forbidden (id int)",
            "42501",
        )
        check(
            conn,
            "deny public write",
            "CREATE TABLE public.analyst_forbidden (id int)",
            "42501",
        )
        conn.execute(
            "CREATE OR REPLACE FUNCTION mdp.analyst_future_probe() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$"
        )
        check(
            conn,
            "deny mdp.invoke",
            "SELECT * FROM mdp.invoke('proof','{}'::jsonb)",
            "42501",
        )
        assert conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='mdp' AND has_function_privilege('analyst_ro',p.oid,'EXECUTE')"
        ).fetchone() == (0,)
        print("PASS no EXECUTE privilege on any mdp function")
        assert conn.execute(
            "SELECT has_database_privilege('analyst_ro','control','CONNECT')"
        ).fetchone() == (False,)
        # The preceding analyst test deliberately disables table grant triggers.
        # Reconcile its synthetic tables before exercising the explorer event policy.
        conn.execute(
            (ROOT / "ops/fly/postgres/boot/init/10-warehouse-grants.sql").read_text()
        )
        from mdp_functions.explore import install

        install(conn)
        assert conn.execute(
            "SELECT rolcanlogin FROM pg_roles WHERE rolname='explorer_ro'"
        ).fetchone() == (False,)
        assert conn.execute(
            "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE m.roleid='explorer_ro'::regrole AND r.rolcanlogin"
        ).fetchone() == (0,)
        for schema in [
            "marts",
            "intermediate",
            "staging",
            "reference",
            "tenant_analystproof_marts",
            "tenant_analystproof_staging",
        ]:
            relation = schema + (
                ".analyst_future_probe"
                if schema in ["marts", "intermediate", "staging"]
                else ".analyst_probe"
            )
            check(
                conn,
                "explorer denies unreviewed " + relation,
                "SELECT * FROM " + relation,
                "42501",
                role="explorer_ro",
            )
            check(
                conn,
                "explorer reads safe copy " + relation,
                "SELECT * FROM explore_" + relation,
                role="explorer_ro",
            )
            for verb in ["DELETE FROM", "TRUNCATE"]:
                check(
                    conn,
                    "explorer denies " + verb + " " + relation,
                    verb + " " + relation,
                    "42501",
                    role="explorer_ro",
                )
        for schema in ["raw", "control", "wb_analyst_proof"]:
            check(
                conn,
                "explorer denies " + schema,
                f"SELECT * FROM {schema}.analyst_probe",
                "42501",
                role="explorer_ro",
            )
        for (name,) in conn.execute(
            "SELECT table_name FROM information_schema.views WHERE table_schema='explore_raw'"
        ):
            check(
                conn,
                "explorer raw view " + name,
                sql.SQL("SELECT * FROM explore_raw.{} LIMIT 1").format(
                    sql.Identifier(name)
                ),
                role="explorer_ro",
            )
        check(
            conn,
            "explorer catalog",
            "SELECT * FROM catalog.relations",
            role="explorer_ro",
        )
        check(
            conn,
            "explorer denies key",
            "SELECT * FROM mdp.pseudonym_key",
            "42501",
            role="explorer_ro",
        )
        assert conn.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='mdp' AND has_function_privilege('explorer_ro',p.oid,'EXECUTE')"
        ).fetchone() == (0,)
        credential = account("add", "fixture", explore=True)
        username, password = [
            line.split(": ", 1)[1] for line in credential.splitlines()[:2]
        ]
        assert username == "explorer_fixture"
        with psycopg.connect(
            make_conninfo(DSN, user=username, password=password)
        ) as login:
            assert login.execute("SHOW log_statement").fetchone() == ("none",)
            login.execute("SELECT * FROM explore_raw.playlist_snapshots LIMIT 1")
        account("remove", "fixture", explore=True)
        print(
            "PASS explore account lifecycle, normalized audit settings, safe views, tenant reads and denied writes"
        )
        # Run the exact doc blocks, excluding the DuckDB connection recipe.
        document = (
            (ROOT / "docs/analyst-access.md")
            .read_text()
            .split("## Ten starter queries")[1]
        )
        queries = re.findall(r"```sql\n(.*?)\n```", document, re.DOTALL)
        assert len(queries) == 10, "expected ten documented starter statements"
        for number, query in enumerate(queries, 1):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE analyst_ro")
                conn.execute("SET LOCAL timezone='UTC'")
                assert conn.execute("SELECT current_user").fetchone() == ("analyst_ro",)
                rows = conn.execute(query).fetchall()
            print(f"PASS starter {number:02}: role=analyst_ro rows={len(rows)}")
            print(query)
        # Exercise the actual shell entrypoints; generated credentials stay in memory.
        for handle in ["ro", "bad-handle", "x;select 1", "x" * 49]:
            assert not account("add", handle, success=False)
        credential = account("add", "fixture")
        username, password = [
            line.split(": ", 1)[1] for line in credential.splitlines()[:2]
        ]
        assert username == "analyst_fixture" and len(password) >= 40
        assert (
            f"[mdp]\nhost=127.0.0.1\nport=15472\ndbname=warehouse\nuser={username}\nsslmode=require"
            in credential
        )
        assert f"127.0.0.1:15472:warehouse:{username}:{password}" in credential
        print("PASS private operator service block and password-file line")
        assert not account("add", "fixture", success=False)
        login_dsn = make_conninfo(DSN, user=username, password=password)
        with psycopg.connect(login_dsn, autocommit=True) as login:
            assert login.execute("SELECT current_user").fetchone() == (username,)
            assert (
                login.execute(
                    "SELECT count(*) FROM marts.mart_shazam_chart_daily"
                ).fetchone()[0]
                > 0
            )
            try:
                login.execute("SELECT * FROM raw.analyst_probe")
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError("login inherited forbidden raw access")
            try:
                login.execute("SET ROLE dbt_transform")
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError("login escalated to dbt_transform")
            account("remove", "fixture")
            try:
                login.execute("SELECT 1")
            except psycopg.OperationalError:
                pass
            else:
                raise AssertionError("removed login session survived")
        try:
            psycopg.connect(login_dsn).close()
        except psycopg.OperationalError:
            pass
        else:
            raise AssertionError("removed login reconnected")
        print(
            "PASS account add, duplicate/invalid refusal, inherited read/denial, removal, session termination, reconnect denial"
        )
    print("PASS analyst grants and 10/10 starter queries")


if __name__ == "__main__":
    main()
