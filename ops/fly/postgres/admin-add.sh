#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
exec pnpm --dir "$root/control" exec tsx "$root/control/packages/control-db/scripts/admin-key.ts" add "$@"
