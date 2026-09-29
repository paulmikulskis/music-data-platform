"""Appendix C: control claims plus the warehouse's generation fence."""

import gzip
import json
import zlib
from io import BytesIO
from typing import Any
from uuid import uuid4

import duckdb
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from mdp_functions.control_db import ControlDB, alert, event
from mdp_functions.cycles import mirror_derived
from mdp_functions.errors import ServiceError, error_hint
from mdp_functions.manifest import parse_manifest
from mdp_functions.schemas import declared
from mdp_functions.store import ObjectStore
from mdp_functions.warehouse.base import Hook, Warehouse
from mdp_functions.warehouse.postgres import PostgresWarehouse, identifier

PERMANENT_DATA_ERRORS = (
    ValueError,
    TypeError,
    KeyError,
    StopIteration,
    FileNotFoundError,
    zlib.error,
    UnicodeDecodeError,
    json.JSONDecodeError,
    ValidationError,
    pa.ArrowInvalid,
    pa.ArrowTypeError,
    psycopg.DataError,
    duckdb.ConversionException,
    duckdb.TypeMismatchException,
    duckdb.BinderException,
)

# These need a declaration or DDL fix. Waiting for a new claim cannot repair them.
# Connection failures, locks, cancellations and serialization conflicts remain retryable.
PERMANENT_SCHEMA_SQLSTATES = {
    "0A000",  # FeatureNotSupported, including ALTER TYPE beneath a dependent view.
    "2BP01",  # DependentObjectsStillExist.
    "42601",  # SyntaxError.
    "42703",  # UndefinedColumn.
    "42804",  # DatatypeMismatch.
    "42809",  # WrongObjectType.
    "42846",  # CannotCoerce.
    "42P16",  # InvalidTableDefinition.
}


class SilverIdentityLanding(PostgresWarehouse):
    """Reuse the receipt/generation fence with per-dump silver identities."""

    def __init__(self, warehouse: PostgresWarehouse, keys: list[str]) -> None:
        super().__init__(warehouse.url)
        self.keys = ["_dump_id", *keys]

    def ensure(self, table: str, columns: dict[str, str]) -> None:
        if any(key not in columns for key in self.keys):
            raise ValueError("Silver output is missing declared identity columns")
        super().ensure(table, columns)
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (table,))
            # Existing ambiguous rows fail closed; never invent an old snapshot.
            conn.execute(
                sql.SQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} ({}) NULLS NOT DISTINCT"
                ).format(
                    sql.Identifier(table.split(".")[1] + "_silver_identity"),
                    identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, self.keys)),
                )
            )

    def stream_parts(self, conn, table, columns, parts) -> int:
        destination = sql.Identifier("landing_" + uuid4().hex)
        names = sql.SQL(",").join(map(sql.Identifier, columns))
        conn.execute(
            sql.SQL("CREATE TEMP TABLE {} (LIKE {}) ON COMMIT DROP").format(
                destination, identifier(table)
            )
        )
        with conn.cursor().copy(
            sql.SQL("COPY {} ({}) FROM STDIN").format(destination, names)
        ) as copy:
            for part in parts:
                for batch in pq.ParquetFile(BytesIO(part)).iter_batches(
                    batch_size=4096
                ):
                    for row in batch.to_pylist():
                        if any(row.get(key) is None for key in self.keys):
                            raise ValueError("Silver output has a null identity")
                        copy.write_row(
                            [
                                Jsonb(value)
                                if isinstance(value, (dict, list))
                                else value
                                for value in (row.get(name) for name in columns)
                            ]
                        )
        return conn.execute(
            sql.SQL(
                "INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT DO NOTHING"
            ).format(identifier(table), names, names, destination)
        ).rowcount


class Landing:
    def __init__(
        self,
        db: ControlDB,
        store: ObjectStore,
        warehouse: Warehouse,
        timeout_s: float = 60,
        warehouse_id: Any = None,
    ) -> None:
        self.db, self.store, self.warehouse = db, store, warehouse
        self.timeout_s = timeout_s
        self.warehouse_id = warehouse_id

    def repair(
        self, dump_id: Any, warehouse_id: Any, target_table: str
    ) -> dict[str, Any]:
        row = self.db.one(
            """UPDATE control.load SET
            op=CASE WHEN status='claimed' THEN op ELSE 'repair' END,
            generation=CASE WHEN status='claimed' THEN generation ELSE generation+1 END,
            status=CASE WHEN status='claimed' THEN status ELSE 'pending' END,
            repair_requested=(status='claimed')
            WHERE dump_id=%s AND warehouse_id=%s AND target_table=%s
            AND status IN ('loaded','rejected','claimed') RETURNING status""",
            (dump_id, warehouse_id, target_table),
        )
        return {
            "status": row["status"] if row else "pending",
            "message": "repair queued" if row else "repair already pending",
        }

    def claim(self, load_id: Any) -> dict[str, Any] | None:
        return self.db.one(
            """UPDATE control.load SET status='claimed',claim_token=%s,
            claim_expires_at=now()+(%s * interval '1 second') WHERE id=%s
            AND (status='pending' OR (status='claimed' AND claim_expires_at<now())) RETURNING *""",
            (uuid4(), self.timeout_s, load_id),
        )

    def finish(
        self, claim: dict[str, Any], terminal: str, count: int, hook: Hook | None = None
    ) -> None:
        with self.db.transaction() as conn:
            if hook:
                hook("before_ack", conn)
            result = conn.execute(
                """UPDATE control.load SET
                status=CASE WHEN repair_requested THEN 'pending'::control.load_status ELSE %s::control.load_status END,
                op=CASE WHEN repair_requested THEN 'repair'::control.load_op ELSE op END,
                generation=CASE WHEN repair_requested THEN generation+1 ELSE generation END,
                repair_requested=false,claim_token=NULL,claim_expires_at=NULL,rows_inserted=%s,updated_at=clock_timestamp(),
                loaded_at=CASE WHEN %s='loaded' THEN now() ELSE loaded_at END
                WHERE dump_id=%s AND warehouse_id=%s AND target_table=%s AND claim_token=%s RETURNING dump_id""",
                (
                    terminal,
                    count,
                    terminal,
                    claim["dump_id"],
                    claim["warehouse_id"],
                    claim["target_table"],
                    claim["claim_token"],
                ),
            ).fetchone()
            if result:
                # A terminal rejection by the run's pinned warehouse takes the dump out of the
                # unstamped index; a repaired load that lands puts it back.
                conn.execute(
                    """UPDATE control.dump d SET rejected_at=CASE WHEN l.status='rejected'
                    THEN coalesce(d.rejected_at, now()) END
                    FROM control.load l, control.run r
                    WHERE d.id=%s AND r.id=d.run_id AND l.dump_id=d.id AND l.warehouse_id=r.warehouse_id
                    AND l.warehouse_id=%s AND l.target_table=%s""",
                    (claim["dump_id"], claim["warehouse_id"], claim["target_table"]),
                )
            if result and terminal == "loaded":
                self.derived(conn, claim["dump_id"])

    @staticmethod
    def derived(conn: Any, dump_id: Any) -> None:
        conn.execute(
            """INSERT INTO control.cycle_input(cycle_id,dump_id,phase)
            SELECT d.cycle_id,d.id,'derived' FROM control.dump d
            JOIN control.streamline s ON s.id=d.streamline_id
            JOIN control.run r ON r.id=d.run_id
            WHERE d.id=%s AND d.kind='output' AND s.layer IN ('silver','gold')
            AND NOT coalesce((r.resolved_config->>'rerun')::boolean, false)
            ON CONFLICT(cycle_id,dump_id) DO NOTHING""",
            (dump_id,),
        )

    def reconcile(self) -> None:
        # Also heals acknowledgements made by older service versions. Only unstamped derived
        # dumps can lack membership that matters: a stamp carries every later cycle.
        dumps = self.db.all(
            """SELECT d.id FROM control.dump d JOIN control.streamline s ON s.id=d.streamline_id
            WHERE d.close_no IS NULL AND d.kind='output' AND s.layer IN ('silver','gold')"""
        )
        receipts = self.warehouse.receipts([d["id"] for d in dumps]) if dumps else []
        with self.db.transaction() as conn:
            for receipt in receipts:
                self.derived(conn, receipt["dump_id"])
        mirror_derived(self.db, self.warehouse)

    def reject(
        self, claim: dict[str, Any], dump: dict[str, Any], kind: str, exc: Exception
    ) -> None:
        message = f"{kind}: dump {dump['id']}: {exc}. {error_hint(kind)['next_step']}"
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO control.dead_letter(run_id,reason,payload_ref) VALUES (%s,%s,%s)",
                (dump["run_id"], message, dump["uri_prefix"]),
            )
            alert(conn, dump["run_id"], kind, str(dump["id"]))
            conn.execute(
                "UPDATE control.run SET error_class=%s,error_message=%s WHERE id=%s AND warehouse_id=%s",
                (kind, message, dump["run_id"], claim["warehouse_id"]),
            )
        self.finish(claim, "rejected", 0)

    def verify(
        self, dump: dict[str, Any], claim: dict[str, Any]
    ) -> tuple[dict[str, Any], list[bytes]]:
        # The registered file list contains the manifest object key, independent of store URI syntax.
        manifest_key = next(f["key"] for f in dump["files"] if f["role"] == "manifest")
        manifest = parse_manifest(self.store.get(manifest_key))
        if (
            manifest["id"] != str(dump["id"])
            or manifest["target_table"] != claim["target_table"]
        ):
            raise ValueError("Manifest identity differs from registered dump")
        parts, count = [], 0
        for file in manifest["files"]:
            data = self.store.get(file["key"])
            if len(data) != file["bytes"]:
                raise ValueError("File size differs from manifest")
            try:
                if file["format"] == "jsonl.gz":
                    records = gzip.decompress(data).splitlines()
                    if len(records) != file["row_count"]:
                        raise ValueError(
                            "Compressed record count differs from manifest"
                        )
                    for record in records:
                        json.loads(record)
                if file["format"] == "parquet":
                    parquet = pq.ParquetFile(BytesIO(data))
                    if parquet.metadata.num_rows != file["row_count"]:
                        raise ValueError("Parquet row count differs from manifest")
                    if set(parquet.schema_arrow.names) != set(manifest["columns"]):
                        raise ValueError("Parquet schema differs from manifest")
                    # Decode every page during verification, before any warehouse mutation.
                    for batch in parquet.iter_batches():
                        batch.validate(full=True)
                    parts.append(data)
                    count += parquet.metadata.num_rows
            except (OSError, EOFError) as exc:
                # Object retrieval is outside this handler: only decoding is permanent.
                raise ValueError("Retained part cannot be decoded") from exc
        if count != manifest["row_count"] or count != dump["row_count"]:
            raise ValueError("Dump totals differ from manifest")
        return manifest, parts

    def process(self, load_id: Any, hook: Hook | None = None) -> bool:
        claim = self.claim(load_id)
        if not claim:
            return False
        dump = self.db.one(
            "SELECT * FROM control.dump WHERE id=%s", (claim["dump_id"],)
        )
        stage = "dump_unreadable"
        try:
            manifest, parts = self.verify(dump, claim)
            stage = "schema_breaking"
            from mdp_functions.registry import discover

            streamline = self.db.one(
                "SELECT source_key FROM control.streamline WHERE id=%s", (manifest["streamline_id"],)
            )
            source = discover().get(streamline["source_key"]) if streamline else None
            columns = dict(manifest["columns"])
            if source and source.per_input:
                model = source.schema.get(manifest["target_table"]) if isinstance(source.schema, dict) else source.schema
                explicit = declared(model) if isinstance(model, type) else {}
                # A declared text version component accepts retained typed values without changing
                # the bootstrap's text column underneath its dbt views. The dump stays immutable.
                for name in source.input_version:
                    if explicit.get(name) == "text" and name in columns:
                        columns[name] = "text"
            declaration = next(
                (
                    item
                    for item in discover().values()
                    if item.layer == "silver"
                    and item.key
                    and manifest["target_table"] in item.writes
                ),
                None,
            )
            warehouse = self.warehouse
            if declaration and isinstance(warehouse, PostgresWarehouse):
                warehouse = SilverIdentityLanding(warehouse, declaration.key)
            warehouse.ensure(manifest["target_table"], columns)
            count = warehouse.land(manifest, claim, parts, hook)
        except PERMANENT_DATA_ERRORS as exc:
            self.reject(claim, dump, stage, exc)
            return True
        except psycopg.Error as exc:
            if exc.sqlstate not in PERMANENT_SCHEMA_SQLSTATES:
                raise
            self.reject(claim, dump, "schema_breaking", exc)
            return True
        self.finish(claim, "loaded", count, hook)
        mirror_derived(self.db, self.warehouse, dump["run_id"])
        with self.db.transaction() as conn:
            event(
                conn,
                dump["run_id"],
                "dump_loaded",
                "Warehouse receipt committed",
                {"dump_id": str(dump["id"])},
            )
        return True

    def drain(self, run_id: Any | None = None) -> None:
        for _ in range(10):
            loads = self.db.all(
                """SELECT l.id FROM control.load l JOIN control.dump d ON d.id=l.dump_id
                WHERE (l.status='pending' OR (l.status='claimed' AND l.claim_expires_at<now()))
                AND (%s::uuid IS NULL OR d.run_id=%s::uuid)
                AND (%s::uuid IS NULL OR l.warehouse_id=%s::uuid)""",
                (run_id, run_id, self.warehouse_id, self.warehouse_id),
            )
            if not loads:
                return
            for load in loads:
                self.process(load["id"])
        raise ServiceError(
            "warehouse_unavailable", "Loads remain pending after repeated repairs"
        )
