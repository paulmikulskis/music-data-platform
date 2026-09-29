"""The scheduled daily selector refuses stale age bands on either warehouse engine."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from identity_harness import Warehouse

pytestmark = pytest.mark.docker
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("engine", ["ci", "pg_local"])
def test_scheduled_daily_selection_checks_age_expiry(tmp_path, engine):
    warehouse = None
    if engine == "pg_local":
        admin = os.environ.get("MDP_CONTROL_ADMIN_URL")
        if not admin:
            pytest.skip("Set MDP_CONTROL_ADMIN_URL to a disposable Postgres server.")
        warehouse = Warehouse(admin, tmp_path)
    profiles = warehouse.profiles if warehouse else ROOT / "dbt/profiles"
    target = tmp_path / "target"
    env = {
        **os.environ,
        "MDP_CI_DB": str(tmp_path / "age.duckdb"),
        "DBT_MDP_SCOPE": "global",
    }

    def run(command, selection, year):
        result = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                "dbt",
                "dbt",
                command,
                "--project-dir",
                "dbt",
                "--profiles-dir",
                str(profiles),
                "--target",
                engine,
                "--target-path",
                str(target),
                "--log-path",
                str(tmp_path / "logs"),
                "--select",
                selection,
                "--indirect-selection",
                "cautious",
                "--vars",
                json.dumps(
                    {
                        "dry_run": True,
                        "cycle_opened_at": f"{year}-01-01 00:00:00",
                    }
                ),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        print(result.stdout)
        return result

    try:
        seeded = run("seed", "song_age_parameters", 2026)
        assert seeded.returncode == 0, seeded.stdout + seeded.stderr
        # Intersect with the actual scheduler selector: selecting this test by name alone
        # would miss a regression that removes it from scheduled daily builds.
        selection = "selector:daily_global_transform,song_age_parameters_current"
        for year, status, failures in [(2026, "pass", 0), (2027, "fail", 5)]:
            result = run("build", selection, year)
            assert result.returncode == (0 if status == "pass" else 1), (
                result.stdout + result.stderr
            )
            results = json.loads((target / "run_results.json").read_text())["results"]
            guard = [
                r
                for r in results
                if r["unique_id"].endswith(".song_age_parameters_current")
            ]
            assert len(guard) == 1, (
                "The scheduled guard is missing. Add it to daily_global_transform."
            )
            assert guard[0]["status"] == status
            assert guard[0]["failures"] == failures
        manifest = json.loads((target / "manifest.json").read_text())
        guard = manifest["nodes"][
            "test.music_data_platform.song_age_parameters_current"
        ]
        assert (
            "model.music_data_platform.int_song_age__daily"
            in guard["depends_on"]["nodes"]
        )
    finally:
        if warehouse:
            warehouse.drop()
