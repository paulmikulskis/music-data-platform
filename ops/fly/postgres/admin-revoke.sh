#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
pnpm --dir "$root/control" exec tsx "$root/control/packages/control-db/scripts/admin-key.ts" revoke "$@"
if [[ "${1:-}" == showcase-* ]]; then
  printf '%s\n' 'Check MDP_SHOWCASE_PEOPLE: remove the person to end access, or replace their admin_key and api_key_id to keep access. See docs/operating.md#give-a-viewer-access.' >&2
fi
