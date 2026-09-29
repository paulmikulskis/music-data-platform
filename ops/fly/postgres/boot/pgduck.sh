#!/usr/bin/env bash
set -euo pipefail
# Same UID/filesystem as Postgres, including $PGDATA/base/pgsql_tmp.
exec gosu postgres /usr/lib/postgresql/17/bin/pgduck_server \
  --unix_socket_directory /tmp --port 5332 \
  --memory_limit "${PGDUCK_MEMORY_LIMIT:-1GB}" \
  --init_file_path /run/mdp/duckdb-init.sql \
  --cache_dir /var/lib/postgresql/pgduck-cache \
  --duckdb_database_file_path /var/lib/postgresql/pgduck-cache/server.db "$@"
