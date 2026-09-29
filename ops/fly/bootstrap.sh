#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${FLY_ORG:?secret store prd FLY_ORG required}"
port="${MDP_BOOTSTRAP_PORT:-15432}"
export MDP_BOOTSTRAP_PORT="$port"
# A local proxy carries TLS unchanged; it is used only for initial migration.
if nc -z 127.0.0.1 "$port"; then echo 'Bootstrap proxy port is already occupied; refusing to reuse it' >&2; exit 1; fi
bash ops/fly/fly.sh proxy "$port":5432 --org "$FLY_ORG" --app mdp-postgres >/tmp/mdp-release-bootstrap-proxy.log 2>&1 &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null || true' EXIT
for _ in {1..30}; do
  kill -0 "$proxy_pid" 2>/dev/null || { echo 'Bootstrap Fly proxy exited' >&2; exit 1; }
  if nc -z 127.0.0.1 "$port"; then break; fi
  sleep 1
done
uv run --project functions python ops/fly/bootstrap.py
