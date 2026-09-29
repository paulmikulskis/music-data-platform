#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
exec uv run --project "$root/functions" python "$root/ops/fly/postgres/analyst-account.py" remove "$@"
