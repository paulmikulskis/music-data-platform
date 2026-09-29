#!/usr/bin/env bash
# Run from the repository root after installing the locked control dependencies.
set -euo pipefail
if [[ "${1:-}" == install ]]; then
  exec pnpm --dir control install --frozen-lockfile
fi
if [[ $# != 0 ]]; then
  echo 'Unknown step. Run bash ops/ci/jobs/conformance-contracts.sh install, then bash ops/ci/jobs/conformance-contracts.sh.' >&2
  exit 2
fi
saved=$(mktemp -d /tmp/mdp-conformance-XXXXXX)
trap 'rm -rf "$saved"' EXIT
cp functions/openapi/service.json "$saved/service.json"
cp control/packages/contracts/openapi/control-api.json "$saved/control-api.json"

uv run --project "${MDP_CI_FUNCTIONS_PROJECT:-functions}" mdp openapi export
pnpm --dir control --filter @mdp/contracts openapi
if ! cmp "$saved/service.json" functions/openapi/service.json ||
   ! cmp "$saved/control-api.json" control/packages/contracts/openapi/control-api.json; then
  echo 'OpenAPI files are stale. Run uv run --project functions mdp openapi export and pnpm --dir control --filter @mdp/contracts openapi, then commit the output.' >&2
  exit 1
fi
pnpm --dir control exec vitest run packages/contracts/test/conformance.test.ts
