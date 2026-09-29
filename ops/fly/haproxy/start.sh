#!/bin/sh
set -eu
: "${MDP_PG_BACKEND_HOST:?Set MDP_PG_BACKEND_HOST}"
printf '%s\n' "${MDP_PG_ALLOWED_CIDRS:-}" | tr ', ' '\n' | sed '/^$/d' > /usr/local/etc/haproxy/allow.lst
exec haproxy -W -db -f /usr/local/etc/haproxy/haproxy.cfg
