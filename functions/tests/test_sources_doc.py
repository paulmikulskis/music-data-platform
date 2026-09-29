"""docs/sources.md is current, and its annotations cover exactly the sources code knows."""

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from mdp_functions.sources_doc import ANNOTATIONS, COLUMNS, OUTPUT, RIGHTS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = """
import json, sys
from pathlib import Path
from mdp_functions.sources_doc import check
print(json.dumps(check(Path(sys.argv[1]))))
"""


def problems(root: Path) -> list[str]:
    """The check in a fresh interpreter: fixture-mode probes registered by other tests stay out."""
    env = {k: v for k, v in os.environ.items() if k != "MDP_FIXTURE_MODE"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            SCRIPT,
            str(root),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def copy(tmp_path: Path) -> Path:
    for path in (ANNOTATIONS, RIGHTS, OUTPUT):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / path, tmp_path / path)
    return tmp_path


def rewrite(root: Path, change) -> None:
    path = root / ANNOTATIONS
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(change(rows))


def test_doc_is_current():
    assert problems(ROOT) == []


def test_a_registered_source_needs_an_annotation(tmp_path):
    root = copy(tmp_path)
    rewrite(
        root, lambda rows: [r for r in rows if r["source_key"] != "billboard_hot100"]
    )
    assert any(p.startswith("billboard_hot100: no row") for p in problems(root))


def test_an_unknown_source_must_be_planned(tmp_path):
    root = copy(tmp_path)
    ghost = {c: "" for c in COLUMNS} | {
        "source_key": "ghost_feed",
        "role": "core product",
        "method": "public page",
        "speed_breadth": "not measured",
        "market_edge": "commodity: a made up feed that nobody collects",
        "api": "`ghost.example.com`",
    }
    rewrite(root, lambda rows: [*rows, ghost])
    assert any(
        p.startswith("ghost_feed: not a registered function") for p in problems(root)
    )


def test_a_planned_row_for_a_registered_source_fails(tmp_path):
    root = copy(tmp_path)

    def plan_sp_playlist(rows):
        for r in rows:
            if r["source_key"] == "sp_playlist":
                r.update(
                    {
                        "status": "planned",
                        "class": "bronze",
                        "scope": "global",
                        "refresh": "hourly",
                    }
                )
        return rows

    rewrite(root, plan_sp_playlist)
    assert any(
        p.startswith("sp_playlist: code or the rights registry now knows it")
        for p in problems(root)
    )


def test_the_api_cell_names_every_declared_host(tmp_path):
    root = copy(tmp_path)

    def drop_host(rows):
        for r in rows:
            if r["source_key"] == "am_playlist":
                r["api"] = "Apple playlist pages"
        return rows

    rewrite(root, drop_host)
    assert any(
        p.startswith("am_playlist: api must name its declared hosts")
        for p in problems(root)
    )


def test_a_stale_doc_fails(tmp_path):
    root = copy(tmp_path)
    (root / OUTPUT).write_text((root / OUTPUT).read_text() + "\nhand edit\n")
    assert problems(root) == [
        f"{OUTPUT} is stale: run `uv run --project functions mdp sources doc`"
    ]
