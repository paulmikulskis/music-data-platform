"""Exercise the CI waiter with local gh commands and no network."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[2]
SHA = "a" * 40
REQUIRED_CHECK = "showcase-artifacts-postgres"
NAMES = ["conformance", "control-ci", "dbt-ci", "functions-ci", "generated-drift"]


def run_wait(tmp_path, states, *, timeout="0", pr_base=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text(
        f"#!{sys.executable}\nraise AssertionError('CI requirements must not read Git history')\n"
    )
    (bin_dir / "gh").write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
root = pathlib.Path(os.environ["STUB_ROOT"])
with (root / "calls").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
assert "--paginate" in sys.argv and "--slurp" in sys.argv
index = int((root / "index").read_text()) if (root / "index").exists() else 0
states = json.loads((root / "states").read_text())
state = states[min(index, len(states)-1)]
if "actions/runs?" in sys.argv[-1]:
    (root / "index").write_text(str(index+1))
    (root / "current").write_text(json.dumps(state))
else:
    state = json.loads((root / "current").read_text())
if state == "error":
    print("synthetic gh error", file=sys.stderr)
    sys.exit(1)
kind = "workflow_runs" if "actions/runs?" in sys.argv[-1] else "check_runs"
print(json.dumps([{kind: state[kind][:2]}, {kind: state[kind][2:]}]))
''')
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    (tmp_path / "states").write_text(json.dumps(states))
    env = os.environ | {
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "STUB_ROOT": str(tmp_path),
        "MDP_CI_WAIT_TIMEOUT_S": timeout, "MDP_CI_WAIT_POLL_S": "0",
    }
    return subprocess.run(["bash", str(ROOT / "ops/ci-wait.sh"), SHA, *(["--pr-base", pr_base] if pr_base else [])],
                          env=env, text=True, capture_output=True, timeout=10, check=False)


def green(names=NAMES):
    rows = [{"name": name, "status": "completed", "conclusion": "success",
             "head_sha": SHA, "html_url": "https://example.invalid/check"} for name in names]
    return {"workflow_runs": rows, "check_runs": [dict(row) for row in rows] + [{
        "name": REQUIRED_CHECK, "status": "completed", "conclusion": "success",
        "head_sha": SHA, "html_url": "https://example.invalid/artifacts",
    }]}


@pytest.mark.parametrize("pr_base", [None, "b" * 40])
@pytest.mark.parametrize("state,code", [
    (green(), 0),
    ("error", 2),
    ({"workflow_runs": [], "check_runs": []}, 2),
    (green(NAMES[:-1]), 2),
])
def test_terminal_states(tmp_path, state, code, pr_base):
    result = run_wait(tmp_path, [state], pr_base=pr_base)
    assert result.returncode == code, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text()
    assert SHA in calls
    assert "--paginate" in calls
    if code == 0:
        assert result.stdout.count("CHECK ") == len(NAMES) + 1


# Skipped and neutral rows are ignored (test_deploy_resilience.py covers them); every other conclusion blocks.
@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"])
def test_completed_non_success_refuses_deploy(tmp_path, conclusion):
    state = green()
    state["check_runs"][-1]["conclusion"] = conclusion
    result = run_wait(tmp_path, [state])
    assert result.returncode == 1
    assert "CI_NOT_GREEN" in result.stdout


@pytest.mark.parametrize("first", ["error", "pending", "empty"])
def test_waits_for_a_complete_read(tmp_path, first):
    state = green()
    if first == "error":
        state = "error"
    elif first == "empty":
        state = {"workflow_runs": [], "check_runs": []}
    else:
        state["check_runs"].append({"name": "extra check", "status": "in_progress",
                                   "conclusion": None, "html_url": "https://example.invalid/pending"})
    result = run_wait(tmp_path, [state, green()], timeout="5")
    assert result.returncode == 0, result.stdout
    assert int((tmp_path / "index").read_text()) == 2


@pytest.mark.parametrize("name", NAMES)
def test_workflows_run_without_path_filters(name):
    workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
    assert workflow[True]["pull_request"] is None
    assert workflow[True]["push"] == {"branches": ["main"]}


@pytest.mark.parametrize("pr_base", [None, "b" * 40])
@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("condition", ["missing", "skipped", "neutral", "running", "stale_sha"])
def test_every_workflow_must_pass_on_the_pinned_sha(tmp_path, pr_base, name, condition):
    state = green()
    run = next(row for row in state["workflow_runs"] if row["name"] == name)
    if condition == "missing":
        state["workflow_runs"].remove(run)
    elif condition == "running":
        run["status"] = "in_progress"
        run["conclusion"] = None
    elif condition == "stale_sha":
        run["head_sha"] = "c" * 40
    else:
        run["conclusion"] = condition
    result = run_wait(tmp_path, [state], pr_base=pr_base)
    assert result.returncode == 2
    assert "CI_GREEN" not in result.stdout


@pytest.mark.parametrize("pr_base", [None, "b" * 40])
@pytest.mark.parametrize("conclusion", ["missing", "skipped", "neutral", None, "failure"])
def test_postgres_artifact_job_must_pass(tmp_path, conclusion, pr_base):
    state = green()
    check = state["check_runs"][-1]
    if conclusion == "missing":
        state["check_runs"].pop()
    else:
        check["conclusion"] = conclusion
        check["status"] = "in_progress" if conclusion is None else "completed"
    result = run_wait(tmp_path, [state], pr_base=pr_base)
    assert result.returncode == (1 if conclusion == "failure" else 2)
    assert "CI_GREEN" not in result.stdout


def test_postgres_artifact_job_is_unconditional():
    workflow = yaml.safe_load((ROOT / ".github/workflows/generated-drift.yml").read_text())
    assert "if" not in workflow["jobs"][REQUIRED_CHECK]


def test_completed_failure_returns_without_waiting_for_other_jobs(tmp_path):
    state = green()
    state["check_runs"][0]["status"] = "in_progress"
    state["check_runs"][0]["conclusion"] = None
    state["check_runs"][-1]["conclusion"] = "failure"
    result = run_wait(tmp_path, [state])
    assert result.returncode == 1
    assert "CI_NOT_GREEN" in result.stdout
