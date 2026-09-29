"""Backtest row files stay in scratch for standalone commands too."""

import json
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backtest import evaluate, run, warehouse


@pytest.mark.parametrize("action", ["capture", "replay", "label"])
def test_standalone_rows_stay_in_scratch(monkeypatch, tmp_path, action):
    scratch = tmp_path / "private"
    output = tmp_path / "repository" / "ops" / "evidence"
    scratch.mkdir()
    (scratch / "state.json").write_text(json.dumps({"cycles": []}))
    destinations = []

    def collect(folder, specs, destination, chosen_on):
        destinations.append(destination)
        (destination / "predictions.csv").write_text("id,rank\nexample,1\n")
        (destination / "inputs.csv").write_text("id\nexample\n")

    def label(connection, state, destination):
        destinations.append(destination)
        (destination / "events.csv").write_text("id\nexample\n")
        (destination / "labels.json").write_text("{}\n")

    monkeypatch.setenv("MDP_BACKTEST_SCRATCH", str(scratch))
    monkeypatch.setattr(sys, "argv", ["run.py", action, "--output", str(output)])
    monkeypatch.setattr(warehouse, "capture", collect)
    monkeypatch.setattr(warehouse, "replay", collect)
    monkeypatch.setattr(warehouse, "local_connection", lambda state: nullcontext(None))
    monkeypatch.setattr(evaluate, "label", label)
    assert run.main() == 0
    assert destinations == [scratch / "results"]
    assert list(output.glob("*.csv")) == []
    assert {path.name for path in output.iterdir()} == (
        {"labels.json"} if action == "label" else set()
    )
    assert list((scratch / "results").glob("*.csv"))
