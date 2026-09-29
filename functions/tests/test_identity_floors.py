"""Both recording and ISRC resolution obey the seeded platform/method floor."""
import os
from datetime import UTC, datetime

import pytest
from identity_harness import Warehouse
from test_identity_spine import reference_rows, resolution

pytestmark = pytest.mark.docker


def test_seeded_floors_gate_both_identity_fields(tmp_path):
    admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
    if not admin:
        pytest.skip("Docker integration tests require MDP_CONTROL_ADMIN_URL")
    wh = Warehouse(admin, tmp_path)
    day = datetime(2026, 9, 24, tzinfo=UTC)
    try:
        wh.generation("20260923-002121", 1, reference_rows())
        cycle = wh.cycle("hourly", 1, day)
        wh.dbt(cycle, "identity_confidence_floors", command="seed")
        for platform in ("spotify", "apple_music", "soundcloud", "bandcamp"):
            wh.observation(platform, "floor-fixture", day, [
                {"id": f"{method}-{n}", "title": "Fixture", "artists": ["Fixture"], "duration": 200000}
                for method in ("mb_trigram", "crosswalk") for n in range(3)
            ], "editorial", 1)
        wh.dbt(cycle, "int_reference__current int_identity__track_inputs")
        floors = {(p, m): float(f) for p, m, f in wh.query("SELECT platform,method,floor FROM reference.identity_confidence_floors")}
        inputs = wh.query("SELECT platform,platform_track_id,fields_hash FROM intermediate.int_identity__track_inputs")
        for platform, track, fields in inputs:
            method, n = track.rsplit("-", 1)
            floor = floors[platform, method]
            confidence = (min(floor - 0.01, 0.99), min(floor, 1.0), None)[int(n)]
            common = resolution({"id": track, "platform": platform}, fields, "unused", "2026-W39",
                                method=method, confidence=confidence)
            if method == "crosswalk":
                keep = {"platform", "platform_track_id", "status", "method", "isrc", "confidence", "candidate_count",
                        "evidence", "input_ref", "input_version", "fields_hash", "reference_version", "retry_week",
                        "scope", "step", "config_version", "learning_eligible", "_source_keys"}
                common = {k: v for k, v in common.items() if k in keep}
            wh.land("raw.mb_resolve" if method == "mb_trigram" else "raw.track_isrc_crosswalk",
                    [common], 1, "mb_resolve" if method == "mb_trigram" else "track_isrc_crosswalk", cycle["id"])
        wh.dbt(cycle, "int_track_identity")
        for platform, track, isrc, gid, method, confidence in wh.query(
                "SELECT platform,platform_track_id,isrc,mb_recording_gid,recording_method,recording_confidence "
                "FROM intermediate.int_track_identity"):
            expected_method, n = track.rsplit("-", 1)
            if n == "1" and floors[platform, expected_method] <= 1.0:
                assert isrc and gid and method == expected_method
                assert confidence == pytest.approx(floors[platform, expected_method])
            else:
                assert (isrc, gid, method, confidence) == (None, None, None, None)
        # Changing the table changes resolution without touching SQL; absent rows fail closed.
        with wh.connect() as conn:
            conn.execute("UPDATE reference.identity_confidence_floors SET floor=1.01 WHERE platform='spotify'")
            conn.execute("DELETE FROM reference.identity_confidence_floors WHERE platform='apple_music'")
        wh.dbt(cycle, "int_track_identity")
        assert wh.query("SELECT count(*) FROM intermediate.int_track_identity WHERE platform IN ('spotify','apple_music') "
                        "AND (isrc IS NOT NULL OR mb_recording_gid IS NOT NULL)") == [(0,)]
    finally:
        wh.drop()
