#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
uv run --project functions python ops/showcase/stack/collect.py "$@"
