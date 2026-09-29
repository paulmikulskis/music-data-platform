#!/usr/bin/env bash
set -euo pipefail
: "${MDP_CONTROL_URL:?Set the runtime control URL}"
: "${MDP_WAREHOUSE_URL:?Set the warehouse loader URL}"
: "${MDP_SERVICE_READ_URL:?Set the warehouse reader URL}"
: "${MDP_CONTROL_ADMIN_URL:?Set the disposable-database administrator URL}"
uv run --project functions pytest functions/tests -q
