"""Reader counters follow declarations, including selectors and unknown readers."""

import json
from pathlib import Path

from mdp_functions.exporter import reader_units
from mdp_functions.registry import discover


def test_reader_units_match_generated_contract():
    root = Path(__file__).resolve().parents[2]
    rows = reader_units(discover())
    assert rows == json.loads(
        (
            root / "control/packages/contracts/src/source-readers.generated.json"
        ).read_text()
    )
    readers = {row["source_key"]: row for row in rows}
    assert readers["sz_chart"]["unit"] == "charts"
    assert readers["sz_chart"]["platforms"] == ["shazam"]
    assert readers["sp_playlist_weekly"]["member_cadence"] == "weekly"
    assert readers["sc_curator_playlists"]["unit"] == "curators"
    assert readers["bc_radio"]["id_prefix"] == "radio:"
    assert readers["sc_hubs"]["unit"] is None
    assert readers["billboard_hot100"]["unit"] is None
