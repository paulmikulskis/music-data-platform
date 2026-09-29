"""Postgres heap adapter. Iceberg URL COPY can replace stream_parts at this seam."""

import csv
import json
import re
from io import BytesIO, StringIO
from typing import Any

import psycopg
import pyarrow.parquet as pq
from mdp_functions import owners
from mdp_functions.schemas import LINEAGE, SQL_TYPES
from mdp_functions.warehouse.base import Hook
from psycopg import sql
from psycopg.rows import dict_row

MIRRORS = {
    "streamlines": (
        {
            "source_key": "text",
            "enabled": "boolean",
            "allow_partial": "boolean",
            "timeout_s": "bigint",
            "batch_size": "bigint",
            "max_concurrency": "bigint",
            "storage": "text",
            "updated_at": "timestamptz",
        },
        ["source_key"],
    ),
    "cycles": (
        {
            "id": "uuid",
            "cadence": "text",
            "scope": "text",
            "opened_at": "timestamptz",
            "opened_by_dbt_run_id": "text",
            "closed_at": "timestamptz",
            "status": "text",
            "git_sha": "text",
            "image_digest": "text",
            "manifest_mode": "text",
            "close_no": "bigint",
            "global_close_no": "bigint",
            # A tenant cycle's global inputs, frozen at close; its manifest reads these, never the
            # current generated list.
            "global_inputs": "jsonb",
            # A global cycle's tenant mirrored closes at bind: scope reads count revisions by them.
            "tenant_close_nos": "jsonb",
            # A tenant cycle's timezone, frozen at bind: its local week (mdp_context().call_week).
            "timezone": "text",
        },
        ["id"],
    ),
    "dump_stamps": (
        {
            "dump_id": "uuid",
            "scope": "text",
            "close_no": "bigint",
            "source_key": "text",
            "target_table": "text",
        },
        ["dump_id"],
    ),
    "cycle_attempts": (
        {
            "dbt_run_id": "text",
            "cycle_id": "uuid",
            "bound_at": "timestamptz",
            "reason_category": "text",
            "git_sha": "text",
            "image_digest": "text",
        },
        ["dbt_run_id"],
    ),
    "cycle_inputs": (
        {
            "cycle_id": "uuid",
            "dump_id": "uuid",
            "phase": "text",
            "added_at": "timestamptz",
        },
        ["cycle_id", "dump_id"],
    ),
    "targets": (
        {
            "id": "uuid",
            "platform": "text",
            "platform_account_id": "text",
            "handle": "text",
            "display_name": "text",
            "role": "text",
            "target_set_id": "uuid",
            "resource_kind": "text",
            "canonical_key": "text",
            "params_json": "jsonb",
            "taken_at": "timestamptz",
            "_cycle_id": "uuid",
            "_revision_id": "uuid",
        },
        ["_revision_id", "id"],
    ),
}


# Manifest reads are indexed range semi-joins over the stamps of one table.
MIRROR_INDEXES = {"dump_stamps": [["scope", "target_table", "close_no"]]}
# Keyed reads of landed tables: the hourly track inputs read only the new dumps, the track keys they
# touch, and those keys' snapshots and observation groups, so an hour costs O(new dumps).
RAW_INDEXES = {
    "playlist_items": {"dump": ["_dump_id"], "track": ["platform_track_id"], "snapshot": ["snapshot_id"],
                       "group": ["observation_group"]},
    "playlist_snapshots": {"dump": ["_dump_id"], "snapshot": ["snapshot_id"], "group": ["observation_group"]},
}


def manifest_sql(cycle: str = "%s", table: str | None = None, global_tables: tuple[str, ...] = ()) -> str:
    """D6 manifest of one cycle in the warehouse, joined through raw.cycles. `list` cycles read their
    rows; `stamp` cycles add their scope's stamps through close_no and, for a tenant cycle, the
    declared global tables' stamps through global_close_no."""
    for name in (table, *global_tables):
        if name is not None and not re.fullmatch(r"raw\.[a-z_][a-z0-9_]*", name):
            raise ValueError("Manifest tables are raw relations")
    by_table = f" AND s.target_table='{table}'" if table else ""
    declared = ",".join(f"'{t}'" for t in global_tables)
    global_part = (
        f" OR (s.scope='global' AND c.scope<>'global' AND s.close_no<=c.global_close_no AND s.target_table IN ({declared}))"
        if declared
        else ""
    )
    return (
        f"SELECT dump_id FROM raw.cycle_inputs WHERE cycle_id={cycle} "
        "UNION ALL SELECT s.dump_id FROM raw.dump_stamps s JOIN raw.cycles c "
        f"ON c.id={cycle} AND c.manifest_mode='stamp' "
        f"AND ((s.scope=c.scope AND s.close_no<=c.close_no){global_part}){by_table}"
    )


def observed_column(conn, table: str) -> str:
    """The landed observed owner class of `table`, or `null` where the table predates it."""
    found = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema='raw' AND table_name=%s "
        "AND column_name='owner_class_observed'",
        (table.removeprefix("raw."),),
    ).fetchone()
    return "owner_class_observed" if found else "null::text"


def identifier(table: str) -> sql.Identifier:
    schema, name = table.split(".")
    if schema != "raw":
        raise ValueError("Warehouse writes must be under raw")
    return sql.Identifier(schema, name)


class PostgresWarehouse:
    def __init__(self, url: str) -> None:
        self.url = url

    def connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5)

    def health(self) -> None:
        with self.connect() as conn:
            conn.execute("SELECT 1")

    def ensure(self, table: str, columns: dict[str, str]) -> None:
        # DDL commits before the fenced landing transaction, serialized per table.
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (table,))
            if not conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname='raw'"
            ).fetchone():
                conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
            conn.execute("""CREATE TABLE IF NOT EXISTS raw._load_receipts (
                dump_id uuid NOT NULL, warehouse_id uuid NOT NULL, target_table text NOT NULL,
                generation integer NOT NULL, claim_token uuid NOT NULL, rows bigint,
                committed_at timestamptz, rows_deduped bigint NOT NULL DEFAULT 0, PRIMARY KEY(dump_id,warehouse_id,target_table))""")
            if not conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='raw' AND table_name='_load_receipts' AND column_name='rows_deduped'"
            ).fetchone():
                conn.execute(
                    "ALTER TABLE raw._load_receipts ADD COLUMN IF NOT EXISTS rows_deduped bigint NOT NULL DEFAULT 0"
                )
            definitions = [
                sql.SQL("{} {}").format(sql.Identifier(k), sql.SQL(SQL_TYPES[v]))
                for k, v in columns.items()
            ]
            conn.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                    identifier(table), sql.SQL(",").join(definitions)
                )
            )
            existing = {
                r["column_name"]: r["data_type"]
                for r in conn.execute(
                    "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='raw' AND table_name=%s",
                    (table.split(".")[1],),
                )
            }
            restore_explore = False
            explore_ready = conn.execute("SELECT to_regprocedure('catalog.prepare_explore_widen(regclass)') AS fn").fetchone()["fn"] is not None
            for name, typ in columns.items():
                if name not in existing:
                    conn.execute(
                        sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                            identifier(table),
                            sql.Identifier(name),
                            sql.SQL(SQL_TYPES[typ]),
                        )
                    )
                elif (existing[name] == "text" and typ not in ("unknown", "text")) or (
                    existing[name] == "bigint" and typ == "double"
                ):
                    if explore_ready and not restore_explore:
                        restore_explore = conn.execute(
                            "SELECT catalog.prepare_explore_widen(%s::regclass) AS present", (table,)
                        ).fetchone()["present"]
                    conn.execute(
                        sql.SQL(
                            "ALTER TABLE {} ALTER COLUMN {} TYPE {} USING {}::{}"
                        ).format(
                            identifier(table),
                            sql.Identifier(name),
                            sql.SQL(SQL_TYPES[typ]),
                            sql.Identifier(name),
                            sql.SQL(SQL_TYPES[typ]),
                        )
                    )
            if restore_explore:
                conn.execute("SELECT catalog.refresh_explore(%s::regclass)", (table,))
            # Retire the old cross-dump uniqueness index without deleting history.
            conn.execute(sql.SQL("DROP INDEX IF EXISTS {}.{}").format(
                sql.Identifier("raw"), sql.Identifier(table.split(".")[1] + "_enrichment_identity")
            ))
            lineage = [sql.Identifier(c) for c in LINEAGE if c in columns and c != "_extra"]
            if lineage:
                conn.execute(
                    sql.SQL("GRANT SELECT ({}) ON {} TO reader_wh").format(
                        sql.SQL(",").join(lineage), identifier(table)
                    )
                )

    def ensure_declared(self, schemas: dict[str, dict[str, str]]) -> list[str]:
        """Create every declared raw table and add its missing columns before any
        transform reads them. Additive only: an existing column keeps its type."""
        with self.connect() as conn:
            present: dict[str, set[str]] = {}
            for r in conn.execute(
                "SELECT c.relname,a.attname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped "
                "WHERE n.nspname='raw' AND c.relkind IN ('r','p','f')"
            ):
                present.setdefault(r["relname"], set()).add(r["attname"])
        changed = []
        for table, columns in sorted(schemas.items()):
            have = present.get(table.split(".")[1])
            missing = {
                k: v for k, v in columns.items() if have is None or k not in have
            }
            if missing:
                self.ensure(table, missing)
                changed.append(
                    f"{table} ({'created' if have is None else '+' + ','.join(missing)})"
                )
        with self.connect() as conn:
            for name, indexes in RAW_INDEXES.items():
                if "raw." + name not in schemas:
                    continue
                for label, columns in indexes.items():
                    conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                        sql.Identifier(f"{name}_{label}_idx"), identifier("raw." + name),
                        sql.SQL(",").join(map(sql.Identifier, columns))))
        return changed

    def land(
        self,
        manifest: dict[str, Any],
        claim: dict[str, Any],
        parts: list[bytes],
        hook: Hook | None = None,
    ) -> int:
        table = manifest["target_table"]
        key = (manifest["id"], claim["warehouse_id"], table)
        token = claim["claim_token"]
        with self.connect() as conn:
            if hook:
                hook("before_fence", conn)
            result = conn.execute(
                """INSERT INTO raw._load_receipts
                (dump_id,warehouse_id,target_table,generation,claim_token,committed_at)
                VALUES (%s,%s,%s,%s,%s,NULL)
                ON CONFLICT (dump_id,warehouse_id,target_table) DO UPDATE SET
                generation=EXCLUDED.generation,claim_token=EXCLUDED.claim_token,committed_at=NULL
                WHERE raw._load_receipts.generation < EXCLUDED.generation""",
                (*key, claim["generation"], token),
            )
            if result.rowcount == 0:
                conn.rollback()
                receipt = conn.execute(
                    "SELECT rows FROM raw._load_receipts "
                    "WHERE dump_id=%s AND warehouse_id=%s AND target_table=%s "
                    "AND committed_at IS NOT NULL",
                    key,
                ).fetchone()
                if receipt is None:
                    raise RuntimeError("Receipt fence returned no committed receipt")
                return receipt["rows"]
            if hook:
                hook("after_fence", conn)
            if claim["op"] == "repair":
                conn.execute(
                    sql.SQL("DELETE FROM {} WHERE _dump_id=%s").format(
                        identifier(table)
                    ),
                    (manifest["id"],),
                )
            count = self.stream_parts(conn, table, manifest["columns"], parts)
            if table in owners.NAMED_TABLES:
                recorded = observed_column(conn, table)
                conn.execute(owners.landing_redaction(table, "postgres", recorded), (manifest["id"],))
            conn.execute(
                "UPDATE raw._load_receipts SET rows=%s,rows_deduped=%s,committed_at=now() "
                "WHERE dump_id=%s AND warehouse_id=%s AND target_table=%s AND claim_token=%s",
                (
                    count,
                    sum(pq.ParquetFile(BytesIO(p)).metadata.num_rows for p in parts)
                    - count,
                    *key,
                    token,
                ),
            )
            if hook:
                hook("before_commit", conn)
        if hook:
            hook("after_commit", None)
        return count

    def stream_parts(
        self,
        conn: psycopg.Connection[Any],
        table: str,
        columns: dict[str, str],
        parts: list[bytes],
    ) -> int:
        count = 0
        destination = identifier(table)
        command = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT csv, NULL {})").format(
            destination,
            sql.SQL(",").join(sql.Identifier(k) for k in columns),
            sql.Literal("\\N"),
        )
        with conn.cursor().copy(command) as copy:
            for part in parts:
                for batch in pq.ParquetFile(BytesIO(part)).iter_batches(
                    batch_size=4096
                ):
                    for row in batch.to_pylist():
                        stream = StringIO()
                        # Quote non-null values, so the literal string \N stays distinguishable from NULL.
                        cells = []
                        for name in columns:
                            value = row.get(name)
                            if value is None:
                                cells.append("\\N")
                            else:
                                cell = StringIO()
                                csv.writer(
                                    cell, quoting=csv.QUOTE_ALL, lineterminator=""
                                ).writerow([value])
                                cells.append(cell.getvalue())
                        stream.write(",".join(cells) + "\n")
                        copy.write(stream.getvalue())
                        count += 1
        return count

    def mirror_rows(self) -> dict[str, list[dict[str, Any]]]:
        """Every row of every control mirror this warehouse holds, for migrate to copy."""
        tables: dict[str, list[dict[str, Any]]] = {}
        with self.connect() as conn:
            for name, (columns, _) in MIRRORS.items():
                present = {
                    r["column_name"]
                    for r in conn.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name=%s",
                        (name,),
                    )
                }
                if present:
                    tables[name] = conn.execute(
                        sql.SQL("SELECT {} FROM {}").format(
                            sql.SQL(",").join(
                                sql.Identifier(c) if c in present else sql.SQL("NULL AS {}").format(sql.Identifier(c))
                                for c in columns
                            ),
                            identifier("raw." + name),
                        )
                    ).fetchall()
        return tables

    def receipts(self, dump_ids: list[Any] | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT to_regclass('raw._load_receipts') AS name"
            ).fetchone()
            if not exists["name"]:
                return []
            if dump_ids is None:
                return conn.execute(
                    "SELECT * FROM raw._load_receipts WHERE committed_at IS NOT NULL"
                ).fetchall()
            return conn.execute(
                "SELECT * FROM raw._load_receipts WHERE committed_at IS NOT NULL AND dump_id=ANY(%s::uuid[])",
                ([str(d) for d in dump_ids],),
            ).fetchall()

    def stripped_cycles(self) -> list[str]:
        """raw.cycles rows without the stamp columns. The previous image's mirror rewrites every row
        with its own columns only."""
        with self.connect() as conn:
            columns = {
                r["column_name"] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name='cycles'"
                )
            }
            if not columns:
                return []
            where = " WHERE manifest_mode IS NULL" if "manifest_mode" in columns else ""
            return [str(r["id"]) for r in conn.execute("SELECT id FROM raw.cycles" + where)]

    def mirror(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """Upsert the given rows by key in one transaction."""
        for name in tables:
            self.ensure("raw." + name, MIRRORS[name][0])
            with self.connect() as conn:
                for index in [MIRRORS[name][1], *MIRROR_INDEXES.get(name, [])]:
                    label = name + "_" + "_".join(index) + "_idx"
                    if not conn.execute("SELECT to_regclass(%s) AS r", ("raw." + label,)).fetchone()["r"]:
                        conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                            sql.Identifier(label), identifier("raw." + name),
                            sql.SQL(",").join(map(sql.Identifier, index))))
        with self.connect() as conn:
            # One order for the per-table locks, so concurrent mirrors never deadlock.
            for name, rows in sorted(tables.items()):
                columns, keys = MIRRORS[name]
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", ("raw." + name,)
                )
                if name == "cycles" and rows:
                    # A status never goes back to open: a late state-change write cannot undo the
                    # closed row that the catch-up committed.
                    settled = {
                        str(r["id"]) for r in conn.execute(
                            "SELECT id FROM raw.cycles WHERE id=ANY(%s::uuid[]) AND status<>'open'",
                            ([str(r["id"]) for r in rows],),
                        )
                    }
                    rows = [r for r in rows if r["status"] != "open" or str(r["id"]) not in settled]
                where = sql.SQL(" AND ").join(
                    sql.SQL("{}=%s").format(sql.Identifier(k)) for k in keys
                )
                if name == "targets":
                    # Frozen revisions are write-once and land in bulk, one statement per revision.
                    revisions: dict[str, list[dict[str, Any]]] = {}
                    for row in rows:
                        revisions.setdefault(str(row["_revision_id"]), []).append(row)
                    present = {
                        str(r["_revision_id"]) for r in conn.execute(
                            "SELECT DISTINCT _revision_id FROM raw.targets WHERE _revision_id=ANY(%s::uuid[])",
                            (list(revisions),),
                        )
                    }
                    names = sql.SQL(",").join(map(sql.Identifier, columns))
                    shape = sql.SQL(",").join(
                        sql.SQL("{} {}").format(sql.Identifier(k), sql.SQL(SQL_TYPES[v])) for k, v in columns.items()
                    )
                    for revision, members in revisions.items():
                        if revision not in present:
                            conn.execute(
                                sql.SQL("INSERT INTO {} ({}) SELECT {} FROM jsonb_to_recordset(%s::jsonb) AS x({})").format(
                                    identifier("raw." + name), names, names, shape
                                ),
                                (json.dumps(members, default=str),),
                            )
                    continue
                # Serialized upserts work without adding control-owned constraints to mirrors.
                with conn.cursor() as cursor:
                    cursor.executemany(
                        sql.SQL("DELETE FROM {} WHERE {}").format(identifier("raw." + name), where),
                        [tuple(row[k] for k in keys) for row in rows],
                    )
                    cursor.executemany(
                        sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                            identifier("raw." + name),
                            sql.SQL(",").join(map(sql.Identifier, columns)),
                            sql.SQL(",").join(sql.Placeholder() for _ in columns),
                        ),
                        [
                            [
                                json.dumps(row.get(k))
                                if isinstance(row.get(k), (dict, list))
                                else row.get(k)
                                for k in columns
                            ]
                            for row in rows
                        ],
                    )
