"""Preview pagination over real PostgreSQL rows, without persistent fixture changes."""
import os
from uuid import uuid4
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg import sql

from mdp_functions.errors import ServiceError
from mdp_functions.preview import PreviewCursor, decode_cursor, encode_cursor, output_preview


def test_cursor_refuses_tampering_and_cross_source_or_table_reuse():
    cursor = encode_cursor(PreviewCursor(source="fixture_accounts", table="raw.example", before=datetime.now(timezone.utc), offset=100), "fixture")
    assert decode_cursor(cursor, "fixture_accounts", "raw.example", "fixture").offset == 100
    for value, source, table in [(cursor + "0", "fixture_accounts", "raw.example"), (cursor, "other", "raw.example"), (cursor, "fixture_accounts", "raw.other"), ("not-a-cursor", "fixture_accounts", "raw.example")]:
        with pytest.raises(ServiceError, match="Restart the preview"):
            decode_cursor(value, source, table, "fixture")


@pytest.mark.skipif(os.environ.get("MDP_CONTROL_INTEGRATION") != "1", reason="isolated warehouse required")
def test_preview_has_distinct_pages_fixed_watermark_catalog_types_and_end():
    with psycopg.connect(os.environ["MDP_WAREHOUSE_TEST_URL"], row_factory=dict_row) as conn:
        schema = "control_preview_" + uuid4().hex
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        conn.execute("CREATE TABLE control_preview (id integer NOT NULL, amount numeric(12,2), _ingested_at timestamptz NOT NULL)")
        conn.execute("INSERT INTO control_preview SELECT n,n/10.0,clock_timestamp()-interval '1 day' FROM generate_series(1,205) n")
        first = output_preview(conn, "fixture_accounts", schema + ".control_preview", "fixture")
        assert len(first["rows"]) == 100 and first["next_cursor"]
        types = {c["name"]: c for c in first["columns"]}
        assert types["amount"]["type"] == "numeric(12,2)" and types["amount"]["nullable"]
        assert types["id"]["type"] == "integer" and not types["id"]["nullable"]
        conn.execute("INSERT INTO control_preview VALUES (999,0,clock_timestamp())")
        second = output_preview(conn, "fixture_accounts", schema + ".control_preview", "fixture", first["next_cursor"])
        third = output_preview(conn, "fixture_accounts", schema + ".control_preview", "fixture", second["next_cursor"])
        assert len(second["rows"]) == 100
        assert len(third["rows"]) == 5 and third["next_cursor"] is None
        ids = [r["id"] for page in [first,second,third] for r in page["rows"]]
        assert len(set(ids)) == 205 and 999 not in ids
        assert set(ids) == set(range(1,206))
        conn.rollback()
