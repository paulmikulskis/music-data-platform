"""Run the merge gate with local git/gh stand-ins; no remote is changed."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SHA = "a" * 40
URL = "https://github.com/paulmikulskis/music-data-platform/pull/123"


@pytest.fixture
def gate(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "ops").mkdir(parents=True)
    for name in ("merge-gate.sh", "ci-wait.sh", "ci_wait.py"):
        shutil.copy(ROOT / "ops" / name, checkout / "ops" / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
tool = pathlib.Path(sys.argv[0]).name
root = pathlib.Path(os.environ["STUB_ROOT"])
mode = os.environ["STUB_MODE"]
sha, base = "a" * 40, "b" * 40
url = "https://github.com/paulmikulskis/music-data-platform/pull/123"
with (root / "calls").open("a") as log:
    log.write(json.dumps([tool, *args]) + "\\n")
if tool == "sleep":
    assert args == ["3"]
elif tool == "git":
    if args[:2] == ["branch", "--show-current"]: print("topic")
    elif args[0] == "status":
        dirty = mode == "dirty" or (mode == "dirty-after-wait" and (root / "waited").exists())
        print(" M changed.py" if dirty else "", end="")
    elif args[0] == "rev-parse": print(sha)
    elif args[0] == "push" and mode == "push-fails": sys.exit(1)
    elif args[0] not in ("fetch", "push", "check-ref-format"):
        raise AssertionError(args)
elif args[:2] == ["pr", "list"]:
    if mode == "reuse": print(url)
elif args[:2] == ["pr", "create"]:
    assert "--draft" in args and args[args.index("--base") + 1] == "main"
    body = pathlib.Path(args[args.index("--body-file") + 1]).read_text()
    assert "Checks tab" in body
    print(url)
elif args[:2] == ["pr", "view"]:
    if "headRefOid,baseRefOid" in args:
        stale = mode == "stale-head" and not (root / "head-read").exists()
        (root / "head-read").write_text("yes")
        print((base if mode == "wrong-head" or stale else sha) + "\\t" + base)
    else: print(base if mode == "moved" else sha)
elif args[0] == "api":
    (root / "waited").write_text("yes")
    assert sha in args[-1] and "--paginate" in args and "--slurp" in args
    runs = "actions/runs?" in args[-1]
    names = ["conformance", "control-ci", "dbt-ci", "functions-ci", "generated-drift"]
    if not runs: names.append("showcase-artifacts-postgres")
    if mode == "missing": names.remove("functions-ci")
    rows = [{"name": name, "head_sha": sha, "status": "completed",
             "conclusion": "success", "html_url": url} for name in names]
    if not runs and mode in ("failure", "skipped"):
        rows[-1]["conclusion"] = mode
    print(json.dumps([{"workflow_runs" if runs else "check_runs": rows}]))
else: raise AssertionError(args)
'''
    for name in ("gh", "git", "sleep"):
        path = bin_dir / name
        path.write_text(stub)
        path.chmod(0o755)

    def run(mode="green", branch="topic"):
        result = subprocess.run(
            ["bash", str(checkout / "ops/merge-gate.sh"), branch],
            env=os.environ | {
                "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                "GITHUB_REPO": "paulmikulskis/music-data-platform",
                "STUB_ROOT": str(tmp_path), "STUB_MODE": mode,
                "MDP_CI_WAIT_TIMEOUT_S": "0", "MDP_CI_WAIT_POLL_S": "0",
            },
            text=True, capture_output=True, timeout=10, check=False,
        )
        calls = tmp_path / "calls"
        return result, [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    return run


@pytest.mark.parametrize("mode", ["green", "reuse"])
def test_pushes_pinned_head_and_checks_draft_without_merging(gate, mode):
    result, calls = gate(mode)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ["git", "push", "origin", f"{SHA}:refs/heads/topic"] in calls
    assert calls.count(["git", "status", "--porcelain"]) == 2
    assert any(call[:3] == ["gh", "pr", "create"] for call in calls) == (mode == "green")
    assert not any("merge" in call for call in calls)
    assert URL in result.stdout
    assert "Next: review" in result.stdout


def test_waits_for_the_pushed_pr_head(gate):
    result, calls = gate("stale-head")
    assert result.returncode == 0, result.stdout + result.stderr
    reads = [i for i, call in enumerate(calls) if "headRefOid,baseRefOid" in call]
    assert len(reads) == 2
    assert calls[reads[0] + 1] == ["sleep", "3"]
    assert next(i for i, call in enumerate(calls) if call[:2] == ["gh", "api"]) > reads[1]
    assert "Next: review" in result.stdout


@pytest.mark.parametrize("mode", ["failure", "skipped", "missing", "moved", "wrong-head", "push-fails", "dirty-after-wait"])
def test_refuses_failed_missing_or_changed_checks(gate, mode):
    result, calls = gate(mode)
    assert result.returncode != 0
    assert "Next: review" not in result.stdout
    assert "ops/merge-gate.sh <branch>" in result.stderr
    assert not any("merge" in call for call in calls)
    if mode == "wrong-head":
        assert calls.count(["sleep", "3"]) == 20
        assert not any(call[:2] == ["gh", "api"] for call in calls)
    if mode == "dirty-after-wait":
        assert calls[-1] == ["git", "status", "--porcelain"]
        assert "checkout changed while checks ran" in result.stderr


@pytest.mark.parametrize("mode,branch", [("dirty", "topic"), ("green", "main"), ("green", "elsewhere")])
def test_refuses_before_pushing(gate, mode, branch):
    result, calls = gate(mode, branch)
    assert result.returncode != 0
    assert not any(call[:2] == ["git", "push"] for call in calls)
