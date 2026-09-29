"""The evidence rule uses independent ISRCs, primary credits and an inclusive 3 s window."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ops/identity"))
from measure import verdict, wilson
from summarize import choose_floor


def test_truth_rule():
    row = {"title": "A Song!", "artist_names": '["Fixture Áct", "Guest"]', "duration_ms": 180000, "platform_isrc": None}
    truth = {"title": "a song", "primary_credit": "Fixture Act", "duration_ms": 183000, "isrcs": ["USAAA2600001"]}
    assert verdict(row, truth) == ("correct", "metadata_agrees")
    assert verdict(row, {**truth, "duration_ms": 183001}) == ("wrong", "duration_disagrees")
    assert verdict(row, {**truth, "primary_credit": "Fixture Act Two"}) == ("wrong", "primary_artist_disagrees")
    assert verdict(row, {**truth, "title": "A Song (Live)"}) == ("wrong", "title_disagrees")
    assert verdict(row, {**truth, "duration_ms": None}) == ("ambiguous", "missing_metadata")
    assert verdict(row, None) == ("ambiguous", "missing_recording")
    # A derived ISRC is never a truth signal; only platform_isrc counts.
    assert verdict({**row, "isrc": "USAAA2600001"}, {**truth, "title": "Other"})[0] == "wrong"
    assert verdict({**row, "platform_isrc": "us-aaa-26-00001"}, {**truth, "title": "Other"})[0] == "correct"
    assert verdict({**row, "platform_isrc": "USAAA2600002"}, truth) == ("wrong", "isrc_disagrees")


def test_floor_requires_supported_precision_and_keeps_unknown_strata_explicit():
    def rows(n, confidence, verdict):
        return [{"recording_confidence": confidence, "verdict": verdict}] * n
    assert choose_floor(rows(55, 1.0, "correct"))[0] == .9
    assert choose_floor(rows(199, 1.0, "correct") + rows(10, 1.0, "wrong"))[0] == 1.01
    assert choose_floor(rows(200, .93, "correct") + rows(10, .9, "wrong"))[0] == .93
    assert choose_floor(rows(300, 1.0, "ambiguous"))[0] == .9
    assert wilson(0, 0) is None
    assert wilson(125, 125)[0] >= .97
    assert wilson(124, 124)[0] < .97
