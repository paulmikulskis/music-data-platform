#!/usr/bin/env bash
# Read committed method choices; publish only reports.
set -euo pipefail
if [ "$#" -lt 1 ]; then
  echo 'Choose a report directory. Run bash ops/backtest/weekly.sh /tmp/backtest-report.' >&2
  exit 2
fi
report_dir=$(realpath -m "$1")
shift
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo_root"
exec nice -n 10 uv run --project functions python ops/backtest/run.py weekly --output "$report_dir" "$@"
