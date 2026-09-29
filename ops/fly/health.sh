#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
for app in mdp-postgres mdp-pg-frontend mdp-functions mdp-control-api mdp-data-api mdp-core-runner; do
  bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app "$app" |
    python3 -c 'import json,sys; rows=json.load(sys.stdin); print(sys.argv[1]+": "+", ".join(m["id"]+"="+m["state"] for m in rows)); assert rows' "$app"
done
