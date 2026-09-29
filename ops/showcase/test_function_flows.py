"""Reviewed function facts stay tied to the declarations."""

import copy
import unittest

from function_flows import HOSTLESS, annotations, flow_rows, generate, manifests

LABELS = {
    "apple_music": "Apple Music",
    "musicbrainz": "MusicBrainz",
    "listenbrainz": "ListenBrainz",
    "typesafe": "TypeSafe",
    "fixture_source": "Fixture source",
    "sp_playlist": "Fixture accounts",
}


class FunctionFlowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = manifests()
        cls.rows = flow_rows()
        cls.notes = annotations()
        cls.flows = generate(cls.catalog, cls.rows, cls.notes)

    def test_hostless_is_exact(self):
        self.assertEqual(
            set(HOSTLESS),
            {k for k, m in self.catalog.items() if m.external and not m.hosts},
        )

    def test_every_function_has_one_row(self):
        self.assertEqual(self.catalog.keys(), self.rows.keys())
        self.assertNotIn("lifecycle_daily_probe", self.flows)
        for key in ("cycle_close", "targets_export"):
            self.assertTrue(self.flows[key]["hidden"])
            self.assertEqual(self.flows[key]["job_kind"], "Housekeeping")

    def test_disagreeing_platform_fails(self):
        rows = copy.deepcopy(self.rows)
        rows["sp_playlist"]["platform"] = "fixture"
        with self.assertRaisesRegex(ValueError, "platform disagrees"):
            generate(self.catalog, rows, self.notes)

    def test_unknown_and_missing_rows_fail(self):
        rows = copy.deepcopy(self.rows)
        del rows["sp_playlist"]
        with self.assertRaisesRegex(ValueError, "missing"):
            generate(self.catalog, rows, self.notes)
        rows["unknown"] = self.rows["sp_playlist"]
        with self.assertRaisesRegex(ValueError, "unknown"):
            generate(self.catalog, rows, self.notes)

    def test_ai_kind_matches_in_both_directions(self):
        for key, kind in (("jev_instrument_family", "Reader"), ("sp_playlist", "AI step")):
            with self.subTest(key=key):
                rows = copy.deepcopy(self.rows)
                rows[key]["job_kind"] = kind
                with self.assertRaisesRegex(ValueError, "job kind"):
                    generate(self.catalog, rows, self.notes)

    def test_external_copy_names_source_and_effective_cadence(self):
        for key, flow in self.flows.items():
            with self.subTest(key=key):
                if flow["hidden"]:
                    continue
                self.assertTrue(flow["card"]["why"])
                self.assertTrue(flow["card"]["ideas"])
                if not self.catalog[key].external:
                    self.assertIsNone(flow["platform"])
                    continue
                names = [
                    LABELS.get(k, k) for k in (flow["platform"], flow["vendor"]) if k
                ]
                self.assertTrue(
                    any(name.lower() in flow["card"]["what"].lower() for name in names)
                )
                phrase = {
                    "hourly": "every hour",
                    "daily": "every day",
                    "weekly": "every week",
                }[flow["cadence"]]
                self.assertIn(phrase, flow["card"]["what"])

    def test_only_selected_fields_are_emitted(self):
        for flow in self.flows.values():
            self.assertEqual(
                set(flow),
                {
                    "platform",
                    "access",
                    "vendor",
                    "job_kind",
                    "cadence",
                    "hosts",
                    "writes",
                    "stages",
                    "card",
                    "hidden",
                },
            )
        self.assertEqual(self.flows["sp_playlist_weekly"]["cadence"], "weekly")
        self.assertEqual(self.flows["am_playlist"]["platform"], "apple_music")
        self.assertEqual(self.flows["mb_resolve"]["access"], "our MusicBrainz copy")
        self.assertEqual(self.flows["jev_instrument_family"]["stages"][2]["mark"], "typesafe")
