"""Check the shipped source split against current function declarations."""

import json
import unittest

from source_registry import OUTPUT, generate


class SourceRegistry(unittest.TestCase):
    def test_registry_does_not_drift(self):
        self.assertEqual(json.loads(OUTPUT.read_text()), generate())

    def test_egress_and_job_kind_define_the_boundary(self):
        registry = generate()
        for key in (
            "cycle_close",
            "targets_export",
        ):
            self.assertFalse(registry[key]["source"])
        for key in ("sp_playlist", "sz_chart", "mb_artist_catalog"):
            self.assertTrue(registry[key]["source"])
        self.assertNotIn("lifecycle_daily_probe", registry)
