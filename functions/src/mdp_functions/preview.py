"""Bounded declared-output previews with signed, source-scoped page cursors."""

import base64
import hashlib
import hmac
from datetime import datetime
from typing import Literal

from psycopg import sql
from pydantic import BaseModel, Field, ValidationError

from mdp_functions.errors import ServiceError


class PreviewColumn(BaseModel):
    name: str
    type: str
    nullable: bool


class OutputPreview(BaseModel):
    table: str
    rows: list[dict[str, object]]
    columns: list[PreviewColumn]
    next_cursor: str | None


class PreviewCursor(BaseModel):
    version: Literal[1] = 1
    source: str
    table: str
    before: datetime
    offset: int = Field(ge=0)


def encode_cursor(cursor: PreviewCursor, secret: str) -> str:
    payload = base64.urlsafe_b64encode(cursor.model_dump_json().encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def decode_cursor(value: str, source: str, table: str, secret: str) -> PreviewCursor:
    try:
        if len(value) > 4096:
            raise ValueError("cursor too long")
        payload, signature = value.split(".")
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature mismatch")
        cursor = PreviewCursor.model_validate_json(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if cursor.source != source or cursor.table != table or cursor.before.tzinfo is None:
            raise ValueError("cursor scope mismatch")
        return cursor
    except (ValueError, ValidationError) as exc:
        raise ServiceError("invalid_cursor", "Restart the preview from its first page", 400) from exc


def output_preview(conn, source: str, table: str, secret: str, value: str | None = None) -> dict:
    # The caller supplies a table from the registered manifest, never arbitrary SQL.
    cursor = decode_cursor(value, source, table, secret) if value else PreviewCursor(
        source=source, table=table, before=conn.execute("SELECT clock_timestamp() AS at").fetchone()["at"], offset=0
    )
    columns = conn.execute(
        """SELECT attname AS name, format_type(atttypid,atttypmod) AS type,
                  NOT attnotnull AS nullable
           FROM pg_attribute WHERE attrelid=to_regclass(%s)
           AND attnum>0 AND NOT attisdropped ORDER BY attnum""", (table,)
    ).fetchall()
    if not columns:
        return {"table": table, "rows": [], "columns": [], "next_cursor": None}
    # A fixed ingestion watermark excludes newly landed rows between page requests.
    # Full-row ordering resolves equal ingestion times deterministically, including
    # after physical table rewrites. Identical rows are interchangeable for preview.
    rows = conn.execute(sql.SQL(
        "SELECT * FROM {} AS preview_row WHERE _ingested_at<=%s "
        'ORDER BY _ingested_at DESC, to_jsonb(preview_row)::text COLLATE "C" '
        "LIMIT 101 OFFSET %s"
    ).format(sql.Identifier(*table.split("."))), (cursor.before, cursor.offset)).fetchall()
    next_cursor = encode_cursor(cursor.model_copy(update={"offset": cursor.offset + 100}), secret) if len(rows) > 100 else None
    return {"table": table, "rows": rows[:100], "columns": columns, "next_cursor": next_cursor}
