#!/usr/bin/env bash
set -euo pipefail
mkdir -p /data/dumps /data/schemas
if [[ -d /data ]]; then
  mkdir -p /data/dumps /data/schemas
  chown -R mdp:mdp /data
fi
if [[ "${FLY_PROCESS_GROUP:-api}" == api ]]; then
  unset MDP_WORKBENCH_ADMIN_URL MDP_WORKBENCH_WH_URL MDP_CONTROL_RT_URL MDP_READER_URL
fi
exec gosu mdp "$@"
