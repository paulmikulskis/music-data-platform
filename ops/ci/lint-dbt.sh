#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "$0")/../.." && pwd)
export MDP_LINT_ROOT=$repo_root
export MDP_LINT_DBT_ROOT=${MDP_LINT_DBT_ROOT:-$repo_root/dbt}
verbose=0
args=()
for arg in "$@"; do
  if [[ "$arg" == "-v" || "$arg" == "--verbose" ]]; then verbose=1; else args+=("$arg"); fi
done
log=$(mktemp)
errors=$(mktemp)
trap 'rm -f "$log" "$errors"' EXIT
if {
  uv run --project "$repo_root/dbt" python "$repo_root/ops/ci/dbt_relations.py" "$MDP_LINT_DBT_ROOT" &&
  uv run --project "$repo_root/dbt" python "$repo_root/ops/ci/lint_dbt.py" "${args[@]+"${args[@]}"}" &&
  uv run --project "$repo_root/functions" python "$repo_root/ops/ci/privacy.py" --compile
} >"$log" 2>"$errors"; then
  if [[ "$verbose" == 1 ]]; then cat "$log" "$errors"; else echo 'PASS dbt lint (parse, relations, descriptions, graph, selectors and privacy)'; fi
else
  cat "$log"
  cat "$errors" >&2
  exit 1
fi
