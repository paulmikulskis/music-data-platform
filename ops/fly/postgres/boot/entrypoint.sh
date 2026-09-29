#!/usr/bin/env bash
set -euo pipefail
install -d -m 700 -o postgres -g postgres /var/lib/postgresql/tls /run/mdp
hostname="${MDP_PG_HOSTNAME:-${FLY_APP_NAME:-mdp-postgres}.example.invalid}"
if [[ ! -f /var/lib/postgresql/tls/server.key || ! -f /var/lib/postgresql/tls/server.crt ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -subj "/CN=$hostname" -addext "subjectAltName=DNS:$hostname" \
    -keyout /var/lib/postgresql/tls/server.key -out /var/lib/postgresql/tls/server.crt 2>/dev/null
fi
chown postgres:postgres /var/lib/postgresql/tls/server.{key,crt}
chmod 600 /var/lib/postgresql/tls/server.{key,crt}
python3 /opt/mdp/boot/configure.py
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
