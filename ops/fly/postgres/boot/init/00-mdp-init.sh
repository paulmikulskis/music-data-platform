#!/usr/bin/env bash
set -euo pipefail
# The official entrypoint invokes this only for an empty PGDATA, as postgres.
# control-db/sql is the single source of databases and role definitions.
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -f /opt/mdp/sql/databases.sql
args=()
for role in migrator rights_sync control_rt functions_rt service_read loader_wh dbt_transform workbench_wh reader_wh api_key_reader showcase_wh; do
  role_upper="$(printf '%s' "$role" | tr '[:lower:]' '[:upper:]')"
  variable="MDP_ROLE_PASSWORD_${role_upper}"
  : "${!variable:?A password is required for every MDP role}"
  args+=(-v "${role}_password=${!variable}")
done
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres "${args[@]}" -f /opt/mdp/sql/roles.sql
for database in control warehouse; do
  psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" -c 'CREATE EXTENSION IF NOT EXISTS plpython3u'
  if [[ "$WITH_PG_LAKE" = 1 ]]; then
    psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" -c 'CREATE EXTENSION IF NOT EXISTS pg_lake CASCADE'
  fi
done
if [[ "$WITH_PG_LAKE" = 1 ]]; then
  # The login roles remain NOINHERIT; explicitly inherit only the loader's
  # upstream object-storage capability, without permission to SET that role.
  psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname warehouse \
    -c 'GRANT lake_read_write TO loader_wh WITH INHERIT TRUE, SET FALSE'
fi
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname warehouse -f /opt/mdp/boot/init/10-warehouse-grants.sql
# Private config comes from MDP_SERVICE_URL / MDP_SERVICE_TOKEN.
psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname warehouse -f /opt/mdp/boot/init/20-mdp-udf.sql

for filename in 15-label-catalog.sql 16-label-definitions.sql 17-explore-views.sql 18-query-statistics.sql; do
  psql -X -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname warehouse -f "/opt/mdp/boot/init/$filename"
done

python3 /opt/mdp/boot/statistics-storage.py
