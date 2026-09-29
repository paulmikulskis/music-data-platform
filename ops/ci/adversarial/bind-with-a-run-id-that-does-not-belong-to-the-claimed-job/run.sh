#!/usr/bin/env bash
set -euo pipefail
exec "$(dirname "$0")/../run-case.sh" "$(basename "$(dirname "$0")")" "$@"
