#!/usr/bin/env bash
# Integration acceptance; offline unit-only runs cannot satisfy this gate.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${MDP_SERVICE_URL:?Set the isolated functions service URL}"
: "${MDP_SERVICE_TOKEN:?Set the service token in the environment}"
: "${MDP_CONTROL_RT_URL:?Set the control_rt connection URL}"
export MDP_CONTROL_INTEGRATION="${MDP_CONTROL_INTEGRATION:-1}"
[[ "$MDP_CONTROL_INTEGRATION" == 1 ]] || { echo 'ACCEPT_CONTROL FAIL integration mode is required'; exit 1; }
# The documented local invocation needs only control/service inputs. Derive the
# two local test-role URLs exclusively for the isolated loopback stack.
if [[ "$MDP_CONTROL_RT_URL" == *"@127.0.0.1:5433/control"* ]]; then
  export MDP_WAREHOUSE_TEST_URL="${MDP_WAREHOUSE_TEST_URL:-postgresql://dbt_transform:dbt_transform@127.0.0.1:5433/warehouse?sslmode=prefer}"
  export MDP_READER_URL="${MDP_READER_URL:-postgresql://reader_wh:reader_wh@127.0.0.1:5433/warehouse?sslmode=prefer}"
  export MDP_FUNCTIONS_TEST_URL="${MDP_FUNCTIONS_TEST_URL:-postgresql://functions_rt:functions_rt@127.0.0.1:5433/control?sslmode=prefer}"
  export MDP_AUTH_MODE="${MDP_AUTH_MODE:-dev}"
fi
: "${MDP_WAREHOUSE_TEST_URL:?Set the isolated warehouse test-owner URL}"
: "${MDP_READER_URL:?Set the isolated warehouse reader URL}"
: "${MDP_FUNCTIONS_TEST_URL:?Set the isolated alert producer URL}"
ts=$(date -u +%Y%m%dT%H%M%SZ)
report="ops/evidence/control/accept-control-${ts}.txt"
mkdir -p ops/evidence/control
exec > >(sed "s|$PWD|<repo>|g" | tee "$report") 2>&1
trap 'accept_status=$?; if [[ "$accept_status" != 0 ]]; then printf "ACCEPT_CONTROL FAIL evidence=%s\n" "$report"; fi' EXIT
printf 'acceptance %s\n' "$ts"
pnpm --dir control install --frozen-lockfile
pnpm --dir control typecheck
pnpm --dir control test
uv run --project functions pytest functions/tests/test_snowflake_udf_shape.py functions/udf/snowflake/test_behavior.py control/scripts/test_local_budget_seed.py control/scripts/test_fixture_scenario.py control/scripts/test_preview_cursor.py -q
pnpm --dir control exec tsx scripts/walk-control.ts
printf 'ACCEPT_CONTROL PASS evidence=%s\n' "$report"
