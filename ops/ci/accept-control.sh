#!/usr/bin/env bash
# acceptance entry point; the implementation lives beside the control workspace.
set -euo pipefail
exec "$(dirname "$0")/../../control/scripts/accept-control.sh" "$@"
