#!/usr/bin/env bash
set -euo pipefail
MDP_LOCAL_ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
source "$MDP_LOCAL_ROOT/ops/local/common.sh"
for tool in docker tmux; do mdp_local_require "$tool"; done
docker info >/dev/null 2>&1 || { mdp_local_error 'Docker is not running. Start Docker, then rerun bash ops/local/down.sh.'; exit 1; }
if [[ -d "$MDP_LOCAL_STATE/starting" ]]; then
  mdp_local_error 'Startup in progress. Wait for up.sh to finish.'; exit 1
fi
if tmux has-session -t "$MDP_LOCAL_SESSION" 2>/dev/null; then
  tmux kill-session -t "$MDP_LOCAL_SESSION"
fi
if docker container inspect "$MDP_LOCAL_CONTAINER" >/dev/null 2>&1; then
  mdp_local_owned
  docker rm -f -v "$MDP_LOCAL_CONTAINER" >/dev/null
fi
rm -f "$MDP_LOCAL_STATE/seeded" "$MDP_LOCAL_STATE/ports.sh"
printf 'Local stack stopped. Container removed. Logs: %s\n' "$MDP_LOCAL_STATE"
