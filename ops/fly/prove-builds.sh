#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
build_root=$(mktemp -d /tmp/mdp-release-proof.XXXXXX)
trap 'rm -rf "$build_root"' EXIT
python3 ops/fly/build-context.py "$build_root" >/dev/null
for item in 'postgres ops/fly/postgres' 'haproxy ops/fly/haproxy' 'functions ops/fly/functions' 'alloy ops/fly/alloy' 'core-runner ops/fly/core-runner' 'control-api control/apps/control-api' 'data-api control/apps/data-api'; do
  read -r name folder <<< "$item"
  args=()
  # Native arm64 extension was already gated; locally prove the amd64 heap fallback.
  [[ "$name" != postgres ]] || args+=(--build-arg WITH_PG_LAKE=0)
  echo "BUILD $name linux/amd64"
  docker build --platform linux/amd64 ${args[@]+"${args[@]}"} -f "$build_root/$folder/Dockerfile" -t "mdp-release-$name:proof" "$build_root"
  echo "PASS docker build $name"
done
