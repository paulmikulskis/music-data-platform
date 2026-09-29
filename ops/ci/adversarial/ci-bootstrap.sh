#!/usr/bin/env bash
# Provision the disposable stack; assertions use HTTP/SQL, never Docker.
set -euo pipefail
cd "$(dirname "$0")/../../.."
export MDP_PG_HOST=127.0.0.1 MDP_PG_PORT=5434 MDP_PG_USER=postgres
export COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-mdp-adversarial}
export MDP_PG_LAKE_CONTAINER="$COMPOSE_PROJECT_NAME-pg"
export MDP_MINIO_CONTAINER="$COMPOSE_PROJECT_NAME-minio"
export TMUX_TMPDIR
TMUX_TMPDIR=$(mktemp -d /tmp/tmx-adversarial-XXXXXX)
unset TMUX TMUX_PANE
cp dbt/profiles/profiles.example.yml dbt/profiles/profiles.yml
compose=(docker compose -f ops/local/docker-compose.yml)
cleanup() {
  tmux kill-server 2>/dev/null || true
  "${compose[@]}" down --volumes --remove-orphans
  rm -rf "$TMUX_TMPDIR"
}
trap cleanup EXIT
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
# pg_local uses the deployed raw bootstrap, including tables with no landed rows.
uv run --project functions python - <<'PYRAW'
import os
from pathlib import Path

from mdp_functions.exporter import ensure_raw

ensure_raw(os.environ["MDP_WAREHOUSE_URL"], Path("functions/schemas"))
PYRAW
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
service_host=$(docker inspect "$MDP_PG_LAKE_CONTAINER" --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}')
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
ops/ci/lifecycle.sh --target pg_local --reset --claim --evidence-dir ops/evidence/adversarial/bootstrap
# A real closed hourly cycle supplies the workbench input before the suite.
ops/ci/lifecycle.sh --target pg_local --case a --evidence-dir ops/evidence/adversarial/bootstrap
export MDP_ADVERSARIAL_UDF_HOST="$service_host"
export MDP_CONTROL_API_URL=http://127.0.0.1:8090
export MDP_WORKBENCH_URL=http://127.0.0.1:8085
export MDP_AUTH_MODE=dev PORT=8090
export MDP_WORKBENCH_WH_URL="${MDP_WORKBENCH_WH_DATABASE_URL%/control*}/warehouse?sslmode=require"
export MDP_READER_URL="${MDP_READER_WH_DATABASE_URL%/control*}/warehouse?sslmode=require"
export MDP_WB_USER=workbench_wh
export MDP_WB_PASSWORD="$MDP_WB_USER"
export MDP_WORKBENCH_ADMIN_URL="$(uv run --project functions python - <<'PYENV'
import os
from psycopg.conninfo import conninfo_to_dict,make_conninfo
options=conninfo_to_dict(os.environ['MDP_CONTROL_ADMIN_URL'])
options['dbname']='warehouse'
print(make_conninfo(**options))
PYENV
)"
# Explicit pane environments: the tmux server predates workbench configuration.
uv run --project functions python - <<'PYSTART'
import os,subprocess
for session,command in [('mdp-test-wb','uv run --project functions mdp workbench serve'),
                        ('mdp-test-control','pnpm --dir control --filter @mdp/control-api start')]:
    argv=['tmux','new-session','-d','-s',session,'-c',os.getcwd()]
    for key,value in os.environ.items():
        if key.startswith(('MDP_','DBT_')):
            argv+=['-e',key+'='+value]
    subprocess.run([*argv,command],check=True,capture_output=True)
PYSTART
for attempt in {1..60}; do
  if curl -fsS -H 'x-mdp-dev-user: dev-user' "$MDP_CONTROL_API_URL/workbench" >/dev/null; then break; fi
  sleep 1
done
ops/ci/accept-adversarial.sh
