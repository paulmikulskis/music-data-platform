"""Operator lifecycle for individual logins and their archived work."""

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
from psycopg import Error, sql
from psycopg.rows import dict_row

from mdp_functions.sandbox import create_sandbox, notice, status
from mdp_functions.sandbox_policy import POLICY, message


def ordinary(conn, role):
    row = conn.execute(
        "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname=%s",
        (role,),
    ).fetchone()
    memberships = conn.execute(
        "SELECT parent.rolname FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member WHERE child.rolname=%s ORDER BY 1",
        (role,),
    ).fetchall()
    expected = "explorer_ro" if role.startswith("explorer_") else "analyst_ro"
    if (
        row is None
        or row[0]
        or memberships != [(expected,)]
        or not role.startswith(("analyst_", "explorer_"))
    ):
        raise ValueError(
            "Memberships: "
            + ", ".join(str(r[0]) for r in memberships)
            + ". "
            + message("sandbox_account_refused")
        )


def disable(conn, role):
    ordinary(conn, role)
    conn.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(role)))
    conn.commit()  # Reconnects must fail before the old sessions are terminated.
    conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s AND pid<>pg_backend_pid()",
        (role,),
    )


def rotate(conn, role):
    ordinary(conn, role)
    password = secrets.token_urlsafe(32)
    conn.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)
        )
    )
    conn.commit()
    conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s AND pid<>pg_backend_pid()",
        (role,),
    )
    return password


def archive(conn, schema, root, transfer=None):
    try:
        return _archive(conn, schema, root, transfer)
    except (Error, duckdb.Error, OSError) as error:
        raise ValueError(
            f"{type(error).__name__}: {error}\n" + message("sandbox_archive_failed")
        ) from None


def _archive(conn, schema, root, transfer=None):
    from mdp_functions.warehouse_snapshot import snapshot

    rows = status(conn, schema)
    if not rows:
        raise ValueError(message("sandbox_missing"))
    info = rows[0]
    if transfer:
        ordinary(conn, transfer)
        if (
            info["is_explorer"]
            and not conn.execute(
                "SELECT pg_has_role(%s,'explorer_ro','MEMBER')", (transfer,)
            ).fetchone()[0]
        ):
            raise ValueError(message("sandbox_acl_refused"))
    disable(conn, info["owner_role"])
    destination = (
        Path(root).resolve()
        / POLICY["archive_root"]
        / schema
        / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        )
    )
    conn.commit()
    conn.autocommit = True
    original_factory = conn.row_factory
    try:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(info["owner_role"])))
        conn.execute(
            sql.SQL("SET statement_timeout = {}").format(
                sql.Literal(POLICY["statement_timeout"])
            )
        )
        conn.row_factory = dict_row
        snapshot(conn, parquet=destination, schemas=[schema], allow_empty=True)
    finally:
        conn.execute("RESET ROLE")
        conn.row_factory = original_factory
    # Metadata is written only beside a complete snapshot. No credentials enter it.
    manifest = destination / "archive.json"
    manifest.write_text(json.dumps(info, default=str, indent=2) + "\n")
    manifest.chmod(0o600)
    with conn.transaction():
        conn.execute(
            "UPDATE mdp.sandbox_state SET archive_path=%s WHERE schema_name=%s",
            (str(destination), schema),
        )
        for dependent in info["dependents"]:
            dependent_schema = (
                dependent.get("schema_name") or dependent["relation"].split(".")[0]
            )
            notice(
                conn,
                dependent_schema,
                "sandbox_archive_blocked",
                f"{schema} is archived for offboarding. Copy or update {dependent['relation']} before the operator removes its input.",
            )
    if (
        any(not d["relation"].startswith(schema + ".") for d in info["dependents"])
        and not transfer
    ):
        raise ValueError(
            f"Archive saved at {destination}. " + message("sandbox_archive_blocked")
        )
    with conn.transaction():
        if transfer:
            for obj in info["objects"]:
                kind = {
                    "v": "VIEW",
                    "m": "MATERIALIZED VIEW",
                    "f": "FOREIGN TABLE",
                }.get(obj["kind"], "TABLE")
                conn.execute(
                    sql.SQL("ALTER {} {} OWNER TO {}").format(
                        sql.SQL(kind),
                        sql.Identifier(schema, obj["name"]),
                        sql.Identifier(transfer),
                    )
                )
            # Routines, standalone sequences and user types also belong to the login.
            for kind, identity in conn.execute(
                """SELECT CASE prokind WHEN 'a' THEN 'AGGREGATE' ELSE 'ROUTINE' END,
                    format('%%I.%%I(%%s)',n.nspname,p.proname,pg_get_function_identity_arguments(p.oid))
                  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s
                  UNION ALL SELECT 'SEQUENCE',format('%%I.%%I',n.nspname,c.relname)
                  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='S'
                  UNION ALL SELECT CASE typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END,format('%%I.%%I',n.nspname,t.typname)
                  FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                  WHERE n.nspname=%s AND t.typrelid=0 AND t.typelem=0 AND t.typtype IN ('d','e','r','b')""",
                (schema, schema, schema),
            ).fetchall():
                conn.execute(
                    sql.SQL("ALTER {} {} OWNER TO {}").format(
                        sql.SQL(kind), sql.SQL(identity), sql.Identifier(transfer)
                    )
                )
            conn.execute(
                sql.SQL("ALTER SCHEMA {} OWNER TO {}").format(
                    sql.Identifier(schema), sql.Identifier(transfer)
                )
            )
            conn.execute(
                "DELETE FROM mdp.sandbox_state WHERE schema_name=%s", (schema,)
            )
            create_sandbox(conn, schema.removeprefix(POLICY["prefix"]), transfer)
        else:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
            conn.execute(
                "DELETE FROM mdp.sandbox_state WHERE schema_name=%s", (schema,)
            )
    return (
        f"Archive saved at {destination}; "
        + ("ownership transferred. " if transfer else "sandbox removed. ")
        + "Open archive.json, tell the listed owners, then remove the disabled login when no work remains."
    )
