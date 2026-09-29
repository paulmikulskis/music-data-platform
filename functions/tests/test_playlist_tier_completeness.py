"""Curated playlist targets retain their movement classification and import evidence."""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "inputs"


def read(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def test_playlist_tier_completeness():
    tiers = read(ROOT / "dbt/seeds/playlist_reach_tiers.csv")
    keyed = {(row["platform"], row["playlist_id"]): row for row in tiers}
    assert len(keyed) == len(tiers)
    targets = read(EVIDENCE / "playlist_seed.csv")
    local = ROOT / "inputs/playlist_seed.csv"
    if local.exists():
        targets += read(local)
    for row in targets:
        if row["platform"] not in {"spotify", "apple_music"}:
            continue
        if row.get("cadence") != "daily":
            continue
        key = row["platform"], row["platform_account_id"].split(":")[-1]
        assert key in keyed, f"Add the target's movement tier: {key}"
        assert keyed[key]["list_kind"] in {"editorial", "new_music", "chart"}
        assert keyed[key]["reach_tier"] in {"1", "2", "3", "4"}
        assert keyed[key]["market"] == row.get("priority_market", "").upper()
