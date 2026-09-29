#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec uv run --project functions python ops/ci/lifecycle/harness.py "$@"
