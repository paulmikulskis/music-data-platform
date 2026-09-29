"""Run with: uv run --project dbt python -m unittest discover -s ops/ci -p test_dbt_relations.py."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dbt_relations import POLICY, literal_reads, sandbox_reads


class ModelRelationsTest(unittest.TestCase):
    def test_sandbox_prefix_comes_from_shared_policy(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(POLICY, prefix="scratch_"):
            project = Path(directory)
            (project / "models").mkdir()
            (project / "models/bad.sql").write_text("select * from scratch_demo.saved")
            self.assertEqual(len(sandbox_reads(project)), 1)

    def test_literal_relations(self):
        for relation in (
            "raw.cost_ledger", '"raw"."cost_ledger"', "CONTROL . targets",
            "marts.profile", "tenant_demo_marts.profile", "warehouse.raw.cost_ledger",
            "some_future_schema.records",
        ):
            for sql in (
                f"select * from {relation}",
                f"select * from records left join {relation} x on true",
                f"select * from records, {relation} x",
                f"with x as (select * from {relation}) select * from x",
                f"select * from (select * from {relation}) x",
                f"select * from /* explanation */ {relation}",
                f"select * from only {relation}",
                f"select * from records cross join lateral {relation}",
                f"select coalesce((select id from {relation}), 0)",
                f"select exists(select 1 from {relation})",
                f"select * from records where id = any(select id from {relation})",
            ):
                with self.subTest(sql=sql):
                    self.assertTrue(literal_reads(sql))

    def test_dbt_relations_aliases_and_non_sql_text(self):
        sql = """
        {# from raw.comment #}
        {{ config(tags=['cadence:daily'], pre_hook='select * from raw.hook') }}
        -- from raw.comment
        /* join control.comment */
        with raw as (select * from {{ source('raw', 'cost_ledger') }})
        select raw.cost_cents, 'from raw.string' as note,
               extract(epoch from raw.created_at), substring(raw.note from 1 for 2)
        from raw join {{ ref('profile') }} p on raw.id = p.id
        where {{ mdp_context().manifest_filter('_dump_id', 'raw.cost_ledger') }}
        {% if is_incremental() %} and raw.id > (select max(id) from {{ this }}) {% endif %}
        """
        self.assertEqual(literal_reads(sql), [])

    def test_build_lint_rejects_literal_before_dbt_parse(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "models").mkdir()
            (project / "models/bad.sql").write_text("select * from raw.cost_ledger")
            result = subprocess.run(
                ["bash", str(root / "ops/ci/lint-dbt.sh"), "ci"],
                env={**os.environ, "MDP_LINT_DBT_ROOT": directory},
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("models/bad.sql: literal relation raw.cost_ledger; use source()/ref()", result.stdout)
