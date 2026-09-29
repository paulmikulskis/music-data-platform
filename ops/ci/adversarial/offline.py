"""Run parser/contract fixtures in disposable projects; never edit the live dbt graph."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def execute(case, directory):
    with tempfile.TemporaryDirectory(prefix="mdp-adversarial-") as temp:
        root = Path(temp)
        project = root / "dbt"
        models = project / "models"
        models.mkdir(parents=True)
        # This tiny project has no playlist marts. Supply the description guard's
        # contract input so the fixture reaches the rule it is meant to exercise.
        playlist = models / "marts/global/playlist.yml"
        playlist.parent.mkdir(parents=True)
        playlist.write_text("version: 2\nmodels: []\n")
        (project / "macros").mkdir()
        (project / "dbt_project.yml").write_text(
            'name: adversarial\nversion: "1.0.0"\nconfig-version: 2\nprofile: music_data_platform\n'
            "model-paths: [models]\nmacro-paths: [macros]\n"
        )
        (project / "macros/invoke.sql").write_text(
            "{% macro mdp_invoke(source) %}select 1 as receipt{% endmacro %}\n"
        )
        for fixture in directory.glob("adversarial_*.sql"):
            shutil.copy(fixture, models / fixture.name)
        if (directory / "contract.yml").exists():
            shutil.copy(directory / "contract.yml", models / "contract.yml")
        env = dict(
            os.environ,
            MDP_CI_DB=str(root / "ci.duckdb"),
            DBT_TARGET_PATH=str(root / "target"),
            DBT_LOG_PATH=str(root / "logs"),
            MDP_LINT_DBT_ROOT=str(project),
            MDP_LINT_RULES_ONLY="1",
            DBT_USE_COLORS="false",
            NO_COLOR="1",
        )
        command = ["uv", "run", "--project", str(ROOT / "dbt"), "dbt"]
        flags = [
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(ROOT / "dbt/profiles"),
            "--target",
            "ci",
            "--no-partial-parse",
        ]

        def run(argv):
            result = subprocess.run(
                argv, env=env, capture_output=True, text=True, timeout=180, check=False
            )
            print(result.stdout + result.stderr, end="")
            return result

        if case["kind"] == "registration":
            package = root / "functions/src/mdp_functions"
            package.parent.mkdir(parents=True)
            shutil.copytree(
                ROOT / "functions/src/mdp_functions",
                package,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            source = package / "sources/adversarial_gold"
            source.mkdir()
            shutil.copy(directory / "adversarial_gold.py", source / "function.py")
            env["PYTHONPATH"] = str(package.parent)
            result = run(
                [
                    sys.executable,
                    "-c",
                    "from mdp_functions.cli import app; app()",
                    "sources",
                    "export",
                ]
            )
            assert result.returncode != 0, (
                "invalid gold was accepted by mdp sources export"
            )
        elif case["kind"] == "lint":
            result = run([str(ROOT / "ops/ci/lint-dbt.sh"), "ci", "-v"])
            assert result.returncode != 0, "invalid model passed CI lint"
            if case["target"] == "tenant":
                shutil.copy(
                    directory / "passing.sql", models / "adversarial_tenant.sql"
                )
                shutil.copy(directory / "passing.yml", models / "contract.yml")
                assert run([str(ROOT / "ops/ci/lint-dbt.sh"), "ci", "-v"]).returncode == 0, (
                    "tenant column variant failed lint"
                )
                assert run([*command, "build", *flags]).returncode == 0, (
                    "tenant column contract failed build"
                )
        elif case["target"] == "circular":
            result = run([str(ROOT / "ops/ci/lint-dbt.sh"), "ci", "-v"])
            output = result.stdout + result.stderr
            assert result.returncode != 0, "CI lint accepted a cyclic graph"
            assert all(
                name in output
                for name in (
                    "Found a cycle",
                    "adversarial_cycle_a",
                    "adversarial_cycle_b",
                )
            ), "CI graph detector must name both models in the cycle"
            print("dbt-failure: acyclic-dbt-graph rejected the cycle")
            (models / "adversarial_cycle_b.sql").write_text(
                "{{ config(materialized='table', tags=['cadence:hourly','scope:global']) }}\n"
                "select 1 as value\n"
            )
            assert run([str(ROOT / "ops/ci/lint-dbt.sh"), "ci", "-v"]).returncode == 0, (
                "CI lint refused the repaired acyclic graph"
            )
        else:
            result = run([*command, "build", *flags])
            assert result.returncode != 0, "invalid contract was accepted"
