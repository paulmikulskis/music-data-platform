"""CI models declare their runtime mirrors and reject literal relation reads."""

import re
import subprocess

import duckdb
import yaml
from conftest import copy_source_inputs
from mdp_functions.costsync import COST_LEDGER_COLUMNS, COST_LEDGER_DDL
from mdp_functions.exporter import export_sources
from mdp_functions.settings import REPO, Settings


def test_cost_mirror_source_and_bootstrap(tmp_path):
    copy_source_inputs(tmp_path)
    export_sources(Settings(), tmp_path)
    sources = yaml.safe_load((tmp_path / "dbt/models/sources/_raw__sources.yml").read_text())
    raw = next(source for source in sources["sources"] if source["name"] == "raw")
    ledger = next(table for table in raw["tables"] if table["name"] == "cost_ledger")
    assert {column["name"]: column["data_type"] for column in ledger["columns"]} == COST_LEDGER_COLUMNS
    assert ledger["freshness"] is None and ledger["loaded_at_field"] is None
    assert not ledger.get("meta", {}).get("writers")

    bootstrap = (tmp_path / "dbt/macros/bootstrap_raw.sql").read_text()
    ddl = re.search(r'run_query\("(create table if not exists raw\.cost_ledger .*?)"\)', bootstrap)[1]
    with duckdb.connect() as conn:
        conn.execute("create schema raw")
        conn.execute(ddl.replace('\\"', '"'))
        local_shape = conn.execute("describe raw.cost_ledger").fetchall()
        assert conn.execute("select count(*) from raw.cost_ledger where is_current").fetchone() == (0,)
        conn.execute("drop table raw.cost_ledger")
        conn.execute(COST_LEDGER_DDL)
        producer_shape = conn.execute("describe raw.cost_ledger").fetchall()
        assert [(r[0], r[1]) for r in local_shape] == [(r[0], r[1]) for r in producer_shape]
        assert producer_shape[0][3] == "PRI"


def test_model_relation_lint():
    result = subprocess.run(
        ["uv", "run", "--project", "dbt", "python", "-m", "unittest", "discover",
         "-s", "ops/ci", "-p", "test_dbt_relations.py", "-v"],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
