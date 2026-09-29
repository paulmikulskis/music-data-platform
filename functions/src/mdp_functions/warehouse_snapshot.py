"""Copy readable warehouse relations without service credentials or extensions."""

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import duckdb
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from mdp_functions.sandbox_policy import POLICY


def analyst_connection():
    dsn = os.environ.get("MDP_ANALYST_URL")
    if not dsn:
        raise ValueError("Set MDP_ANALYST_URL to your individual warehouse login")
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    who = conn.execute(
        "SELECT current_user AS name, rolsuper, rolcanlogin FROM pg_roles WHERE rolname=current_user"
    ).fetchone()
    if (
        who["rolsuper"]
        or not who["rolcanlogin"]
        or not who["name"].startswith(("analyst_", "explorer_"))
    ):
        conn.close()
        raise ValueError("Use an individual analyst_ or explorer_ login")
    for key in ("statement_timeout", "idle_in_transaction_session_timeout"):
        conn.execute(sql.SQL("SET {} = {}").format(sql.Identifier(key), sql.Literal(POLICY[key])))
    conn.execute("SET lock_timeout='250ms'")
    return conn


def readable(conn, relation):
    return conn.execute(
        "SELECT coalesce(has_table_privilege(to_regclass(%s),'SELECT'),false) AS ok",
        (relation,),
    ).fetchone()["ok"]


def duck_type(pg_type):
    # COPY uses PostgreSQL's text rendering; retain exact decimal/array types.
    aliases = {
        "character varying": "VARCHAR",
        "character": "VARCHAR",
        "text": "VARCHAR",
        "jsonb": "JSON",
        "bytea": "VARCHAR",
        "inet": "VARCHAR",
        "cidr": "VARCHAR",
        "USER-DEFINED": "VARCHAR",
        "oid": "UINTEGER",
        "name": "VARCHAR",
        '"char"': "VARCHAR",
    }
    # Unbounded or oversized Postgres decimals have no lossless DuckDB decimal.
    # Keep text rather than silently accepting DuckDB's default scale of three.
    if pg_type in {"numeric", "decimal"}:
        return "VARCHAR"
    decimal = re.fullmatch(r"(?:numeric|decimal)\((\d+)(?:,(-?\d+))?\)", pg_type)
    if decimal and not 0 <= int(decimal[2] or 0) <= int(decimal[1]) <= 38:
        return "VARCHAR"
    if pg_type.endswith("[]"):
        return "JSON"
    if pg_type in aliases:
        return aliases[pg_type]
    if pg_type.startswith("character"):
        return "VARCHAR"
    if re.fullmatch(
        r"(smallint|integer|bigint|boolean|real|double precision|uuid|json|date|interval|time( with(out)? time zone)?|timestamp( with(out)? time zone)?|(numeric|decimal)(\(\d+(,\d+)?\))?)",
        pg_type,
    ):
        return pg_type
    return "VARCHAR"


def snapshot(conn, out=None, parquet=None, schemas=None, *, allow_empty=False):
    if bool(out) == bool(parquet):
        raise ValueError("Choose exactly one of --out or --parquet")
    destination = Path(out or parquet).resolve()
    if destination.exists():
        raise ValueError("Output already exists; choose a new path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    relations = conn.execute(
        "SELECT n.nspname AS schema,c.relname AS name,c.relkind AS kind FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.relkind IN ('r','p','v','m','f') "
        "AND n.nspname NOT LIKE 'pg\\_%%' AND n.nspname <> 'information_schema' "
        "AND has_schema_privilege(n.oid,'USAGE') AND has_table_privilege(c.oid,'SELECT') "
        "AND (%s::text[] IS NULL OR n.nspname=ANY(%s)) ORDER BY 1,2",
        (schemas, schemas),
    ).fetchall()
    if not relations and not allow_empty:
        raise ValueError(
            "No readable relations match --schemas. Run mdp warehouse sandbox status and choose a listed schema."
        )
    # Publish only a complete artifact. A failure leaves neither a partial database
    # nor a misleading directory of half-written Parquet files.
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".mdp-snapshot-"
    ) as temporary:
        scratch = Path(temporary)
        database = duckdb.connect(
            str(scratch / "snapshot.duckdb") if out else ":memory:"
        )
        records = []
        tenants = set()
        scope_unresolved = False
        try:
            for relation in relations:
                schema, name = relation["schema"], relation["name"]
                qualified = sql.Identifier(schema, name)
                label = f"{schema}.{name}"
                csv_path = scratch / "rows.csv"
                with conn.transaction():
                    conn.execute(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    # Served tables take their lock before the first MVCC snapshot,
                    # as the data API does. Never wait behind a build's table swap.
                    if relation["kind"] in {"r", "p"}:
                        conn.execute(
                            sql.SQL("LOCK TABLE {} IN ACCESS SHARE MODE NOWAIT").format(
                                qualified
                            )
                        )
                    else:
                        conn.execute(
                            sql.SQL("SELECT * FROM {} LIMIT 0").format(qualified)
                        )
                    columns = conn.execute(
                        "SELECT attname,format_type(atttypid,atttypmod) AS type FROM pg_attribute "
                        "WHERE attrelid=to_regclass(%s) AND attnum>0 AND NOT attisdropped ORDER BY attnum",
                        (qualified.as_string(conn),),
                    ).fetchall()
                    stamp = None
                    if conn.execute(
                        "SELECT to_regprocedure('catalog.snapshot_stamp(text)') AS fn"
                    ).fetchone()["fn"]:
                        stamp = conn.execute(
                            "SELECT catalog.snapshot_stamp(%s) AS stamp",
                            (qualified.as_string(conn),),
                        ).fetchone()["stamp"]
                    elif readable(conn, "marts._build"):
                        stamp = conn.execute(
                            "SELECT * FROM marts._build WHERE relation=%s", (label,)
                        ).fetchone()
                    labels = None
                    if readable(conn, "catalog.relations"):
                        # Catalog layouts can grow without changing the snapshot format.
                        labels = conn.execute(
                            "SELECT to_jsonb(r) AS labels FROM catalog.relations r "
                            "WHERE to_jsonb(r)->>'relation'=%s OR "
                            "(coalesce(to_jsonb(r)->>'schema_name',to_jsonb(r)->>'schema')=%s "
                            "AND coalesce(to_jsonb(r)->>'relation_name',to_jsonb(r)->>'name')=%s)",
                            (label, schema, name),
                        ).fetchall()
                    at = datetime.now(timezone.utc).isoformat()
                    expressions = sql.SQL(", ").join(
                        sql.SQL("to_json({}) AS {}").format(
                            sql.Identifier(c["attname"]), sql.Identifier(c["attname"])
                        )
                        if c["type"].endswith("[]")
                        else sql.Identifier(c["attname"])
                        for c in columns
                    )
                    with (
                        csv_path.open("wb") as target,
                        conn.cursor().copy(
                            sql.SQL(
                                "COPY (SELECT {} FROM {}) TO STDOUT WITH (FORMAT CSV, HEADER, NULL '\\N')"
                            ).format(expressions, qualified)
                        ) as copy,
                    ):
                        for chunk in copy:
                            target.write(chunk)
                # The remote read transaction ends before local conversion starts.
                ident = lambda value: '"' + value.replace('"', '""') + '"'
                table = f"{ident(schema)}.{ident(name)}"
                database.execute(f"CREATE SCHEMA IF NOT EXISTS {ident(schema)}")
                types = {c["attname"]: duck_type(c["type"]) for c in columns}
                database.execute(
                    f"CREATE TABLE {table} AS SELECT * FROM read_csv(?, columns=?, header=true, nullstr='\\N', allow_quoted_nulls=false)",
                    [str(csv_path), types],
                )
                if "tenant_id" in types:
                    tenants.update(
                        str(row[0])
                        for row in database.execute(
                            f"SELECT DISTINCT tenant_id FROM {table} WHERE tenant_id IS NOT NULL LIMIT 2"
                        ).fetchall()
                    )
                if not labels:
                    scope_unresolved = True
                for item in labels or []:
                    tenant = item["labels"].get("tenant")
                    scope_unresolved |= tenant in {None, "unknown", "tenant"}
                    if (
                        tenant
                        and tenant not in {"unknown", "global", "tenant"}
                        and "tenant_id" not in types
                    ):
                        tenants.add(tenant)
                rows = database.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                records.append(
                    {
                        "relation": label,
                        "rows": rows,
                        "captured_at": at,
                        "build": stamp,
                        "labels": labels,
                        "columns": columns,
                    }
                )
                if parquet:
                    folder = (
                        scratch
                        / "parquet"
                        / quote(schema, safe="").replace("..", "%2E%2E")
                    )
                    folder.mkdir(parents=True, exist_ok=True)
                    database.execute(
                        f"COPY {table} TO ? (FORMAT PARQUET)",
                        [
                            str(
                                folder
                                / f"{quote(name, safe='').replace('..', '%2E%2E')}.parquet"
                            )
                        ],
                    )
                    database.execute(f"DROP TABLE {table}")
            manifest = json.dumps(
                {
                    "format": 1,
                    "consistency": "per relation",
                    "cross_tenant": len(tenants) > 1,
                    "scope_unresolved": scope_unresolved,
                    "relations": records,
                },
                default=str,
            )
            if out:
                database.execute("CREATE TABLE _mdp_snapshot (manifest JSON)")
                database.execute("INSERT INTO _mdp_snapshot VALUES (?)", [manifest])
            else:
                (scratch / "parquet").mkdir(exist_ok=True)
                (scratch / "parquet" / "_mdp_snapshot.json").write_text(manifest + "\n")
        finally:
            database.close()
        artifact = scratch / ("snapshot.duckdb" if out else "parquet")
        artifact.chmod(0o600 if out else 0o700)
        artifact.rename(destination)
    scope = "true" if len(tenants) > 1 else "unknown" if scope_unresolved else "false"
    return f"Snapshot: {len(records)} relations, {sum(r['rows'] for r in records)} rows → {destination} (cross-tenant={scope}). Next: inspect builds, closes and labels in _mdp_snapshot."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schemas", help="Comma-separated schemas; defaults to every readable relation"
    )
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--out")
    output.add_argument("--parquet")
    args = parser.parse_args()
    with analyst_connection() as conn:
        print(
            snapshot(
                conn,
                args.out,
                args.parquet,
                args.schemas.split(",") if args.schemas else None,
            )
        )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, psycopg.Error, duckdb.Error) as error:
        from mdp_functions.sandbox_policy import message

        raise SystemExit(
            str(error)
            if isinstance(error, ValueError)
            else f"{type(error).__name__}: {error}\n" + message("sandbox_snapshot_failed")
        ) from None
