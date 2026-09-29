"""Audit denominators and full-census annotation guards."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "url_audit", Path(__file__).resolve().parents[2] / "ops/identity/audit.py"
)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def population():
    classes = [
        None,
        "title_suffix_only",
        "artist_alias_or_credit_order",
        "duration_only",
        "different_recording_same_work",
        "real_mislink",
        "other_title_text",
        None,
    ]
    rows, labels = [], []
    for n, cls in enumerate(classes):
        row = {
            "platform": "spotify",
            "platform_track_id": str(n),
            "mb_recording_gid": str(n),
            "recording_method": "mb_url",
            "original_sample": n != 5,
            "verdict": "wrong" if cls else "ambiguous" if n == 7 else "correct",
        }
        rows.append(row)
        if cls:
            labels.append({**row, "class": cls})
    return rows, labels


def test_relaxation_keeps_versions_and_other_titles_wrong_and_uses_census_threshold():
    sample, census = audit.summarize(*population())
    assert sample["strict_precision"] == 1 / 6
    assert sample["relaxed_precision"] == 4 / 6
    assert sample["real_mislinks"] == 0
    assert census["strict_precision"] == 1 / 7
    assert census["relaxed_precision"] == 4 / 7
    assert census["real_mislink_share"] == 1 / 8  # includes ambiguous resolved row
    assert census["all_residual_share"] == 3 / 8


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate", "unknown"])
def test_incomplete_or_invalid_annotations_cannot_produce_precision(defect):
    rows, labels = population()
    if defect == "missing":
        labels.pop()
    elif defect == "extra":
        labels.append({**labels[0], "platform_track_id": "not-in-census"})
    elif defect == "duplicate":
        labels.append(labels[0])
    else:
        labels[0]["class"] = "accept_everything"
    with pytest.raises(AssertionError):
        audit.summarize(rows, labels)
