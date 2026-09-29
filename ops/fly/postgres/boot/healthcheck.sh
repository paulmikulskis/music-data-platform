#!/usr/bin/env bash
set -euo pipefail
gosu postgres psql -X -At -d warehouse -c 'SELECT 1' >/dev/null
if [[ "$WITH_PG_LAKE" = 1 ]]; then
  # Test the pgduck socket independently; pg_isready alone does not run a query.
  gosu postgres psql -X 'host=/tmp port=5332 dbname=postgres' -At -c 'SELECT 1' >/dev/null
fi
