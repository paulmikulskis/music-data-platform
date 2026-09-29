"""Wait for every check and the expected workflows on a pinned commit."""

import argparse
import json
import os
import re
import subprocess
import sys
import time

REPO = os.environ.get("GITHUB_REPO", "")
EXPECTED_WORKFLOWS = {"conformance", "control-ci", "dbt-ci", "functions-ci", "generated-drift"}
# Require this separately: a green sibling job cannot stand in for it.
EXPECTED_CHECKS = {"showcase-artifacts-postgres"}


# A skipped or neutral row neither passed nor failed (the scheduled cadence-watch job is gated off by a
# repository variable), so it is left out: it does not block, and it never stands in for a required
# workflow. Every other conclusion, and a completed row without one, still blocks.
IGNORED = {"skipped", "neutral"}


def api(path, key):
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{REPO}/{path}"],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return [row for page in json.loads(result.stdout) for row in page[key]]


def snapshot(sha):
    # No truncated list may count as green. A failed page invalidates the whole read.
    runs = api(f"actions/runs?head_sha={sha}&per_page=100", "workflow_runs")
    checks = api(f"commits/{sha}/check-runs?filter=latest&per_page=100", "check_runs")
    runs = [run for run in runs if run["head_sha"] == sha and run["conclusion"] not in IGNORED]
    for check in checks:
        print(f"CHECK {check['name']} {check['status']} {check['conclusion'] or '-'}; "
              f"open {check['html_url']}", flush=True)
    checks = [check for check in checks if check["conclusion"] not in IGNORED]
    missing = EXPECTED_WORKFLOWS - {run["name"] for run in runs}
    if missing:
        print(f"WAIT missing workflows: {', '.join(sorted(missing))}; "
              f"open https://github.com/{REPO}/actions", flush=True)
    missing_checks = EXPECTED_CHECKS - {check["name"] for check in checks}
    if missing_checks:
        print(f"WAIT missing checks: {', '.join(sorted(missing_checks))}; "
              f"open https://github.com/{REPO}/actions", flush=True)
    if any(row["status"] == "completed" and row["conclusion"] != "success" for row in checks + runs):
        return False
    if missing or missing_checks or any(row["status"] != "completed" for row in checks + runs):
        return None
    return all(row["conclusion"] == "success" for row in checks + runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha", help="full commit SHA to check")
    parser.add_argument("--pr-base", help="full PR base SHA; both modes require all checks")
    args = parser.parse_args()
    if not all(re.fullmatch(r"[0-9a-fA-F]{40}", value) for value in (args.sha, args.pr_base) if value):
        print("Pass full commit SHAs: ops/ci-wait.sh <sha> [--pr-base <sha>]", file=sys.stderr)
        return 2
    sha = args.sha.lower()
    try:
        print(f"REQUIRED workflows: {', '.join(sorted(EXPECTED_WORKFLOWS))}; "
              f"checks: {', '.join(sorted(EXPECTED_CHECKS))}; "
              f"open https://github.com/{REPO}/actions", flush=True)
        limit = float(os.environ.get("MDP_CI_WAIT_TIMEOUT_S", "3600"))
        interval = float(os.environ.get("MDP_CI_WAIT_POLL_S", "30"))
        if limit < 0 or interval < 0:
            raise ValueError("negative wait")
    except ValueError:
        print("FAIL cannot prepare CI wait; check the SHA and MDP_CI_WAIT_* settings, then rerun ops/ci-wait.sh <sha>")
        return 2
    deadline = time.monotonic() + limit
    while True:
        try:
            result = snapshot(sha)
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            result = None
            print("WAIT checks unavailable; run gh auth status, then retry ops/ci-wait.sh <sha>", flush=True)
        if result is not None:
            print(f"CI_GREEN; review https://github.com/{REPO}/commit/{sha}" if result else
                  f"CI_NOT_GREEN; fix the failed checks at https://github.com/{REPO}/actions", flush=True)
            return 0 if result else 1
        if time.monotonic() >= deadline:
            print(f"CI_TIMEOUT; inspect https://github.com/{REPO}/actions and rerun ops/ci-wait.sh {sha}")
            return 2
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    sys.exit(main())
