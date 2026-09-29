"""Tests for enrichment landing."""


import subprocess
from io import BytesIO
from uuid import uuid4

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from mdp_functions.warehouse.postgres import PostgresWarehouse
from psycopg import sql


def test_enrichment_landing_preserves_physical_copies_and_receipt_fence(databases):
    adapter = PostgresWarehouse(databases["warehouse_url"])
    table = "raw.enrich_regression_" + uuid4().hex[:8]
    columns = {
        **dict.fromkeys(
            ["input_ref", "input_version", "llm_step_id", "config_version", "label"],
            "text",
        ),
        "_dump_id": "uuid",
        "_landed_seq": "bigint",
    }
    adapter.ensure(table, columns)
    warehouse = uuid4()
    manifests = []
    for n in range(2):
        dump = uuid4()
        row = {
            "input_ref": "same",
            "input_version": "v1",
            "llm_step_id": None,
            "config_version": "config",
            "label": "fixture",
            "_dump_id": str(dump),
            "_landed_seq": n + 1,
        }
        output = BytesIO()
        pq.write_table(pa.Table.from_pylist([row]), output)
        manifest = {"id": dump, "target_table": table, "columns": columns}
        claim = {
            "warehouse_id": warehouse,
            "claim_token": uuid4(),
            "generation": 1,
            "op": "load",
        }
        assert adapter.land(manifest, claim, [output.getvalue()]) == 1
        manifests.append((manifest, claim, output.getvalue()))
    manifest, claim, part = manifests[0]
    assert (
        adapter.land(
            manifest,
            {**claim, "claim_token": uuid4(), "generation": 2, "op": "repair"},
            [part],
        )
        == 1
    )
    assert (
        adapter.land(
            manifest, {**claim, "claim_token": uuid4(), "generation": 1}, [part]
        )
        == 1
    )
    with adapter.connect() as conn:
        assert (
            conn.execute(
                sql.SQL("SELECT count(*) AS n FROM {}").format(
                    sql.Identifier(*table.split("."))
                )
            ).fetchone()["n"]
            == 2
        )
        assert [
            r["rows_deduped"]
            for r in conn.execute(
                "SELECT rows_deduped FROM raw._load_receipts WHERE target_table=%s ORDER BY rows DESC",
                (table,),
            )
        ] == [0, 0]


def test_enrichment_staging_excludes_later_cycle_and_dedupes(databases):
    render = """
from pathlib import Path
from types import SimpleNamespace
from jinja2 import Environment
print(Environment().from_string(Path('dbt/models/staging/stg_jev__instrument_family.sql').read_text()).render(
 config=lambda **kw: '', ref=lambda *args: 'fixture_invoke', source=lambda *args: 'pg_temp.enrich_fixture',
 mdp_source_projection=lambda *args: '*',
 mdp_context=lambda: SimpleNamespace(manifest_filter=lambda column, table=None: column + " IN (SELECT dump_id FROM pg_temp.manifest_fixture WHERE cycle='old')")))
"""
    rendered = subprocess.run(
        ["uv", "run", "--project", "dbt", "python", "-c", render],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    with psycopg.connect(databases["admin_warehouse"]) as conn:
        conn.execute(
            "CREATE TEMP TABLE enrich_fixture(input_ref text,input_version text,llm_step_id text,config_version text,_landed_seq int,_dump_id text,label text,_source_key text DEFAULT 'fixture',scope text DEFAULT 'global',step text DEFAULT 'external:fixture',question text DEFAULT 'fixture')"
        )
        conn.execute(
            "INSERT INTO enrich_fixture(input_ref,input_version,llm_step_id,config_version,_landed_seq,_dump_id,label) VALUES ('same','v1',NULL,'c1',1,'old-dump','old'),('same','v1',NULL,'c1',2,'old-duplicate','duplicate'),('same','v1',NULL,'c2',3,'new-dump','future')"
        )
        conn.execute("CREATE TEMP TABLE manifest_fixture(cycle text,dump_id text)")
        conn.execute(
            "INSERT INTO manifest_fixture VALUES ('old','old-dump'),('old','old-duplicate'),('new','new-dump')"
        )
        rows = conn.execute(rendered).fetchall()
        assert len(rows) == 1 and rows[0][6] == "duplicate"
