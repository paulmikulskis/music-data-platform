"""Keep extracted CI jobs runnable through the same script locally."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
# Other jobs keep their current setup until their local service contracts are extracted.
EXTRACTED = {"conformance": ("contracts",)}

# Preserve the checks from conformance.yml before extraction. The two comparisons
# replace its git diff so the job also works in a disposable, non-Git copy.
INSTALL = ["pnpm", "--dir", "control", "install", "--frozen-lockfile"]
SERVICE = "functions/openapi/service.json"
CONTROL = "control/packages/contracts/openapi/control-api.json"
CHECKS = [
    ["uv", "run", "--project", "functions", "mdp", "openapi", "export"],
    ["pnpm", "--dir", "control", "--filter", "@mdp/contracts", "openapi"],
    ["cmp", "<snapshot>/service.json", SERVICE],
    ["cmp", "<snapshot>/control-api.json", CONTROL],
    [
        "pnpm",
        "--dir",
        "control",
        "exec",
        "vitest",
        "run",
        "packages/contracts/test/conformance.test.ts",
    ],
]


@pytest.fixture
def conformance_job(tmp_path):
    for name in (SERVICE, CONTROL):
        path = tmp_path / name
        path.parent.mkdir(parents=True)
        path.write_text("{}\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    stub = (
        f"#!{sys.executable}\n"
        + """
import json
import os
import subprocess
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
call = [tool, *sys.argv[1:]]
if tool == "cmp":
    call[1] = "<snapshot>/" + Path(call[1]).name
with open(os.environ["CALLS"], "a") as output:
    output.write(json.dumps(call) + "\\n")
if call == json.loads(os.environ["FAIL_CALL"]):
    sys.exit(23)
if os.environ["DRIFT"] and (
    (tool == "uv" and os.environ["DRIFT"].startswith("functions/"))
    or (tool == "pnpm" and call[-1] == "openapi"
        and os.environ["DRIFT"].startswith("control/"))
):
    Path(os.environ["DRIFT"]).write_text('{"changed": true}\\n')
if tool == "cmp":
    sys.exit(subprocess.call([os.environ["REAL_CMP"], *sys.argv[1:]]))
"""
    )
    for tool in ("uv", "pnpm", "cmp"):
        path = binaries / tool
        path.write_text(stub)
        path.chmod(0o755)

    def execute(*args, fail=None, drift=""):
        calls = tmp_path / "calls.jsonl"
        calls.write_text("")
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("MDP_")
        }
        env.update(
            PATH=f"{binaries}{os.pathsep}{os.environ['PATH']}",
            CALLS=str(calls),
            FAIL_CALL=json.dumps(fail),
            DRIFT=drift,
            REAL_CMP=shutil.which("cmp"),
        )
        result = subprocess.run(
            ["bash", str(ROOT / "ops/ci/jobs/conformance-contracts.sh"), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, [json.loads(line) for line in calls.read_text().splitlines()]

    return execute


def test_conformance_installs_locked_dependencies(conformance_job):
    result, calls = conformance_job("install")
    assert result.returncode == 0, result.stderr
    assert calls == [INSTALL]


def test_conformance_install_failure_stops_the_job(conformance_job):
    result, calls = conformance_job("install", fail=INSTALL)
    assert result.returncode != 0
    assert calls == [INSTALL]


def test_conformance_executes_every_check_in_order(conformance_job):
    result, calls = conformance_job()
    assert result.returncode == 0, result.stderr
    assert calls == CHECKS


@pytest.mark.parametrize("index", range(len(CHECKS)))
def test_conformance_stops_at_each_failed_check(conformance_job, index):
    result, calls = conformance_job(fail=CHECKS[index])
    assert result.returncode != 0
    assert calls == CHECKS[: index + 1]


@pytest.mark.parametrize("path,index", [(SERVICE, 2), (CONTROL, 3)])
def test_conformance_rejects_each_stale_document(conformance_job, path, index):
    result, calls = conformance_job(drift=path)
    assert result.returncode != 0
    assert calls == CHECKS[: index + 1]
    assert "Run uv run --project functions mdp openapi export" in result.stderr


def test_local_ready_selection():
    subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "ops",
            "-p",
            "test_ready.py",
        ],
        cwd=ROOT,
        check=True,
    )


def test_extracted_jobs_have_only_script_run_steps():
    for workflow, jobs in EXTRACTED.items():
        document = yaml.safe_load(
            (ROOT / f".github/workflows/{workflow}.yml").read_text()
        )
        for job in jobs:
            script = f"ops/ci/jobs/{workflow}-{job}.sh"
            steps = document["jobs"][job]["steps"]
            runs = [step["run"] for step in steps if "run" in step]
            assert runs == [f"bash {script} install", f"bash {script}"], (
                f"{workflow}/{job}: keep installation followed by the job checks"
            )
            for command in runs:
                assert "\n" not in command, f"Move inline commands to {script}"
                words = shlex.split(command)
                assert words[:2] == ["bash", script], f"Call bash {script}"
                assert words[2:] in ([], ["install"]), (
                    f"Use a declared step in {script}"
                )
            subprocess.run(["bash", "-n", script], cwd=ROOT, check=True)
