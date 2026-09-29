#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 1 ]]; then
  echo 'usage: functions/udf/postgres/install.sh <superuser-target-DSN>' >&2
  exit 2
fi
: "${MDP_SERVICE_URL:?Set the functions service URL reachable from Postgres}"
: "${MDP_SERVICE_TOKEN:?Set the functions service bearer token}"
psql -X -v ON_ERROR_STOP=1 --dbname "$1" -f "$(dirname "$0")/mdp_invoke.sql"
