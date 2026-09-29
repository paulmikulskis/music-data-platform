"""aggregation; every leg runs even after an earlier failure."""

import argparse
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from harness import Harness

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--local",
    action="store_true",
    help="allow MISSING deploy-only preflight inputs; never suppress FAIL",
)
parser.add_argument(
    "--only-lifecycle",
    action="store_true",
    help="run only lifecycle acceptance; full aggregate remains a separate gate",
)
args = parser.parse_args()
args.target = os.environ.get("MDP_ACCEPT_TARGET", "pg_local")
h = Harness(args)
path = Path("ops/evidence/platform") / (
    "accept-platform-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".txt"
)
path.parent.mkdir(parents=True, exist_ok=True)
failed = []
with path.open("x") as h.log:
    h.note(
        "acceptance; local="
        + str(args.local)
        + "; scope="
        + ("lifecycle-only" if args.only_lifecycle else "all")
    )
    legs = [
        ("preflight", ["ops/preflight.sh", "--stage", "0"]),
        ("lint-dbt", ["ops/ci/lint-dbt.sh"]),
        ("typecheck", ["pnpm", "--dir", "control", "typecheck"]),
        (
            "pytest",
            ["uv", "run", "--project", "functions", "pytest", "functions/tests", "-q"],
        ),
        (
            "ruff",
            [
                "uv",
                "run",
                "--project",
                "functions",
                "ruff",
                "check",
                "functions",
                "ops/ci/lifecycle",
            ],
        ),
        (
            "dbt-ci",
            [
                "uv",
                "run",
                "--project",
                "dbt",
                "dbt",
                "build",
                "--target",
                "ci",
                "--project-dir",
                "dbt",
                "--profiles-dir",
                "dbt/profiles",
            ],
        ),
        (
            "lifecycle",
            ["ops/ci/lifecycle.sh", "--target", args.target, "--case", "all"],
        ),
    ]
    if args.only_lifecycle:
        legs = [(name, command) for name, command in legs if name == "lifecycle"]
        h.note(
            "SKIP out-of-scope legs: preflight, lint-dbt, typecheck, pytest, ruff, dbt-ci; orchestrator runs the full aggregate separately"
        )
    for name, command in legs:
        print("ACCEPT running " + name, flush=True)
        try:
            env = {"MDP_FIXTURE_MODE": "0"} if name in ("pytest", "dbt-ci") else {}
            if name == "dbt-ci":
                env["MDP_CI_DB"] = str(Path(tempfile.mkdtemp()) / "ci.duckdb")
            result = h.command(command, ok=False, timeout=2400, env=env)
            passed = result.returncode == 0
            if name == "preflight" and args.local and not passed:
                lines = result.stdout.splitlines()
                required = {
                    "fly.token",
                    "fly.org",
                    "fly.region",
                    "r2",
                    "dbtcloud",
                    "secret_store",
                    "fixture_accounts",
                    "fixture_targets",
                    "billboard",
                    "budgets",
                    "github",
                }
                blockers = [
                    line
                    for line in lines
                    if line.startswith(("FAIL ", "MISSING "))
                    and (
                        line.split()[1].startswith("local.")
                        or (line.split()[1] in required and line.startswith("FAIL "))
                    )
                ]
                passed = (
                    any(line.startswith("SUMMARY suite=local ") for line in lines)
                    and not blockers
                )
                h.note(
                    "LOCAL preflight permits only MISSING deploy inputs; blockers="
                    + str(blockers)
                )
            if name == "lifecycle":
                for line in result.stdout.splitlines():
                    if line.startswith(("CASE ", "LIFECYCLE EVIDENCE ")):
                        print(line, flush=True)
            if not passed:
                failed.append(name)
                lines = (result.stdout + result.stderr).splitlines()
                error = next(
                    (
                        line.strip()
                        for line in lines
                        if (line.startswith("CASE ") and " FAIL " in line)
                        or any(
                            token in line
                            for token in (
                                "Error:",
                                "Error in",
                                "ERROR",
                                "FAILED ",
                                "missing dependent",
                                "I001",
                                "RUF012",
                                "S102",
                            )
                        )
                    ),
                    "command exited " + str(result.returncode),
                )
                h.note(f"FIRST ERROR {name}: {error}")
                print(h.clean(f"FIRST ERROR {name}: {error}"), flush=True)
            print(f"ACCEPT leg {name} {'PASS' if passed else 'FAIL'}", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep running independent acceptance legs
            h.note(str(exc))
            failed.append(name)
            print("ACCEPT leg " + name + " FAIL", flush=True)
    h.note("FAILED legs: " + ", ".join(failed))
    final = "ACCEPT platform " + ("FAIL" if failed else "PASS")
    h.note(final)
summary = Path("ops/evidence/platform/lifecycle-report.txt")
suite_summary = summary.read_text() if summary.exists() else ""
suite_summary = suite_summary.split("Latest dated acceptance:", 1)[0]
summary.write_text(
    suite_summary
    + f"Latest dated acceptance: {path}; scope={'lifecycle-only' if args.only_lifecycle else 'all'}\n{final}\n"
)
print("EVIDENCE " + str(path), flush=True)
print(final, flush=True)
raise SystemExit(bool(failed))
