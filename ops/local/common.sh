#!/usr/bin/env bash
# Internal helpers shared by up/down and the Bash/Zsh-compatible environment file.
mdp_local_error() { printf 'Local stack: %s\n' "$*" >&2; return 1; }
mdp_local_require() {
  command -v "$1" >/dev/null 2>&1 && return 0
  local hint
  case "$1" in
    docker) hint='macOS: brew install --cask docker; Linux (Ubuntu): sudo apt-get install docker.io' ;;
    uv) hint='macOS: brew install uv; Linux: curl -LsSf https://astral.sh/uv/install.sh | sh' ;;
    pnpm) hint='macOS: npm install --global pnpm@10.28.0; Linux (system Node): sudo npm install --global pnpm@10.28.0' ;;
    node) hint='macOS: brew install node@22 && brew link --force node@22; Linux (Ubuntu): curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash - && sudo apt-get install -y nodejs' ;;
    tmux) hint='macOS: brew install tmux; Linux (Ubuntu): sudo apt-get install tmux' ;;
    psql) hint='macOS: brew install libpq && brew link --force libpq; Linux (Ubuntu): sudo apt-get install postgresql-client' ;;
    curl) hint='macOS: brew install curl; Linux (Ubuntu): sudo apt-get install curl' ;;
  esac
  mdp_local_error "missing tool: $1. Install with $hint"
}
mdp_local_id=$(printf '%s' "$MDP_LOCAL_ROOT" | cksum | cut -d ' ' -f 1)
MDP_LOCAL_CONTAINER="mdp-local-${UID}-${mdp_local_id}"
MDP_LOCAL_STATE="/tmp/$MDP_LOCAL_CONTAINER"
export TMUX_TMPDIR="$MDP_LOCAL_STATE/tmux"
# TMUX takes precedence over TMUX_TMPDIR when called inside another tmux session.
unset TMUX
MDP_LOCAL_SESSION=mdp-local
# A running checkout keeps its original ports, including in newly opened terminals.
if [ -f "$MDP_LOCAL_STATE/ports.sh" ]; then
  . "$MDP_LOCAL_STATE/ports.sh"
fi
MDP_LOCAL_PG_PORT=${MDP_LOCAL_PG_PORT:-56432}
MDP_LOCAL_FUNCTIONS_PORT=${MDP_LOCAL_FUNCTIONS_PORT:-18080}
MDP_LOCAL_WORKBENCH_PORT=${MDP_LOCAL_WORKBENCH_PORT:-18085}
MDP_LOCAL_CONTROL_PORT=${MDP_LOCAL_CONTROL_PORT:-18090}
MDP_LOCAL_DATA_PORT=${MDP_LOCAL_DATA_PORT:-18091}
mdp_local_owned() {
  [ "$(docker inspect -f '{{index .Config.Labels "mdp.local.root"}}' "$MDP_LOCAL_CONTAINER")" = "$MDP_LOCAL_ROOT" ] ||
    mdp_local_error "container $MDP_LOCAL_CONTAINER belongs to another stack"
}
