#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
docker compose -f ops/local/docker-compose.yml up -d mdp-pg-lake mdp-minio
ready=0
for attempt in {1..90}; do
  if docker exec mdp-pg-lake /opt/mdp/boot/healthcheck.sh >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
if [[ "$ready" != 1 ]]; then
  docker logs --tail 50 mdp-pg-lake
  exit 1
fi
python3 ops/local/pg-lake-gates.py all
