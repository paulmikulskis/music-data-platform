#!/usr/bin/env bash
# Run from any directory. Python's standard library owns selection and reporting.
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_root"
exec python3 ops/ready.py "$@"
