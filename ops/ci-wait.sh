#!/usr/bin/env bash
# Wait for the checks on one immutable revision. See ops/CLAUDE.md.
set -euo pipefail
exec python3 "$(dirname "$0")/ci_wait.py" "$@"
