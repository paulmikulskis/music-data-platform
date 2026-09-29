#!/usr/bin/env bash
set -euo pipefail
# A SIGKILL leaves a stale PID file; remove only after checking the process is gone.
if [[ -s "$PGDATA/postmaster.pid" ]]; then
  read -r old_pid < "$PGDATA/postmaster.pid"
  if ! kill -0 "$old_pid" 2>/dev/null; then rm -f "$PGDATA/postmaster.pid"; fi
fi
if [[ "$WITH_PG_LAKE" = 1 ]]; then
  ready=0
  for attempt in {1..30}; do
    if gosu postgres pg_isready -h /tmp -p 5332 -U postgres >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  if [[ "$ready" != 1 ]]; then echo 'pgduck_server did not become ready' >&2; exit 1; fi
fi
python3 /opt/mdp/boot/statistics-storage.py
exec /usr/local/bin/docker-entrypoint.sh postgres -c config_file=/run/mdp/postgresql.conf -c hba_file=/opt/mdp/boot/pg_hba.mdp.conf -c logging_collector=off -c log_statement=none -c log_min_duration_statement=-1 -c log_min_duration_sample=-1 -c log_min_error_statement=panic -c pg_stat_statements.track_utility=off -c pg_stat_statements.save=off -c pg_stat_statements.max=1000
