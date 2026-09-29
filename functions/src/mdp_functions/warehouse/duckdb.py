"""Single-process development warehouse; protocol and receipt keys match Postgres."""

import json
import threading
from io import BytesIO
from typing import Any

import duckdb
import pyarrow.parquet as pq
from mdp_functions import owners
from mdp_functions.schemas import SQL_TYPES
from mdp_functions.warehouse.base import Hook
from mdp_functions.warehouse.postgres import MIRRORS

LOCK = threading.RLock()


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def observed_column(conn, table: str) -> str:
    """The landed observed owner class of `table`, or `null` where the table predates it."""
    found = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema='raw' AND table_name=? "
        "AND column_name='owner_class_observed'",
        [table.removeprefix("raw.")],
    ).fetchone()
    return "owner_class_observed" if found else "null::text"


def relation(table: str) -> str:
    if not table.startswith("raw."):
        raise ValueError("Warehouse writes must be under raw")
    return ".".join(quoted(x) for x in table.split("."))


class DuckDBWarehouse:
    def __init__(self, path: str) -> None:
        self.path = path

    def health(self) -> None:
        with LOCK, duckdb.connect(self.path) as conn:
            conn.execute("SELECT 1")

    def ensure(self, table: str, columns: dict[str, str]) -> None:
        with LOCK, duckdb.connect(self.path) as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
            conn.execute("""CREATE TABLE IF NOT EXISTS raw._load_receipts (
                dump_id uuid, warehouse_id uuid, target_table varchar, generation integer,
                claim_token uuid, rows bigint, committed_at timestamptz,
                PRIMARY KEY(dump_id,warehouse_id,target_table))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS raw._fixture_receipts (
                source_key varchar,run_id varchar,status varchar,coverage varchar,rows_written bigint,
                rows_rejected bigint,dump_id varchar,landed_seq bigint,trace_url varchar,message varchar)""")
            defs = ",".join(
                f"{quoted(k)} {SQL_TYPES[v].replace('jsonb', 'json')}"
                for k, v in columns.items()
            )
            conn.execute(f"CREATE TABLE IF NOT EXISTS {relation(table)} ({defs})")
            old = {
                r[0]: r[1]
                for r in conn.execute(f"DESCRIBE {relation(table)}").fetchall()
            }
            for key, typ in columns.items():
                sql_type = SQL_TYPES[typ].replace("jsonb", "json")
                if key not in old:
                    conn.execute(
                        f"ALTER TABLE {relation(table)} ADD COLUMN {quoted(key)} {sql_type}"
                    )
                elif (old[key] == "VARCHAR" and typ not in ("unknown", "text")) or (
                    old[key] == "BIGINT" and typ == "double"
                ):
                    conn.execute(
                        f"ALTER TABLE {relation(table)} ALTER {quoted(key)} TYPE {sql_type}"
                    )

    def land(
        self,
        manifest: dict[str, Any],
        claim: dict[str, Any],
        parts: list[bytes],
        hook: Hook | None = None,
    ) -> int:
        key = (manifest["id"], str(claim["warehouse_id"]), manifest["target_table"])
        with LOCK, duckdb.connect(self.path) as conn:
            conn.execute("BEGIN")
            if hook:
                hook("before_fence", conn)
            acquired = conn.execute(
                "INSERT INTO raw._load_receipts VALUES (?,?,?,?,?,NULL,NULL) "
                "ON CONFLICT(dump_id,warehouse_id,target_table) DO UPDATE SET "
                "generation=excluded.generation,claim_token=excluded.claim_token,committed_at=NULL "
                "WHERE raw._load_receipts.generation < excluded.generation RETURNING generation",
                (*key, claim["generation"], str(claim["claim_token"])),
            ).fetchone()
            if acquired is None:
                receipt = conn.execute(
                    "SELECT rows FROM raw._load_receipts "
                    "WHERE dump_id=? AND warehouse_id=? AND target_table=? AND committed_at IS NOT NULL",
                    key,
                ).fetchone()
                conn.execute("ROLLBACK")
                if receipt is None:
                    raise RuntimeError("Receipt fence returned no committed receipt")
                return receipt[0]
            if hook:
                hook("after_fence", conn)
            if claim["op"] == "repair":
                conn.execute(
                    f"DELETE FROM {relation(key[2])} WHERE _dump_id=?", [key[0]]
                )
            count = 0
            names = ",".join(quoted(k) for k in manifest["columns"])
            for part in parts:
                for batch in pq.ParquetFile(BytesIO(part)).iter_batches():
                    conn.register("_landing_part", batch)
                    conn.execute(
                        f"INSERT INTO {relation(key[2])} ({names}) SELECT {names} FROM _landing_part"
                    )
                    count += batch.num_rows
                    conn.unregister("_landing_part")
            if key[2] in owners.NAMED_TABLES:
                recorded = observed_column(conn, key[2])
                conn.execute(owners.landing_redaction(key[2], "duckdb", recorded), [key[0]])
            conn.execute(
                "UPDATE raw._load_receipts SET rows=?,committed_at=now() "
                "WHERE dump_id=? AND warehouse_id=? AND target_table=? AND claim_token=?",
                (count, *key, str(claim["claim_token"])),
            )
            if hook:
                hook("before_commit", conn)
            conn.execute("COMMIT")
        if hook:
            hook("after_commit", None)
        return count

    def receipts(self, dump_ids: list[Any] | None = None) -> list[dict[str, Any]]:
        with LOCK, duckdb.connect(self.path) as conn:
            if not conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='raw' AND table_name='_load_receipts'"
            ).fetchone():
                return []
            cursor = (
                conn.execute("SELECT * FROM raw._load_receipts WHERE committed_at IS NOT NULL")
                if dump_ids is None
                else conn.execute(
                    "SELECT * FROM raw._load_receipts WHERE committed_at IS NOT NULL AND list_contains(?, dump_id::varchar)",
                    [[str(d) for d in dump_ids]],
                )
            )
            return [
                dict(zip([c[0] for c in cursor.description], row))
                for row in cursor.fetchall()
            ]

    def stripped_cycles(self) -> list[str]:
        with LOCK, duckdb.connect(self.path) as conn:
            columns = {
                r[0] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name='cycles'"
                ).fetchall()
            }
            if not columns:
                return []
            where = " WHERE manifest_mode IS NULL" if "manifest_mode" in columns else ""
            return [str(r[0]) for r in conn.execute("SELECT id FROM raw.cycles" + where).fetchall()]

    def mirror(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        for name in tables:
            self.ensure("raw." + name, MIRRORS[name][0])
        with LOCK, duckdb.connect(self.path) as conn:
            conn.execute("BEGIN")
            for name, rows in sorted(tables.items()):
                columns, keys = MIRRORS[name]
                for row in rows:
                    where = " AND ".join(f"{quoted(k)}=?" for k in keys)
                    # Revisions are write-once, and a cycle status never goes back to open.
                    if (
                        name == "targets"
                        and conn.execute(
                            f"SELECT 1 FROM raw.{quoted(name)} WHERE {where}",
                            [str(row[k]) for k in keys],
                        ).fetchone()
                    ) or (
                        name == "cycles"
                        and row["status"] == "open"
                        and conn.execute(
                            "SELECT 1 FROM raw.cycles WHERE id=? AND status<>'open'", [str(row["id"])]
                        ).fetchone()
                    ):
                        continue
                    conn.execute(
                        f"DELETE FROM raw.{quoted(name)} WHERE {where}",
                        [str(row[k]) for k in keys],
                    )
                    conn.execute(
                        f"INSERT INTO raw.{quoted(name)} VALUES ({','.join('?' for _ in columns)})",
                        [
                            None if row.get(k) is None
                            else json.dumps(row[k], default=str) if isinstance(row[k], (dict, list))
                            else str(row[k])
                            for k in columns
                        ],
                    )
            conn.execute("COMMIT")

    def fixture_receipts(self, source: str, receipts: list[dict[str, Any]]) -> None:
        with LOCK, duckdb.connect(self.path) as conn:
            conn.execute(
                "DELETE FROM raw._fixture_receipts WHERE source_key=?", [source]
            )
            for receipt in receipts:
                conn.execute(
                    "INSERT INTO raw._fixture_receipts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [
                        source,
                        *[
                            receipt[k]
                            for k in (
                                "run_id",
                                "status",
                                "coverage",
                                "rows_written",
                                "rows_rejected",
                                "dump_id",
                                "landed_seq",
                                "trace_url",
                                "message",
                            )
                        ],
                    ],
                )
