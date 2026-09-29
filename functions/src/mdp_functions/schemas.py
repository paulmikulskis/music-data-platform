"""Frozen schemas, lossless extra fields, and additive drift classification."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from mdp_functions.errors import ServiceError

LINEAGE = {
    "_run_id": "uuid",
    "_dump_id": "uuid",
    "_landed_seq": "bigint",
    "_cycle_id": "uuid",
    "_revision_id": "uuid",
    "_target_id": "uuid",
    "_request_id": "text",
    "_source_key": "text",
    "_ingested_at": "timestamptz",
    "_extra": "jsonb",
}
GOLD_COLUMNS = {
    "input_ref": "text", "input_version": "text", "scope": "text", "step": "text",
    "config_version": "text", "llm_step_id": "text", "learning_eligible": "boolean",
    "_source_keys": "jsonb", "_input_cycle": "text",
    # Admission time of the producing run: configurations rank by it, newest first.
    "run_admitted_at": "timestamptz",
}
# A silver function declaring input_key: the identity its step 1b completion rows match.
SILVER_INPUT_COLUMNS = {
    "input_ref": "text", "input_version": "text", "scope": "text", "step": "text",
    "config_version": "text", "_source_keys": "jsonb", "_input_cycle": "text",
}
COMPLETION_COLUMNS = {k: "text" for k in ("scope", "input_ref", "input_version", "step", "config_version")}
COMPLETION_COLUMNS["required_outputs"] = "jsonb"
# Runtime-written bronze completion: the run's registered output dumps per table and their row counts,
# plus the range or generation the function recorded with ctx.record_completion().
RUN_COMPLETION = "raw._run_completion"
RUN_COMPLETION_COLUMNS = {"run_id": "text", "source_key": "text", "outputs": "jsonb", "recorded": "jsonb"}

SQL_TYPES = {
    "unknown": "text",
    "text": "text",
    "bigint": "bigint",
    "double": "double precision",
    "boolean": "boolean",
    "jsonb": "jsonb",
    "date": "date",
    "timestamptz": "timestamptz",
    "uuid": "uuid",
}


def infer_type(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, (float, Decimal)):
        return "double"
    if isinstance(value, datetime):
        return "timestamptz"
    if isinstance(value, date):
        return "date"
    if isinstance(value, (dict, list)):
        return "jsonb"
    return "text"


def merge_type(old: str, new: str) -> str:
    if new == "unknown" or old == new:
        return old
    if old == "unknown":
        return new
    if {old, new} <= {"bigint", "double"}:
        return "double"
    raise ServiceError("schema_breaking", f"Incompatible types {old} and {new}")


def declared(model: type[BaseModel]) -> dict[str, str]:
    result = {}
    for name, field in model.model_json_schema()["properties"].items():
        variants = field.get("anyOf", [field])
        typ = next((v for v in variants if v.get("type") != "null"), {})
        result[name] = {
            "integer": "bigint",
            "number": "double",
            "boolean": "boolean",
            "object": "jsonb",
            "array": "jsonb",
        }.get(
            typ.get("type"),
            {"date-time": "timestamptz", "date": "date", "uuid": "uuid"}.get(
                typ.get("format"), "text"
            ),
        )
    return result


def shape(
    rows: list[dict[str, Any]], declaration: Any, previous: dict[str, str] | None,
    runtime_columns: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], list[dict[str, Any]], bool]:
    model = (
        declaration
        if isinstance(declaration, type) and issubclass(declaration, BaseModel)
        else None
    )
    columns = declared(model) if model else dict(previous or {})
    drift = False
    # Existing known fields are typed; previously unseen fields remain in _extra after the freeze.
    if not model and not previous:
        for row in rows:
            for key, value in row.items():
                if key not in LINEAGE and key != "tenant_id":
                    columns[key] = merge_type(
                        columns.get(key, "unknown"), infer_type(value)
                    )
    elif not model:
        for row in rows:
            for key in columns:
                # Runtime columns are typed by the runtime, not inferred: a retained result read back
                # from JSON carries a timestamp as text.
                if key in (runtime_columns or {}):
                    continue
                widened = merge_type(columns[key], infer_type(row.get(key)))
                drift |= widened != columns[key]
                columns[key] = widened
    if model and previous:
        drift = any(k not in previous or previous[k] != v for k, v in columns.items())
        for key, typ in previous.items():
            if key in (runtime_columns or {}):
                continue
            if key not in columns:
                raise ServiceError("schema_breaking", f"Removed column {key}")
            merge_type(typ, columns[key])
    columns.update(runtime_columns or {})
    accepted, rejected = [], []
    for row in rows:
        extra = {
            **row.get("_extra", {}),
            **{
                k: v
                for k, v in row.items()
                if k not in columns and k not in LINEAGE and k != "tenant_id"
            },
        }
        values = {k: row.get(k) for k in columns}
        if model:
            try:
                values = model.model_validate(
                    {k: v for k, v in row.items() if k in columns}
                ).model_dump(mode="json")
                values.update({k: row.get(k) for k in (runtime_columns or {})})
            except ValidationError as exc:
                rejected.append(
                    {
                        "record": row,
                        "reason": str(exc),
                        "_target_id": row.get("_target_id"),
                        "_request_id": row.get("_request_id"),
                    }
                )
                continue
        accepted.append(
            {
                **values,
                "_extra": extra,
                "_target_id": row.get("_target_id"),
                "_request_id": row.get("_request_id"),
            }
        )
    return accepted, columns, rejected, drift


def schema_fingerprint(columns: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(columns.items()), separators=(",", ":")).encode()
    ).hexdigest()


def archive(root: Path, source: str, table: str, columns: dict[str, str]) -> str:
    fingerprint = schema_fingerprint(columns)
    directory = root / source
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{fingerprint}.json"
    tables = (
        set(json.loads(path.read_text()).get("tables", [])) if path.exists() else set()
    )
    tables.add(table)
    path.write_text(
        json.dumps(
            {"table": min(tables), "tables": sorted(tables), "columns": columns},
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    return fingerprint
