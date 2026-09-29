#!/usr/bin/env bash
# Source from Bash or Zsh after up.sh. All credentials below are disposable fixtures.
# Port overrides for up.sh: MDP_LOCAL_{PG,FUNCTIONS,WORKBENCH,CONTROL,DATA}_PORT.
if [ -n "${ZSH_VERSION:-}" ]; then
  eval 'MDP_LOCAL_ROOT=${${(%):-%x}:A:h:h:h}'
else
  MDP_LOCAL_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
fi
. "$MDP_LOCAL_ROOT/ops/local/common.sh"
mdp_local_require docker || return 1
mdp_local_owned || return 1
mdp_local_binding=$(docker port "$MDP_LOCAL_CONTAINER" 5432/tcp) || return 1
case "$mdp_local_binding" in
  127.0.0.1:*) export MDP_PG_PORT=${mdp_local_binding##*:} ;;
  *) mdp_local_error 'Postgres must be running on loopback; run ops/local/up.sh'; return 1 ;;
esac
export PGCONNECT_TIMEOUT=3
export MDP_PG_HOST=127.0.0.1 MDP_PG_USER=dbt_transform MDP_PG_PASSWORD=dbt_transform
export MDP_PG_DB=warehouse MDP_PG_SCHEMA=dbt MDP_WB_USER=workbench_wh MDP_WB_PASSWORD=workbench_wh
mdp_local_address="127.0.0.1:$MDP_PG_PORT"
export MDP_CONTROL_DATABASE_URL="postgresql://migrator:migrator@$mdp_local_address/control?sslmode=require"
export MDP_CONTROL_URL="postgresql://functions_rt:functions_rt@$mdp_local_address/control?sslmode=require"
export MDP_CONTROL_RT_URL="postgresql://control_rt:control_rt@$mdp_local_address/control?sslmode=require"
export MDP_CONTROL_RT_DATABASE_URL="$MDP_CONTROL_RT_URL"
export MDP_WAREHOUSE_URL="postgresql://loader_wh:loader_wh@$mdp_local_address/warehouse?sslmode=require"
export MDP_SERVICE_READ_URL="postgresql://service_read:service_read@$mdp_local_address/warehouse?sslmode=require"
export MDP_WORKBENCH_WH_URL="postgresql://workbench_wh:workbench_wh@$mdp_local_address/warehouse?sslmode=require"
export MDP_READER_URL="postgresql://reader_wh:reader_wh@$mdp_local_address/warehouse?sslmode=require"
export MDP_API_KEY_READER_URL="postgresql://api_key_reader:api_key_reader@$mdp_local_address/control?sslmode=require"
export MDP_CONTROL_ADMIN_URL="postgresql://postgres:postgres@$mdp_local_address/control?sslmode=require"
export MDP_WAREHOUSE_ADMIN_URL="postgresql://postgres:postgres@$mdp_local_address/warehouse?sslmode=require"
export MDP_ANALYST_URL="postgresql://analyst_local:analyst_local@$mdp_local_address/warehouse?sslmode=require"
export MDP_WORKBENCH_ADMIN_URL="$MDP_WAREHOUSE_ADMIN_URL"
export MDP_SERVICE_URL="http://127.0.0.1:$MDP_LOCAL_FUNCTIONS_PORT"
export MDP_WORKBENCH_URL="http://127.0.0.1:$MDP_LOCAL_WORKBENCH_PORT"
export MDP_CONTROL_API_URL="http://127.0.0.1:$MDP_LOCAL_CONTROL_PORT"
export MDP_DATA_API_URL="http://127.0.0.1:$MDP_LOCAL_DATA_PORT"
export MDP_SERVICE_TOKEN=local-fixture-service MDP_DATA_API_KEY=local-fixture-reader
export MDP_CONTROL_API_KEY=local-fixture-promoter
export MDP_AUTH_MODE=dev MDP_FIXTURE=true MDP_DBT_CLOUD_VERIFY=false MDP_CORE_LAUNCHER=record
export MDP_DUMP_ROOT="file://$MDP_LOCAL_STATE/dumps" MDP_DEV_DB="$MDP_LOCAL_STATE/dev.duckdb"
export DBT_PROFILES_DIR="$MDP_LOCAL_ROOT/dbt/profiles" DBT_PROJECT_DIR="$MDP_LOCAL_ROOT/dbt"
export DBT_MDP_SCOPE=global

export MDP_BUILD_SHA=$(git -C "$MDP_LOCAL_ROOT" rev-parse HEAD 2>/dev/null || printf local-snapshot)
