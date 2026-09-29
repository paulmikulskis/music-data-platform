"""Tests for manifest validation."""

import gzip
import json
import zlib
from io import BytesIO
from typing import Any
from uuid import uuid4

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from manifest_validation_fixture import retained
from mdp_functions.dumps import json_bytes
from mdp_functions.manifest import parse_manifest
from mdp_functions.recovery import Recovery
from mdp_functions.runs import Runtime
from pydantic import ValidationError
from test_landing import pending


@pytest.mark.parametrize(
    "kind", ["deflate", "unicode", "parquet", "json", "validation"]
)
async def test_real_permanent_decode_errors_rejected(rt: Runtime, kind: str) -> None:
    load = await pending(rt)
    key, manifest = retained(rt, load)
    if kind == "validation":
        manifest["run_id"] = 42
        with pytest.raises(ValidationError):
            parse_manifest(json_bytes(manifest))
    elif kind == "parquet":
        part = manifest["files"][0]
        data = b"corrupt parquet"
        with pytest.raises(pa.ArrowInvalid):
            pq.ParquetFile(BytesIO(data))
        rt.store.put(part["key"], data)
        part["bytes"] = len(data)
    else:
        if kind == "deflate":
            data = bytearray(gzip.compress(b"{}"))
            data[10] = 7  # Reserved DEFLATE block type, with a valid gzip header.
            data = bytes(data)
            with pytest.raises(zlib.error):
                gzip.decompress(data)
        else:
            raw = b"\xff" if kind == "unicode" else b"{"
            with pytest.raises(
                UnicodeDecodeError if kind == "unicode" else json.JSONDecodeError
            ):
                json.loads(raw)
            data = gzip.compress(raw)
        part_key = manifest["prefix"] + "bad.jsonl.gz"
        rt.store.put(part_key, data)
        manifest["files"].append(
            {
                "key": part_key,
                "role": "payload",
                "format": "jsonl.gz",
                "bytes": len(data),
                "row_count": 1,
            }
        )
    rt.store.put(key, json_bytes(manifest))
    assert rt.landing(load["warehouse_id"]).process(load["id"])
    assert rt.db.one(
        "SELECT status,claim_token,claim_expires_at FROM control.load WHERE id=%s",
        (load["id"],),
    ) == {"status": "rejected", "claim_token": None, "claim_expires_at": None}
    assert (
        rt.db.one("SELECT error_class FROM control.run WHERE id=%s", (load["run_id"],))[
            "error_class"
        ]
        == "dump_unreadable"
    )
    assert rt.db.one(
        "SELECT payload_ref FROM control.dead_letter WHERE run_id=%s", (load["run_id"],)
    )["payload_ref"]
    assert not any(
        str(r["dump_id"]) == str(load["dump_id"]) for r in rt.warehouse.receipts()
    )


async def test_actual_failing_ddl_exits_rejected(rt: Runtime) -> None:
    load = await pending(rt)
    key, manifest = retained(rt, load)
    # A real ALTER TYPE fails on an existing nonnumeric row before landing begins.
    table = "review_ddl_" + uuid4().hex
    with psycopg.connect(rt.settings.warehouse_url) as conn:
        conn.execute(
            psycopg.sql.SQL("CREATE TABLE raw.{} (title text)").format(
                psycopg.sql.Identifier(table)
            )
        )
        conn.execute(
            psycopg.sql.SQL("INSERT INTO raw.{} VALUES ('invalid integer')").format(
                psycopg.sql.Identifier(table)
            )
        )
    manifest["target_table"] = "raw." + table
    manifest["columns"]["title"] = "bigint"
    rt.db.execute(
        "UPDATE control.load SET target_table=%s WHERE id=%s",
        (manifest["target_table"], load["id"]),
    )
    rt.store.put(key, json_bytes(manifest))
    rt.landing(load["warehouse_id"]).process(load["id"])
    assert rt.db.one(
        "SELECT status,claim_token FROM control.load WHERE id=%s", (load["id"],)
    ) == {"status": "rejected", "claim_token": None}
    assert (
        rt.db.one("SELECT error_class FROM control.run WHERE id=%s", (load["run_id"],))[
            "error_class"
        ]
        == "schema_breaking"
    )
    assert (
        "invalid input syntax"
        in rt.db.one(
            "SELECT reason FROM control.dead_letter WHERE run_id=%s", (load["run_id"],)
        )["reason"]
    )


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "run_id",
        "streamline_id",
        "cycle_id",
        "revision_id",
        "warehouse_id",
        "batch_id",
        "lease_token",
        "attempt_id",
        "page_id",
    ],
)
@pytest.mark.parametrize("value", [42, [], {}], ids=["number", "list", "object"])
async def test_invalid_uuid_manifest_quarantined_and_recovery_continues(
    rt: Runtime, field: str, value: Any
) -> None:
    load = await pending(rt)
    _, manifest = retained(rt, load)
    manifest[field] = value
    rt.store.put("dumps/000-invalid/manifest.json", json_bytes(manifest))
    await Recovery(rt).once(resume=False)
    assert (
        json.loads(rt.store.get("dumps/000-invalid/quarantine.json"))["error_class"]
        == "dump_unreadable"
    )
    assert (
        rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
            "status"
        ]
        == "loaded"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("page", []),
        ("page", "1"),
        ("files", {}),
        ("page_manifests", {}),
        ("completed_targets", 3),
        ("columns", []),
        (
            "cursor_expected",
            {"": {"version": [], "reset_generation": 0, "exists": True}},
        ),
    ],
)
async def test_manifest_collections_and_numbers_validated(
    rt: Runtime, field: str, value: Any
) -> None:
    load = await pending(rt)
    _, manifest = retained(rt, load)
    manifest[field] = value
    with pytest.raises(ValidationError):
        parse_manifest(json_bytes(manifest))


async def test_recovery_validates_other_page_manifests(rt: Runtime) -> None:
    load = await pending(rt)
    _, manifest = retained(rt, load)
    manifest["id"] = str(uuid4())
    key = "dumps/000-orphan/manifest.json"
    sibling_key = "dumps/001-orphan/manifest.json"
    manifest["page_manifests"] = [key, sibling_key]
    rt.store.put(key, json_bytes(manifest))
    rt.store.put(sibling_key, json_bytes({**manifest, "lease_token": {}}))
    await Recovery(rt).once(resume=False)
    for prefix in ("000-orphan", "001-orphan"):
        assert (
            json.loads(rt.store.get(f"dumps/{prefix}/quarantine.json"))["error_class"]
            == "dump_unreadable"
        )
    assert (
        rt.db.one("SELECT status FROM control.load WHERE id=%s", (load["id"],))[
            "status"
        ]
        == "loaded"
    )
