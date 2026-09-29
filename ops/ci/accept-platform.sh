#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ " ${*} " == *" --deployed "* ]]; then exec bash ops/ci/accept-platform-deployed.sh "$@"; fi
exec uv run --project functions python ops/ci/lifecycle/accept.py "$@"
