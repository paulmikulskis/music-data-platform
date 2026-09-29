#!/usr/bin/env bash
# All callers supply org/app. flyctl itself supports --org only on some commands.
set -euo pipefail
org=''; app=''; args=()
while (($#)); do
  case "$1" in
    --org) org=$2; shift 2 ;;
    --app) app=$2; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done
: "${FLY_ORG:?Set FLY_ORG}"
[[ "$org" == "$FLY_ORG" && "$app" == mdp-* ]] || { echo 'Refusing org other than FLY_ORG or non-MDP app' >&2; exit 2; }
# Map the logical template name to the operator's provisioned app.
app_key="FLY_APP_$(printf '%s' "${app#mdp-}" | tr '[:lower:]-' '[:upper:]_')"
app="${!app_key:?Set the FLY_APP variable for this service}"
# Config validation is read-only and works before an app exists.
if [[ "${args[0]} ${args[1]:-}" == 'config validate' ]]; then
  exec fly "${args[@]}" --app "$app"
fi
fly orgs list --json | python3 -c 'import json,sys; assert sys.argv[1] in json.load(sys.stdin), "Org membership missing"' "$org"
if [[ "${args[0]} ${args[1]:-}" == 'apps create' ]]; then
  exec fly apps create "$app" --org "$org" "${args[@]:2}"
fi
fly apps list --org "$org" --json | python3 -c 'import json,sys; assert any(a["Name"]==sys.argv[1] and a["Organization"]["Slug"]==sys.argv[2] for a in json.load(sys.stdin)), "App ownership mismatch or missing app"' "$app" "$org"
if [[ "${args[0]} ${args[1]:-}" == 'apps restart' ]]; then
  exec fly apps restart "$app" "${args[@]:2}"
fi
if [[ "${args[0]} ${args[1]:-}" == 'machine run' || "${args[0]} ${args[1]:-}" == 'machine create' || "${args[0]}" == deploy ]]; then
  # deploy lacks --org in current flyctl; machine run and machine create support it.
  if [[ "${args[0]}" == machine ]]; then args+=(--org "$org"); fi
fi
[[ "${args[0]}" != proxy ]] || args+=(--org "$org")
exec fly "${args[@]}" --app "$app"
