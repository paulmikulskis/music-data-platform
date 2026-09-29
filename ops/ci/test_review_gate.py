"""Completeness is mandatory for literal, forwarded and aggregated served keys."""

import copy
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("review_gate", Path(__file__).with_name("review_gate.py"))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
ROOT = Path(__file__).resolve().parents[2]
MODEL = f"model.{gate.PROJECT}.mart_fixture"


def manifest(code, annotated=True):
    prefix = f"model.{gate.PROJECT}."
    return {
        "nodes": {
            MODEL: {
                "name": "mart_fixture", "resource_type": "model",
                "config": {"meta": {"grain": ["id"]}},
                "raw_code": code + ("\n{{ mdp_annotate('rows') }}" if annotated else ""),
                "depends_on": {"nodes": [prefix + "staging"]},
            },
            prefix + "staging": {"depends_on": {"nodes": ["source.fixture.raw"]}},
        },
        "sources": {"source.fixture.raw": {"meta": {"writers": ["actual_writer", "release_writer"]}}},
        "macros": {f"macro.{gate.PROJECT}.mdp_annotate": {
            "macro_sql": (ROOT / "dbt/macros/mdp_annotate.sql").read_text(),
        }},
    }


class MartLineageTests(unittest.TestCase):
    def test_quoted_hook_delimiters_do_not_hide_annotation_or_keys(self):
        for config in (
            "{{ config(post_hook=['{{ mdp_search_index() }}', '{{ mdp_analyze() }}']) }}",
            '{{ config(post_hook=["{{ mdp_search_index() }}"]) }}',
            r'''{{ config(post_hook=["select 'escaped \\\" }} text'"]) }}''',
            "{% set hook = '{{ mdp_analyze() }} %}' %}",
        ):
            with self.subTest(config=config):
                code = config + "\nselect source_keys as _source_keys from upstream"
                self.assertEqual(gate.source_key_expressions(code), (set(), True))
                m = manifest(code)
                self.assertEqual(gate.served_mart_errors(m), [])
                m["nodes"][MODEL]["compiled_code"] = (
                    "select cast(mdp_flags.keys as text) as source_keys "
                    "from (select '[\"actual_writer\"]' as _mdp_lineage_writers) mdp_lineage"
                )
                self.assertIn("release_writer", gate.served_mart_errors(m)[0])
                self.assertIn("can omit", gate.served_mart_errors(manifest(code, False))[0])
                bad = config + '\nselect \'["unrelated_writer"]\' as _source_keys'
                self.assertIn("not an upstream raw writer", gate.served_mart_errors(manifest(bad))[0])

    def test_injected_bad_literal(self):
        good = manifest("select '[\"actual_writer\"]' as _source_keys")
        self.assertEqual(gate.served_mart_errors(good), [])
        bad = copy.deepcopy(good)
        bad["nodes"][MODEL]["raw_code"] = bad["nodes"][MODEL]["raw_code"].replace("actual_writer", "unrelated_writer")
        self.assertIn("'unrelated_writer' is not an upstream raw writer", gate.served_mart_errors(bad)[0])

    def test_literals_branches_and_aggregates_need_completeness(self):
        for code in (
            "select '[\"actual_writer\"]' as _source_keys",
            "select case when found then '[\"actual_writer\",\"release_writer\"]' else '[\"actual_writer\"]' end as _source_keys",
            "select '[\"actual_writer\",\"release_writer\"]' as _source_keys union all select '[]'",
            "select {{ mdp_source_keys_agg('source_key') }} as _source_keys",
            "select r._source_keys from rows r left join releases on r.album_id = releases.album_id",
            "select source_keys as _source_keys from upstream",
        ):
            with self.subTest(code=code):
                errors = gate.served_mart_errors(manifest(code, annotated=False))
                self.assertTrue(any("can omit upstream writers" in e and "release_writer" in e for e in errors))
                self.assertEqual(gate.served_mart_errors(manifest(code)), [])

    def test_compiled_floor_must_include_every_writer(self):
        m = manifest("select source_keys as _source_keys from upstream")
        for floor, passes in (( '["actual_writer","release_writer"]', True), ('["actual_writer"]', False), ('[]', False)):
            m["nodes"][MODEL]["compiled_code"] = (
                "select cast(mdp_flags.keys as text) as source_keys "
                f"from (select '{floor}' as _mdp_lineage_writers) mdp_lineage"
            )
            self.assertEqual(not gate.served_mart_errors(m), passes)

    def test_old_annotation_cannot_prove_missed_release_join(self):
        m = manifest("select {{ mdp_source_keys(arrays=['p._source_keys', 'r._source_keys']) }} as _source_keys "
                     "from picked p left join releases r on r.album_id = p.display_album")
        m["macros"][f"macro.{gate.PROJECT}.mdp_annotate"]["macro_sql"] = "select _source_keys as source_keys from rows"
        self.assertIn("release_writer", gate.served_mart_errors(m)[0])

    def test_comments_and_unrelated_json_do_not_supply_keys(self):
        code = "-- select '[\"actual_writer\",\"release_writer\"]' as _source_keys\nselect '[\"actual_writer\"]' as evidence"
        self.assertTrue(any("carries no source keys" in e for e in gate.served_mart_errors(manifest(code, False))))

    def test_annotation_cannot_be_hidden_in_an_unused_cte(self):
        m = manifest("with unused as ({{ mdp_annotate('rows') }}) select * from other", False)
        self.assertIn("can omit", gate.served_mart_errors(m)[0])

    def test_no_writer_and_unserved_models(self):
        m = manifest("select '[]' as _source_keys", False)
        m["sources"]["source.fixture.raw"]["meta"]["writers"] = []
        self.assertEqual(gate.served_mart_errors(m), [])
        m["nodes"][MODEL]["config"]["meta"] = {}
        self.assertEqual(gate.served_mart_errors(m), [])


if __name__ == "__main__":
    unittest.main()
