"""Page publication: all output manifests, registrations and cursor CAS are one unit."""

import gzip
import json
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from uuid import UUID, uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from psycopg.types.json import Jsonb

from mdp_functions.control_db import (
    Connection,
    ControlDB,
    alert,
    event,
    fence,
    open_alerts,
)
from mdp_functions.errors import ServiceError
from mdp_functions.layers import Ctx
from mdp_functions.manifest import parse_manifest
from mdp_functions.schemas import (
    COMPLETION_COLUMNS,
    GOLD_COLUMNS,
    LINEAGE,
    RUN_COMPLETION,
    RUN_COMPLETION_COLUMNS,
    SILVER_INPUT_COLUMNS,
    archive,
    declared,
    infer_type,
    merge_type,
    shape,
)
from mdp_functions.settings import Settings
from mdp_functions.store import ObjectStore


def component_columns(
    manifest: Any, rows: list[dict[str, Any]], previous: dict[str, str] | None, input_types: dict[str, str],
    declaration: Any = None,
) -> dict[str, str]:
    """An explicit output type owns a version component; otherwise use its input type or history."""
    explicit = declared(declaration) if isinstance(declaration, type) else {}
    columns = {}
    for name in manifest.input_version:
        typ = explicit.get(name, input_types.get(name))
        if typ is None:
            typ = (previous or {}).get(name, "unknown")
            for row in rows:
                typ = merge_type(typ, infer_type(row.get(name)))
        columns[name] = typ
    return columns


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, default=str, sort_keys=True).encode()


def parquet_bytes(rows: list[dict[str, Any]], columns: dict[str, str]) -> bytes:
    types = {"bigint": pa.int64(), "double": pa.float64(), "boolean": pa.bool_()}
    arrays = {}
    for key, typ in columns.items():
        values = [r.get(key) for r in rows]
        if typ not in types:
            values = [
                None
                if v is None
                else (json.dumps(v, default=str) if typ == "jsonb" else str(v))
                for v in values
            ]
        arrays[key] = pa.array(values, type=types.get(typ, pa.string()))
    buffer = BytesIO()
    pq.write_table(pa.table(arrays), buffer)
    return buffer.getvalue()


class Dumps:
    def __init__(self, db: ControlDB, store: ObjectStore, settings: Settings) -> None:
        self.db, self.store, self.settings = db, store, settings

    def publish(
        self,
        ctx: Ctx,
        run: dict[str, Any],
        batch: dict[str, Any],
        completed_targets: list[str],
        complete: bool = False,
    ) -> bool:
        with self.db.transaction() as conn:
            locked = conn.execute(
                "SELECT * FROM control.batch WHERE id=%s", (batch["id"],)
            ).fetchone()
            page = (locked["last_part_uploaded"] or 0) + 1
            outputs: list[
                tuple[str, str, list[dict[str, Any]], dict[str, str], str]
            ] = []
            for table, rows in ctx.outputs.items():
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))", ("schema:" + table,)
                )
                last = conn.execute(
                    """SELECT d.schema_fingerprint,d.files FROM control.dump d
                    JOIN control.load l ON l.dump_id=d.id WHERE d.streamline_id=%s AND l.target_table=%s
                    ORDER BY d.created_at DESC LIMIT 1""",
                    (run["streamline_id"], table),
                ).fetchone()
                previous = None
                if last and last["schema_fingerprint"]:
                    path = (
                        self.settings.schema_root
                        / ctx.manifest.source_key
                        / (last["schema_fingerprint"] + ".json")
                    )
                    if path.exists():
                        previous = json.loads(path.read_text())["columns"]
                    else:
                        key = next(
                            f["key"] for f in last["files"] if f["role"] == "manifest"
                        )
                        full = json.loads(self.store.get(key))["columns"]
                        previous = {
                            k: v
                            for k, v in full.items()
                            if k not in LINEAGE and k != "tenant_id"
                        }
                declaration = (
                    ctx.manifest.schema.get(table, "infer")
                    if isinstance(ctx.manifest.schema, dict)
                    else ctx.manifest.schema
                )
                runtime_columns = None
                if table == "raw._enrichment_completion":
                    declaration = "infer"
                    runtime_columns = COMPLETION_COLUMNS
                elif ctx.manifest.layer == "gold":
                    runtime_columns = {**GOLD_COLUMNS, **component_columns(ctx.manifest, rows, previous, ctx.input_types, declaration)}
                elif ctx.manifest.per_input:
                    runtime_columns = {**SILVER_INPUT_COLUMNS, **component_columns(ctx.manifest, rows, previous, ctx.input_types, declaration)}
                accepted, columns, rejected, drift = shape(rows, declaration, previous, runtime_columns)
                ctx.rejected.extend(rejected)
                ctx.yielded_count -= len(rejected)
                fingerprint = archive(
                    self.settings.schema_root, ctx.manifest.source_key, table, columns
                )
                acknowledged = conn.execute(
                    "SELECT %s=ANY(acknowledged_fingerprints) AS known FROM control.streamline WHERE id=%s",
                    (f"{ctx.manifest.source_key}:{table}:{fingerprint}", run["streamline_id"]),
                ).fetchone()["known"]
                if drift and not acknowledged:
                    added = sorted(set(columns) - set(previous or {}))
                    widened = sorted(k for k in columns if k in (previous or {}) and columns[k] != previous[k])
                    message = f"{table}: added {', '.join(added) or 'none'}; widened {', '.join(widened) or 'none'}"
                    event(conn, run["id"], "schema_drift", message,
                          {"table": table, "fingerprint": fingerprint, "added": added, "widened": widened}, "warning")
                    alert(conn, run["id"], "schema_drift", message)
                if accepted:
                    outputs.append(("output", table, accepted, columns, fingerprint))
            # Checkpoint delivery only after schema validation, in the page's transaction.
            # Markers survive page flushes and retries of an unfinished target.
            if ctx.target_id:
                identity = str(ctx.target_id)
                if any(kind == "output" and rows for kind, _, rows, _, _ in outputs):
                    completed_targets.append("accepted:" + identity)
                if ctx.exclusions:
                    completed_targets.append("excluded:" + identity)
                if ctx.rejected:
                    completed_targets.append("row_rejected:" + identity)
                if (identity in completed_targets and "row_rejected:" + identity in completed_targets
                        and "accepted:" + identity not in completed_targets):
                    completed_targets.append("rejected:" + identity)
                completed_targets[:] = list(dict.fromkeys(completed_targets))
            if ctx.rejected:
                columns = {"record": "jsonb", "reason": "text"}
                fingerprint = archive(
                    self.settings.schema_root,
                    ctx.manifest.source_key,
                    "raw._rejected",
                    columns,
                )
                outputs.append(
                    ("rejected", "raw._rejected", ctx.rejected, columns, fingerprint)
                )
            manifests = []
            # The output rows of this page as registered, for callers that verify their landing.
            ctx.published = {}
            stamp = datetime.now(timezone.utc)
            ids = [str(uuid4()) for _ in outputs]
            page_id = str(uuid4())
            expected = {
                k: {
                    "version": v.get("version", 0),
                    "reset_generation": v.get("reset_generation", 0),
                    "exists": True,
                }
                for k, v in ctx.cursors.items()
            }
            for dump_id, (kind, table, records, schema, fingerprint) in zip(
                ids, outputs
            ):
                seq = conn.execute(
                    "SELECT nextval('control.landed_seq') AS seq"
                ).fetchone()["seq"]
                columns = {**schema, **LINEAGE}
                if ctx.manifest.tenant_bound:
                    columns["tenant_id"] = "uuid"
                rows = [
                    {
                        **r,
                        "_run_id": str(run["id"]),
                        "_dump_id": dump_id,
                        "_landed_seq": seq,
                        "_cycle_id": str(run["cycle_id"]),
                        "_revision_id": str(run["revision_id"])
                        if run["revision_id"]
                        else None,
                        "_source_key": ctx.manifest.source_key,
                        "_ingested_at": stamp,
                        "_extra": r.get("_extra", {}),
                        **(
                            {"tenant_id": str(run["tenant_id"])}
                            if ctx.manifest.tenant_bound
                            else {}
                        ),
                    }
                    for r in records
                ]
                if kind == "output":
                    ctx.published[table] = rows
                prefix = f"dumps/{ctx.manifest.source_key}/dt={stamp.date()}/run={run['id']}/dump={dump_id}/"
                files = []

                def upload(
                    name: str,
                    role: str,
                    fmt: str,
                    data: bytes,
                    count: int,
                    prefix: str = prefix,
                    files: list[dict[str, Any]] = files,
                ) -> None:
                    self.store.put(prefix + name, data)
                    files.append(
                        {
                            "name": name,
                            "key": prefix + name,
                            "role": role,
                            "format": fmt,
                            "bytes": len(data),
                            "row_count": count,
                        }
                    )

                upload(
                    f"part-{page:04d}.parquet",
                    kind,
                    "parquet",
                    parquet_bytes(rows, columns),
                    len(rows),
                )
                if kind == "rejected":
                    upload(
                        f"rejected-{page:04d}.jsonl.gz",
                        "rejected",
                        "jsonl.gz",
                        gzip.compress(b"\n".join(json_bytes(r) for r in records)),
                        len(records),
                    )
                if ctx.manifest.keep_payload and ctx.payloads:
                    upload(
                        f"raw-{page:04d}.jsonl.gz",
                        "payload",
                        "jsonl.gz",
                        gzip.compress(b"\n".join(json_bytes(p) for p in ctx.payloads)),
                        len(ctx.payloads),
                    )
                manifests.append(
                    {
                        "id": dump_id,
                        "kind": kind,
                        "run_id": str(run["id"]),
                        "streamline_id": str(run["streamline_id"]),
                        "cycle_id": str(run["cycle_id"]),
                        "revision_id": str(run["revision_id"])
                        if run["revision_id"]
                        else None,
                        "warehouse_id": str(run["warehouse_id"]),
                        "landed_seq": seq,
                        "target_table": table,
                        "prefix": prefix,
                        "uri_prefix": self.store.uri(prefix),
                        "files": files,
                        "row_count": len(rows),
                        "bytes": sum(f["bytes"] for f in files),
                        "columns": columns,
                        "schema_fingerprint": fingerprint,
                        "function_version": self.settings.image_digest,
                        "batch_id": str(batch["id"]),
                        "lease_token": str(batch["lease_token"]),
                        "attempt_id": str(batch["attempt_id"]),
                        "page": page,
                        "page_id": page_id,
                        "complete": complete,
                        "completed_targets": completed_targets,
                        "cursor_changes": ctx.pending_cursors,
                        "cursor_expected": expected,
                        "observed": ctx.observed_count,
                        "yielded": ctx.yielded_count,
                        "rejected": len(ctx.rejected),
                        "exclusions": ctx.exclusions,
                        "accounting_version": 2,
                    }
                )
            group = [m["prefix"] + "manifest.json" for m in manifests]
            # Every part in the page exists before any manifest becomes visible.
            for manifest in manifests:
                manifest["page_manifests"] = group
                self.store.put(
                    manifest["prefix"] + "manifest.json", json_bytes(manifest)
                )
            self.register(
                conn,
                batch,
                run,
                manifests,
                ctx.pending_cursors,
                expected,
                completed_targets,
                page,
                complete,
                ctx.observed_count,
                ctx.yielded_count,
                len(ctx.rejected),
                ctx.completion,
                exclusions=ctx.exclusions,
                accounting_version=2,
            )
            open_alerts(conn, run["id"], ctx.alerts)
            draining = locked["status"] == "draining"
        for key, value in ctx.pending_cursors.items():
            old = ctx.cursors.get(key, {})
            ctx.cursors[key] = {
                "cursor_value": value,
                "version": old.get("version", 0) + 1,
                "reset_generation": old.get("reset_generation", 0),
            }
        batch["last_part_uploaded"] = page
        ctx.clear_page()
        return draining

    def publish_completion(
        self, run: dict[str, Any], attempt: dict[str, Any], row: dict[str, Any], settling: bool = False
    ) -> None:
        """Register the run's last dump, naming every other output dump it registered.

        Fenced on the run's current, live attempt: every batch is terminal, so no batch lease remains.
        When a run settles without a live attempt (`settling`), the fence is its latest attempt.
        """
        from mdp_functions.errors import LeaseLost

        columns = {**RUN_COMPLETION_COLUMNS, **LINEAGE}
        dump_id, page_id = str(uuid4()), str(uuid4())
        stamp = datetime.now(timezone.utc)
        with self.db.transaction() as conn:
            conn.execute("SELECT id FROM control.run WHERE id=%s FOR UPDATE", (run["id"],))
            current = conn.execute(
                "SELECT id,status,deadline_at>now() AS alive FROM control.run_attempt WHERE run_id=%s ORDER BY attempt_no DESC LIMIT 1",
                (run["id"],),
            ).fetchone()
            if not current or current["id"] != attempt["id"] or (
                not settling and (current["status"] != "running" or not current["alive"])
            ):
                raise LeaseLost()
            owner = conn.execute(
                "SELECT id,lease_token FROM control.batch WHERE run_id=%s ORDER BY index LIMIT 1", (run["id"],)
            ).fetchone()
            seq = conn.execute("SELECT nextval('control.landed_seq') AS seq").fetchone()["seq"]
            rows = [{
                **row,
                "_run_id": str(run["id"]), "_dump_id": dump_id, "_landed_seq": seq,
                "_cycle_id": str(run["cycle_id"]),
                "_revision_id": str(run["revision_id"]) if run["revision_id"] else None,
                "_target_id": None, "_request_id": str(run["id"]), "_source_key": row["source_key"],
                "_ingested_at": stamp, "_extra": {},
                **({"tenant_id": str(run["tenant_id"])} if run["tenant_id"] else {}),
            }]
            if run["tenant_id"]:
                columns["tenant_id"] = "uuid"
            prefix = f"dumps/{row['source_key']}/dt={stamp.date()}/run={run['id']}/dump={dump_id}/"
            data = parquet_bytes(rows, columns)
            self.store.put(prefix + "part-0001.parquet", data)
            files = [{"name": "part-0001.parquet", "key": prefix + "part-0001.parquet", "role": "output",
                      "format": "parquet", "bytes": len(data), "row_count": 1}]
            manifest = {
                "id": dump_id, "kind": "output", "run_id": str(run["id"]),
                "streamline_id": str(run["streamline_id"]), "cycle_id": str(run["cycle_id"]),
                "revision_id": str(run["revision_id"]) if run["revision_id"] else None,
                "warehouse_id": str(run["warehouse_id"]), "landed_seq": seq, "target_table": RUN_COMPLETION,
                "prefix": prefix, "uri_prefix": self.store.uri(prefix), "files": files, "row_count": 1,
                "bytes": len(data), "columns": columns,
                "schema_fingerprint": archive(self.settings.schema_root, row["source_key"], RUN_COMPLETION, RUN_COMPLETION_COLUMNS),
                "function_version": self.settings.image_digest,
                "batch_id": str(owner["id"]) if owner else page_id,
                "lease_token": str(owner["lease_token"] or page_id) if owner else page_id,
                "attempt_id": str(attempt["id"]), "page": 1, "page_id": page_id, "complete": True,
                "completed_targets": [], "cursor_changes": {}, "cursor_expected": {},
                "observed": 0, "yielded": 0, "rejected": 0, "page_manifests": [prefix + "manifest.json"],
            }
            self.store.put(prefix + "manifest.json", json_bytes(manifest))
            conn.execute(
                """INSERT INTO control.dump(id,kind,run_id,streamline_id,cycle_id,revision_id,landed_seq,uri_prefix,
                files,row_count,schema_fingerprint,function_version,published_at) VALUES (%s,'output',%s,%s,%s,%s,%s,%s,%s,1,%s,%s,now())""",
                (dump_id, run["id"], run["streamline_id"], run["cycle_id"], run["revision_id"], seq,
                 manifest["uri_prefix"],
                 Jsonb([*files, {"role": "manifest", "key": prefix + "manifest.json", "name": "manifest.json", "format": "json", "row_count": 0}]),
                 manifest["schema_fingerprint"], manifest["function_version"]),
            )
            conn.execute(
                "INSERT INTO control.load(dump_id,warehouse_id,target_table,op,generation,status) VALUES (%s,%s,%s,'load',1,'pending')",
                (dump_id, run["warehouse_id"], RUN_COMPLETION),
            )
            event(conn, run["id"], "run_completion_written", "Run completion names every registered output dump",
                  {"dump_id": dump_id, "outputs": row["outputs"]})

    def preserve_failure(
        self, ctx: Ctx, run: dict[str, Any], batch: dict[str, Any], exc: ServiceError
    ) -> str:
        key = f"failures/{run['id']}/{batch['id']}/{uuid4()}.json"
        self.store.put(
            key,
            json_bytes(
                {
                    "payloads": ctx.payloads,
                    "outputs": ctx.outputs,
                    "rejected_records": ctx.rejected,
                    "observed": ctx.observed_count,
                    "yielded": ctx.yielded_count,
                    "rejected": len(ctx.rejected),
                    "error_class": exc.error_class,
                }
            ),
        )
        with self.db.transaction() as conn:
            fence(conn, batch)
            event(
                conn,
                run["id"],
                "page_published",
                "Failed page preserved for diagnosis",
                {
                    "observed": ctx.observed_count,
                    "yielded": 0,
                    "rejected": max(
                        ctx.observed_count - sum(ctx.exclusions.values()), ctx.yielded_count + len(ctx.rejected)
                    ),
                    "exclusions": ctx.exclusions,
                    "accounting_version": 2,
                    "payload_ref": self.store.uri(key),
                    "batch_id": str(batch["id"]),
                },
            )
            conn.execute(
                "UPDATE control.run SET rows_rejected=rows_rejected+%s WHERE id=%s",
                (
                    max(ctx.observed_count - sum(ctx.exclusions.values()), ctx.yielded_count + len(ctx.rejected)),
                    run["id"],
                ),
            )
        ctx.clear_page()
        return self.store.uri(key)

    def register(
        self,
        conn: Connection,
        batch: dict[str, Any],
        run: dict[str, Any],
        manifests: list[dict[str, Any]],
        changes: dict[str, Any],
        expected: dict[str, Any],
        completed_targets: list[str],
        page: int,
        complete: bool,
        observed: int,
        yielded: int,
        rejected: int,
        completion: dict[str, Any] | None = None,
        exclusions: dict[str, int] | None = None,
        accounting_version: int = 1,
    ) -> None:
        locked = fence(conn, batch)
        if page <= (locked["last_part_uploaded"] or 0):
            return
        for manifest in manifests:
            files = [
                *manifest["files"],
                {
                    "role": "manifest",
                    "key": manifest["prefix"] + "manifest.json",
                    "name": "manifest.json",
                    "format": "json",
                    "row_count": 0,
                },
            ]
            conn.execute(
                """INSERT INTO control.dump(id,kind,run_id,streamline_id,cycle_id,revision_id,
                landed_seq,uri_prefix,files,row_count,schema_fingerprint,function_version,lease_token,published_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) ON CONFLICT(id) DO NOTHING""",
                (
                    manifest["id"],
                    manifest["kind"],
                    run["id"],
                    run["streamline_id"],
                    run["cycle_id"],
                    run["revision_id"],
                    manifest["landed_seq"],
                    manifest["uri_prefix"],
                    Jsonb(files),
                    manifest["row_count"],
                    manifest["schema_fingerprint"],
                    manifest["function_version"],
                    batch["lease_token"],
                ),
            )
            conn.execute(
                """INSERT INTO control.load(dump_id,warehouse_id,target_table,op,generation,status)
                VALUES (%s,%s,%s,'load',1,'pending') ON CONFLICT DO NOTHING""",
                (manifest["id"], run["warehouse_id"], manifest["target_table"]),
            )
        dump_ids = [UUID(m["id"]) for m in manifests]
        cursor_key = "backfill:" + str(run["id"]) if run["kind"] == "backfill" else "default"
        for target, value in changes.items():
            target_id = target or None
            current = conn.execute(
                """SELECT * FROM control.cursor WHERE streamline_id=%s
                AND target_id IS NOT DISTINCT FROM %s::uuid AND cursor_key=%s FOR UPDATE""",
                (run["streamline_id"], target_id, cursor_key),
            ).fetchone()
            before = expected.get(
                target, {"version": 0, "reset_generation": 0, "exists": False}
            )
            if current:
                if not before["exists"] or (
                    current["version"],
                    current["reset_generation"],
                ) != (before["version"], before["reset_generation"]):
                    raise ServiceError(
                        "cursor_conflict",
                        "Cursor advanced or was reset while page was in flight",
                    )
                conn.execute(
                    """UPDATE control.cursor SET cursor_value=%s,version=version+1,dump_id=%s,updated_at=now()
                    WHERE streamline_id=%s AND target_id IS NOT DISTINCT FROM %s::uuid AND cursor_key=%s
                    AND version=%s AND reset_generation=%s""",
                    (
                        Jsonb(value),
                        dump_ids[0] if dump_ids else None,
                        run["streamline_id"],
                        target_id,
                        cursor_key,
                        before["version"],
                        before["reset_generation"],
                    ),
                )
            else:
                if before["exists"]:
                    raise ServiceError(
                        "cursor_conflict", "Cursor disappeared while page was in flight"
                    )
                inserted = conn.execute(
                    """INSERT INTO control.cursor(streamline_id,target_id,cursor_key,cursor_value,version,dump_id)
                    VALUES (%s,%s,%s,%s,1,%s) ON CONFLICT DO NOTHING RETURNING version""",
                    (
                        run["streamline_id"],
                        target_id,
                        cursor_key,
                        Jsonb(value),
                        dump_ids[0] if dump_ids else None,
                    ),
                ).fetchone()
                if not inserted:
                    raise ServiceError(
                        "cursor_conflict", "Concurrent first cursor publication"
                    )
        checkpoint = {"completed_targets": completed_targets, "complete": complete}
        conn.execute(
            """UPDATE control.batch SET dump_ids=dump_ids||%s::uuid[],dump_id=coalesce(%s,dump_id),
            last_part_uploaded=%s,cursor_checkpoint=%s WHERE id=%s""",
            (
                dump_ids,
                dump_ids[0] if dump_ids else None,
                page,
                Jsonb(checkpoint),
                batch["id"],
            ),
        )
        event(
            conn,
            run["id"],
            "page_published",
            "Page dumps and cursor committed together",
            {
                "batch_id": str(batch["id"]),
                "page": page,
                "observed": observed,
                "yielded": yielded,
                "rejected": rejected,
                "exclusions": exclusions or {},
                "accounting_version": accounting_version,
                **({"completion": completion} if completion else {}),
            },
        )
        conn.execute(
            "UPDATE control.run SET rows_rejected=rows_rejected+%s WHERE id=%s",
            (rejected - (sum((exclusions or {}).values()) if accounting_version < 2 else 0), run["id"]),
        )

    def recover_manifest(self, key: str) -> None:
        manifest = parse_manifest(self.store.get(key))
        if self.db.one("SELECT id FROM control.dump WHERE id=%s", (manifest["id"],)):
            return
        group = [parse_manifest(self.store.get(k)) for k in manifest["page_manifests"]]
        run = self.db.one(
            "SELECT * FROM control.run WHERE id=%s", (manifest["run_id"],)
        )
        if not run:
            raise ServiceError(
                "dump_late", "Manifest run is absent from this control catalog"
            )
        batch = {
            "id": UUID(manifest["batch_id"]),
            "lease_token": UUID(manifest["lease_token"]),
            "attempt_id": UUID(manifest["attempt_id"]),
        }
        with self.db.transaction() as conn:
            self.register(
                conn,
                batch,
                run,
                group,
                manifest["cursor_changes"],
                manifest["cursor_expected"],
                manifest["completed_targets"],
                manifest["page"],
                manifest["complete"],
                manifest["observed"],
                manifest["yielded"],
                manifest["rejected"],
                exclusions=manifest.get("exclusions", {}),
                accounting_version=manifest.get("accounting_version", 1),
            )
