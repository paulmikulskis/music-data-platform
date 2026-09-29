"""Only catalog-owned messages are safe to print beside private capture errors."""

import json
from pathlib import Path


class BacktestError(ValueError):
    def __init__(self, code):
        catalog = (
            Path(__file__).resolve().parents[2]
            / "functions/src/mdp_functions/error_catalog.json"
        )
        hint = json.loads(catalog.read_text())[code]
        super().__init__(hint["summary"] + " " + hint["next_step"])
