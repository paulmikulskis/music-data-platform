#!/usr/bin/env bash
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
fi
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PGHOST="${MDP_PG_HOST:-127.0.0.1}"
export PGPORT="${MDP_PG_PORT:-5433}"
export PGUSER="${MDP_PG_USER:-postgres}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
export PGPASSWORD="$POSTGRES_PASSWORD"
export MDP_CONTROL_DATABASE_URL="postgresql://migrator:migrator@${PGHOST}:${PGPORT}/control?sslmode=prefer"
export MDP_CONTROL_ADMIN_URL="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/postgres?sslmode=prefer"
for role in migrator rights_sync control_rt functions_rt service_read loader_wh dbt_transform workbench_wh reader_wh api_key_reader showcase_wh; do
  role_upper="$(printf '%s' "$role" | tr '[:lower:]' '[:upper:]')"
  export "MDP_${role_upper}_DATABASE_URL=postgresql://${role}:${role}@${PGHOST}:${PGPORT}/control?sslmode=prefer"
done
# Sourcing exports connection settings only; execution explicitly bootstraps.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  psql -X -v ON_ERROR_STOP=1 -d postgres -f "$repo_root/control/packages/control-db/sql/databases.sql"
  psql -X -v ON_ERROR_STOP=1 -d postgres \
    -v migrator_password=migrator -v rights_sync_password=rights_sync \
    -v control_rt_password=control_rt -v functions_rt_password=functions_rt \
    -v service_read_password=service_read -v loader_wh_password=loader_wh \
    -v dbt_transform_password=dbt_transform -v workbench_wh_password=workbench_wh \
    -v reader_wh_password=reader_wh -v api_key_reader_password=api_key_reader \
    -v showcase_wh_password=showcase_wh \
    -f "$repo_root/control/packages/control-db/sql/roles.sql"
  (cd "$repo_root/control" && pnpm --filter @mdp/control-db migrate)
  psql -X -v ON_ERROR_STOP=1 -d control -f "$repo_root/control/packages/control-db/sql/showcase-count.sql"
  # Transactional and rerunnable, including recovery from partial initialization.
  psql -X -v ON_ERROR_STOP=1 -d warehouse -f "$repo_root/ops/fly/postgres/boot/init/10-warehouse-grants.sql"
  psql -X -v ON_ERROR_STOP=1 -d warehouse -f "$repo_root/ops/fly/postgres/boot/init/15-label-catalog.sql"
  psql -X -v ON_ERROR_STOP=1 -d warehouse -f "$repo_root/ops/fly/postgres/boot/init/16-label-definitions.sql"
  psql -X -v ON_ERROR_STOP=1 -d warehouse -f "$repo_root/ops/fly/postgres/boot/init/17-explore-views.sql"
  psql -X -v ON_ERROR_STOP=1 -d warehouse -f "$repo_root/ops/fly/postgres/boot/init/18-query-statistics.sql"
  # Keep local raw projections and pg_local's mdp_pseudonym joins on the same fixture key.
  psql -X -v ON_ERROR_STOP=1 -d warehouse -c "UPDATE mdp.pseudonym_key SET key = 'mdp-local-pseudonym'"
  # Marks a disposable local stack; one-off repairs apply through a raw DSN only where it is set.
  psql -X -v ON_ERROR_STOP=1 -d postgres -c "ALTER DATABASE warehouse SET mdp.local_stack = 'on'"
  psql -X -v ON_ERROR_STOP=1 -d control <<'SQL'
INSERT INTO control.runner_mode (id, runner) VALUES (true, 'core')
ON CONFLICT (id) DO NOTHING;
INSERT INTO control.warehouse (adapter, database, dsn_secret_ref, is_production)
SELECT 'postgres', 'warehouse', 'MDP_WAREHOUSE_URL', true
WHERE NOT EXISTS (SELECT 1 FROM control.warehouse WHERE is_production);
SQL
fi
