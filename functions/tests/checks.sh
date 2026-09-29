#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source ops/local/init.sh
export MDP_CONTROL_URL="$MDP_FUNCTIONS_RT_DATABASE_URL"
export MDP_WAREHOUSE_URL="${MDP_LOADER_WH_DATABASE_URL%/control*}/warehouse?sslmode=prefer"
export MDP_SERVICE_READ_URL="${MDP_SERVICE_READ_DATABASE_URL%/control*}/warehouse?sslmode=prefer"
uv run --project functions ruff check functions
uv run --project functions python -m mdp_functions.source_lint
uv run --project functions pytest functions/tests -q "$@"
if [[ "${MDP_PG_PORT:-5433}" == "5435" ]]; then
  uv run --project functions pytest functions/tests/prove_udf.py -q
fi
