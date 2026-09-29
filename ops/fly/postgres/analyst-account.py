#!/usr/bin/env python3
"""Individual warehouse logins. Run through analyst-add.sh / analyst-remove.sh."""

import os
import secrets

import psycopg
from mdp_functions.sandbox import create_sandbox
from psycopg import sql


def main():
    import argparse
    from pathlib import Path

    from mdp_functions.sandbox import freeze, status
    from mdp_functions.sandbox_accounts import archive, disable, ordinary, rotate
    from mdp_functions.sandbox_policy import message, name

    parser = argparse.ArgumentParser(
        description="Manage an individual warehouse login. Next: choose add, rotate, disable, freeze, unfreeze, archive or remove."
    )
    parser.add_argument(
        "action",
        choices=["add", "rotate", "disable", "freeze", "unfreeze", "archive", "remove"],
    )
    parser.add_argument("handle")
    parser.add_argument("--explore", action="store_true")
    parser.add_argument("--transfer")
    args = parser.parse_args()
    name(args.handle)
    group = "explorer_ro" if args.explore else "analyst_ro"
    role = ("explorer_" if args.explore else "analyst_") + args.handle
    dsn = os.environ.get("MDP_WAREHOUSE_ADMIN_URL")
    if not dsn:
        raise ValueError(
            "The administrator connection is missing. Set MDP_WAREHOUSE_ADMIN_URL to the warehouse, then rerun this command."
        )
    with psycopg.connect(dsn) as conn:
        if conn.execute("SELECT current_database()").fetchone()[0] != "warehouse":
            raise ValueError(
                "The connection selects another database. Set MDP_WAREHOUSE_ADMIN_URL to warehouse, then rerun."
            )
        password = None
        if args.action == "add":
            password = secrets.token_urlsafe(32)
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
            conn.execute(
                sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(
                    sql.Identifier(group), sql.Identifier(role)
                )
            )
            for key, value in [
                ("timezone", "UTC"),
                ("log_statement", "none"),
                ("log_parameter_max_length", "0"),
            ]:
                conn.execute(
                    sql.SQL("ALTER ROLE {} SET {} = {}").format(
                        sql.Identifier(role), sql.Identifier(key), sql.Literal(value)
                    )
                )
            conn.execute(sql.SQL("ALTER ROLE {} SET search_path = marts, intermediate, staging").format(sql.Identifier(role)))
            create_sandbox(conn, args.handle, role)
        else:
            ordinary(conn, role)
            owned = [row for row in status(conn) if row["owner_role"] == role]
            if args.action == "rotate":
                password = rotate(conn, role)
            elif args.action == "disable":
                disable(conn, role)
            elif args.action in ("freeze", "unfreeze"):
                for row in owned:
                    freeze(conn, row["schema_name"], args.action == "freeze")
            elif args.action == "archive":
                if not owned:
                    raise ValueError(message("sandbox_missing"))
                for row in owned:
                    print(
                        archive(
                            conn,
                            row["schema_name"],
                            Path(__file__).resolve().parents[3],
                            args.transfer,
                        )
                    )
            else:
                disable(conn, role)
                for row in owned:
                    if row["object_count"]:
                        raise ValueError(
                            "The login is disabled and its sandbox still holds objects. Run analyst-account.py archive "
                            + args.handle
                            + (" --explore" if args.explore else "")
                            + " before remove."
                        )
                    conn.execute(
                        sql.SQL("DROP SCHEMA {} RESTRICT").format(
                            sql.Identifier(row["schema_name"])
                        )
                    )
                    conn.execute(
                        "DELETE FROM mdp.sandbox_state WHERE schema_name=%s",
                        (row["schema_name"],),
                    )
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
    if password:
        print(f"username: {role}\npassword: {password}")
        print(
            f"[mdp]\nhost=127.0.0.1\nport=15472\ndbname=warehouse\nuser={role}\nsslmode=require"
        )
        print(f"127.0.0.1:15472:warehouse:{role}:{password}")
        print(
            "Deliver the service and password-file entries privately; update ~/.pgpass and reconnect. Next: mdp warehouse sandbox status."
        )
    else:
        print(
            f"{args.action} completed for {role}. Next: run mdp warehouse sandbox status --all as operator and review any dependents."
        )


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except psycopg.Error as error:
        raise SystemExit(
            f"Account operation failed (SQLSTATE {error.sqlstate or 'connection'}); no credential printed. Inspect the login with psql \\du <login>, check sandbox status, then rerun."
        ) from None
