#!/usr/bin/env bash
# CI fixture provisioning only. Harness assertions never use Docker.
set -euo pipefail
cd "$(dirname "$0")/../../.."
export MDP_PG_PORT=5434
compose=(docker compose -f ops/local/docker-compose.yml)
if [[ -n ${MDP_CI_IMAGE:-} ]]; then
  docker pull "$MDP_CI_IMAGE"
  docker tag "$MDP_CI_IMAGE" mdp-postgres:local
  "${compose[@]}" up -d --no-build mdp-pg-lake
else
  "${compose[@]}" up -d --build mdp-pg-lake
fi
source ops/local/init.sh
for attempt in {1..90}; do
  if psql -XAt -d postgres -c 'select 1' >/dev/null 2>&1; then break; fi
  sleep 2
done
bash ops/local/init.sh >/dev/null
export MDP_CONTROL_URL="$MDP_FUNCTIONS_RT_DATABASE_URL"
export MDP_CONTROL_RT_URL="$MDP_CONTROL_RT_DATABASE_URL"
export MDP_WAREHOUSE_URL="${MDP_LOADER_WH_DATABASE_URL%/control*}/warehouse?sslmode=prefer"
export MDP_SERVICE_READ_URL="${MDP_SERVICE_READ_DATABASE_URL%/control*}/warehouse?sslmode=prefer"
export MDP_FIXTURE=true MDP_FIXTURE_MODE=1 MDP_DBT_CLOUD_VERIFY=false
export MDP_DUMP_ROOT="file://$(mktemp -d)" MDP_SCHEMA_ROOT="$(mktemp -d)"
export MDP_DEV_DB="$(mktemp -d)/dev.duckdb"
export MDP_SERVICE_TOKEN="$(uv run --project functions python -c 'from uuid import uuid4; print(uuid4().hex)')"
uv run --project functions mdp control init --local >/dev/null
# Keep the admin URL on the target control database before the explicit claim.
export MDP_CONTROL_ADMIN_URL="$(uv run --project functions python - <<'PY'
import os
from psycopg.conninfo import conninfo_to_dict, make_conninfo
options = conninfo_to_dict(os.environ['MDP_CONTROL_ADMIN_URL'])
options['dbname'] = 'control'
print(make_conninfo(**options))
PY
)"
# Container-to-host address on Linux; no fixed private network assumption.
service_host=$(docker inspect mdp-pg-lake --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}')
export MDP_SERVICE_URL="http://${service_host}:8080"
psql -X -d warehouse -v ON_ERROR_STOP=1 -f functions/udf/postgres/mdp_invoke.sql >/dev/null
export MDP_SERVICE_URL=http://127.0.0.1:8080
export MDP_PG_HOST="$PGHOST" MDP_PG_USER=dbt_transform MDP_PG_DB=warehouse MDP_PG_SCHEMA=dbt
export MDP_PG_PASSWORD="$MDP_PG_USER"
# Reference seeds are prerequisite fixture data; bootstrap must not bind a cycle.
uv run --project dbt dbt seed --project-dir dbt --profiles-dir dbt/profiles \
  --target pg_local --vars '{"dry_run":true}' >/dev/null
tmux new-session -d -s mdp-test-svc -c "$PWD" 'uv run --project functions mdp serve'
for attempt in {1..60}; do
  if curl -fsS -H "Authorization: Bearer $MDP_SERVICE_TOKEN" "$MDP_SERVICE_URL/v1/health" >/dev/null; then break; fi
  sleep 1
done
ops/ci/lifecycle.sh --target pg_local --reset --claim
ops/ci/lifecycle.sh --target pg_local --case all
