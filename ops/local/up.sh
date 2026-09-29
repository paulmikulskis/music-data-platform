#!/usr/bin/env bash
set -euo pipefail
# Only fixture configuration enters child processes, never inherited provider credentials.
if [[ ${1:-} != --local-environment ]]; then
  options=(HOME="$HOME" PATH="$PATH" USER="${USER:-local}")
  for name in MDP_LOCAL_PG_PORT MDP_LOCAL_FUNCTIONS_PORT MDP_LOCAL_WORKBENCH_PORT MDP_LOCAL_CONTROL_PORT MDP_LOCAL_DATA_PORT; do
    [[ -z ${!name:-} ]] || options+=("$name=${!name}")
  done
  exec env -i "${options[@]}" bash "$0" --local-environment "$@"
fi
shift
if [[ $# -gt 1 || ($# -eq 1 && $1 != --demo-week) ]]; then
  echo 'usage: bash ops/local/up.sh [--demo-week] (demo week is on by default)' >&2
  exit 2
fi
MDP_LOCAL_ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
source "$MDP_LOCAL_ROOT/ops/local/common.sh"
missing_tools=false
for tool in docker uv pnpm node tmux psql curl; do
  mdp_local_require "$tool" || missing_tools=true
done
if $missing_tools; then
  printf 'Install the tools above, then rerun bash ops/local/up.sh.\n' >&2
  exit 1
fi
docker info >/dev/null 2>&1 || { mdp_local_error 'Docker is not running. Start Docker, then rerun bash ops/local/up.sh.'; exit 1; }
cd "$MDP_LOCAL_ROOT"
umask 077
mkdir -p "$TMUX_TMPDIR"
if ! mkdir "$MDP_LOCAL_STATE/starting" 2>/dev/null; then
  mdp_local_error "Startup in progress. Wait for up.sh to finish ($MDP_LOCAL_STATE/starting)."; exit 1
fi
trap 'rmdir "$MDP_LOCAL_STATE/starting"' EXIT
trap 'mdp_local_error "startup failed; logs: $MDP_LOCAL_STATE; stop with bash ops/local/down.sh"' ERR

if ! docker container inspect "$MDP_LOCAL_CONTAINER" >/dev/null 2>&1; then
  # Check all ports together before provisioning anything.
  uv run --no-project python - "$MDP_LOCAL_PG_PORT" "$MDP_LOCAL_FUNCTIONS_PORT" "$MDP_LOCAL_WORKBENCH_PORT" "$MDP_LOCAL_CONTROL_PORT" "$MDP_LOCAL_DATA_PORT" <<'PY'
import socket
import sys
sockets = []
for value in sys.argv[1:]:
    try:
        port = int(value)
        if not 1024 <= port <= 65535:
            raise ValueError()
        sock = socket.socket()
        sock.bind(('127.0.0.1', port))
        sockets.append(sock)
    except (OSError, ValueError):
        sys.exit(f'Local stack: port {value} is invalid, repeated or busy; set MDP_LOCAL_*_PORT')
PY
  # Build the repo image in heap mode; Docker reuses unchanged layers.
  docker build --build-arg WITH_PG_LAKE=0 -t mdp-postgres:dev -f ops/fly/postgres/Dockerfile .
  roles=()
  for role in migrator rights_sync control_rt functions_rt service_read loader_wh dbt_transform workbench_wh reader_wh api_key_reader showcase_wh; do
    upper=$(printf '%s' "$role" | tr '[:lower:]' '[:upper:]')
    roles+=(-e "MDP_ROLE_PASSWORD_${upper}=$role")
  done
  docker run -d --name "$MDP_LOCAL_CONTAINER" --label "mdp.local.root=$MDP_LOCAL_ROOT" \
    --shm-size=1g -p "127.0.0.1:$MDP_LOCAL_PG_PORT:5432" \
    -e POSTGRES_PASSWORD=postgres -e MDP_PG_HOSTNAME=localhost "${roles[@]}" mdp-postgres:dev >/dev/null
  rm -f "$MDP_LOCAL_STATE/seeded"
  : > "$MDP_LOCAL_STATE/ports.sh"
  for name in MDP_LOCAL_PG_PORT MDP_LOCAL_FUNCTIONS_PORT MDP_LOCAL_WORKBENCH_PORT MDP_LOCAL_CONTROL_PORT MDP_LOCAL_DATA_PORT; do
    printf '%s=%q\n' "$name" "${!name}" >> "$MDP_LOCAL_STATE/ports.sh"
  done
else
  mdp_local_owned
  docker start "$MDP_LOCAL_CONTAINER" >/dev/null
fi
source ops/local/env.sh
ready=false
for ((attempt=0; attempt<90; attempt++)); do
  if psql "$MDP_WAREHOUSE_ADMIN_URL" -XAtc 'select 1' >/dev/null 2>&1; then ready=true; break; fi
  [[ $(docker inspect -f '{{.State.Running}}' "$MDP_LOCAL_CONTAINER") == true ]] || break
  sleep 1
done
$ready || { mdp_local_error "Postgres is not ready; inspect docker logs $MDP_LOCAL_CONTAINER"; exit 1; }
pnpm --dir control install --frozen-lockfile
uv sync --frozen --project functions
uv sync --frozen --project dbt
[[ -f dbt/profiles/profiles.yml ]] || cp dbt/profiles/profiles.example.yml dbt/profiles/profiles.yml
if [[ ! -f "$MDP_LOCAL_STATE/seeded" ]]; then
  # init.sh expects the administrator, whereas dbt reads MDP_PG_USER as dbt_transform.
  MDP_PG_USER=postgres uv run --project functions mdp control init --local
  uv run --project functions mdp registry sync
  uv run --project functions python ops/local/seed.py
  uv run --project functions python ops/local/demo_week.py
  uv run --project dbt dbt seed --target pg_local --vars '{dry_run: true}'
  models=()
  while IFS= read -r model; do models+=("+$model"); done < ops/local/models.txt
  uv run --project dbt dbt build --target pg_local --vars '{dry_run: true, cycle_opened_at: "2026-09-20T23:00:00Z"}' \
    --select "${models[@]}" --indirect-selection cautious
  uv run --project functions python ops/local/verify_week.py
  touch "$MDP_LOCAL_STATE/seeded"
fi

start_service() {
  local name=$1 command=$2
  local launch="$MDP_LOCAL_STATE/$name.sh" shell_command
  if tmux list-windows -t "$MDP_LOCAL_SESSION" -F '#{window_name}' 2>/dev/null | grep -Fxq "$name"; then return; fi
  printf '#!/usr/bin/env bash\nset -euo pipefail\ncd %q\nsource ops/local/env.sh\nexec %s > %q 2>&1\n' \
    "$MDP_LOCAL_ROOT" "$command" "$MDP_LOCAL_STATE/$name.log" > "$launch"
  printf -v shell_command 'exec env -i HOME=%q PATH=%q bash %q' "$HOME" "$PATH" "$launch"
  if tmux has-session -t "$MDP_LOCAL_SESSION" 2>/dev/null; then
    tmux new-window -d -t "$MDP_LOCAL_SESSION" -n "$name" "$shell_command"
  else
    tmux -f /dev/null new-session -d -s "$MDP_LOCAL_SESSION" -n "$name" "$shell_command"
  fi
}
start_service functions 'uv run --project functions mdp serve --host 127.0.0.1 --port "$MDP_LOCAL_FUNCTIONS_PORT"'
start_service workbench 'uv run --project functions mdp workbench serve --host 127.0.0.1 --port "$MDP_LOCAL_WORKBENCH_PORT"'
start_service control 'env HOST=127.0.0.1 PORT="$MDP_LOCAL_CONTROL_PORT" pnpm --dir control --filter @mdp/control-api start'
start_service data 'env HOST=127.0.0.1 PORT="$MDP_LOCAL_DATA_PORT" pnpm --dir control --filter @mdp/data-api start'
for url in "$MDP_SERVICE_URL/v1/health" "$MDP_WORKBENCH_URL/health" "$MDP_CONTROL_API_URL/health" "$MDP_DATA_API_URL/health"; do
  ready=false
  for ((attempt=0; attempt<60; attempt++)); do
    if curl --noproxy '*' -fsS --max-time 3 "$url" >/dev/null 2>&1; then ready=true; break; fi
    sleep 1
  done
  $ready || { mdp_local_error "$url is not ready; logs: $MDP_LOCAL_STATE"; exit 1; }
done
printf '\nFunctions: %s\nWorkbench: %s (open %s/workbench)\nConsole: %s/ops\nExplorer: %s/explorer\nData API: %s\n' \
  "$MDP_SERVICE_URL" "$MDP_WORKBENCH_URL" "$MDP_CONTROL_API_URL" "$MDP_CONTROL_API_URL" "$MDP_CONTROL_API_URL" "$MDP_DATA_API_URL"
printf 'Logs: %s\nAttach: source ops/local/env.sh && tmux attach -t %s\nStop: bash ops/local/down.sh\n' "$MDP_LOCAL_STATE" "$MDP_LOCAL_SESSION"
printf '\nAnalyst SQL: psql -X "%s"\n' "$MDP_ANALYST_URL"
printf 'TablePlus / DBeaver: PostgreSQL, host 127.0.0.1, port %s, database warehouse\n' "$MDP_PG_PORT"
printf 'User: analyst_local  Password: analyst_local  SSL mode: require\n'
printf 'R: mdpr::mdp_setup(target = "local", port = %s); con <- mdpr::mdp_connect("mdp_local")\n' "$MDP_PG_PORT"
printf 'R setup writes ~/.pg_service.conf and ~/.pgpass (0600). psql service=mdp_local\n'
printf 'Separate files: set PGSERVICEFILE and PGPASSFILE before mdp_setup() and psql. See docs/analyst-access.md#r-and-psql-files\n'
