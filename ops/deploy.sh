#!/usr/bin/env bash
# Run under secret_store run -- bash ops/deploy.sh
# --dry-run executes nothing (including no secret store/Fly reads).
set -euo pipefail
# Bash reads a script as it runs. The whole body is one group, parsed before any of it runs, so an
# edit to this file during a deploy cannot change or break the run in progress.
{
cd "$(dirname "$0")/.."
dry=false; selected=' '; build_only=false
while (($#)); do
  case "$1" in
    --dry-run) dry=true; shift ;;
    --app) selected+="$2 "; shift 2 ;;
    --build-only) build_only=true; shift ;;
    *) echo 'usage: ops/deploy.sh [--dry-run] [--app mdp-APP]... [--build-only]' >&2; exit 2 ;;
  esac
done
: "${FLY_ORG:?Set FLY_ORG=example-org via secret store prd}"
check_fly_version() {
  local version
  # flyctl >= 0.3.226 includes the triple-quote secret importer (superfly/flyctl#4688).
  if version=$(fly version 2>/dev/null) \
    && python3 -c '
import json, re, sys
output = sys.stdin.read().strip()
try:
    payload = json.loads(output)
except ValueError:
    text = re.fullmatch(r"fly(?:ctl)?\s+(v\S+)(?:\s+.*)?", output, re.DOTALL)
    version = text[1] if text else None
else:
    version = payload.get("Version", payload.get("version")) if isinstance(payload, dict) else None
if not isinstance(version, str):
    sys.exit(1)
identifier = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
parsed = re.fullmatch(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:-(" + identifier + r"))?(?:\+" + identifier + r")?", version)
if not parsed:
    sys.exit(1)
release = tuple(int(part) for part in parsed.group(1, 2, 3))
floor = (0, 3, 226)
# A prerelease of the floor itself predates the first supported release.
sys.exit(not (release > floor or (release == floor and parsed[4] is None)))' <<< "$version"; then
    return 0
  fi
  echo 'FAIL deploy needs flyctl v0.3.226 or later to import secrets unchanged. Run fly version update, then rerun the deploy.' >&2
  return 1
}
if $dry; then
  echo 'COMMAND fly version; require flyctl v0.3.226 or later before deploying'
else
  check_fly_version
fi
# One revision for the whole run, HEAD resolved once: build-context.py archives it for every image,
# bootstrap's migrations come from it, and the rebuild machines run its image. Bootstrap (grants, UDF,
# raw tables, seeds, runbooks) and machines.py (with the models it reads) read the checkout itself, so
# before each of those steps verify_pin checks that HEAD is still the pin, that no tracked file they
# read is modified, and that no untracked file sits under an archived path (bootstrap would discover
# it; the images lack it); the deploy stops otherwise. A caller's MDP_DEPLOY_REVISION must be HEAD:
# deploy another revision from a checkout of it.
verify_pin() {  # verify_pin <step>
  $dry && return 0
  local head dirty
  head=$(git rev-parse HEAD)
  if [[ "$head" != "$MDP_DEPLOY_REVISION" ]]; then
    echo "FAIL HEAD moved from $MDP_DEPLOY_REVISION to $head before $1; deploy stopped. Deploy again from one revision." >&2
    exit 1
  fi
  dirty=$(git status --porcelain --untracked-files=no -- inputs; git status --porcelain --untracked-files=all -- "${archived[@]}")
  if [[ -n "$dirty" ]]; then
    printf 'FAIL files the deploy archives or reads differ from the pin before %s (modified, or untracked under an archived path); commit, stash or remove them, then deploy:\n%s\n' "$1" "$dirty" >&2
    exit 1
  fi
}
if $dry; then
  echo 'COMMAND git rev-parse HEAD; refuse a caller revision other than HEAD, a modified tracked file under the archived paths or inputs/, and an untracked file under the archived paths (checked again before bootstrap and the runner update)'
  export MDP_DEPLOY_REVISION='<head-sha>'
else
  head=$(git rev-parse HEAD)
  if [[ -n "${MDP_DEPLOY_REVISION:-}" && "$(git rev-parse "$MDP_DEPLOY_REVISION^{commit}")" != "$head" ]]; then
    echo 'FAIL MDP_DEPLOY_REVISION is not HEAD; bootstrap and machines.py read the checkout, so deploy that revision from a checkout of it' >&2
    exit 1
  fi
  export MDP_DEPLOY_REVISION="$head"
  archived=()
  while IFS= read -r path; do archived+=("$path"); done < <(python3 ops/fly/build-context.py --paths)
  verify_pin 'the build'
fi
echo "REVISION $MDP_DEPLOY_REVISION"
# Order matters when mdp-core-runner and mdp-functions both change. Bootstrap (migrations, raw tables,
# UDFs) runs first. Runner updates clear the schedule and use --skip-start, only while stopped,
# and they stay stopped until mdp-functions is deployed and healthy. A runner on the previous image
# binds through a pre-stamp dbt hook, which the new service refuses (runner_outdated) without closing
# its cycle. A new runner against the previous service fails where a declared read changed (the daily
# enrichments), and a daily that straddles the swap fails its transform. So no runner runs until both
# sides match. control-api follows, and the runners start and the mart rebuild runs only after it, so
# any promoter a cycle or the rebuild runs meets the control API of its own release (the previous one
# refuses the promoter key and has no targets.lookup); until then, Run Now gets 409 runner_outdated.
# data-api deploys after the rebuild (below); showcase follows its API dependencies.
apps=(mdp-postgres mdp-pg-frontend mdp-core-runner mdp-functions mdp-control-api mdp-data-api mdp-showcase mdp-alloy)
for app in $selected; do
  [[ " ${apps[*]} " == *" $app "* ]] || { echo "Unknown --app. Choose one of: ${apps[*]}" >&2; exit 2; }
done
wanted() { [[ "$selected" == ' ' || "$selected" == *" $1 "* ]]; }
preflight_secrets() {
  local secrets_json app failed=false
  if $dry; then
    echo 'COMMAND download secret store JSON once; run python3 ops/fly/secret-map.py filter <app> for every selected app before provisioning'
    return 0
  fi
  if ! secrets_json=$(secret_store export json 2>/dev/null); then
    echo 'FAIL secret preflight cannot download secret store JSON. Check secret store access to music-data-platform/prd, then rerun the deploy.' >&2
    return 1
  fi
  for app in "${apps[@]}"; do
    wanted "$app" || continue
    # Keep values in memory and discard the import text. The filter prints only refused key names.
    if ! python3 ops/fly/secret-map.py filter "$app" <<< "$secrets_json" >/dev/null; then
      failed=true
    fi
  done
  ! $failed
}
run() { printf 'COMMAND'; printf ' %q' "$@"; printf '\n'; $dry || "$@"; }
fly_app() { run bash ops/fly/fly.sh "$@" --org "$FLY_ORG" --app "$app"; }
# A pushed image can take a few seconds to become readable in Fly's registry.
# Use only for calls that consume this deploy's image; other failures keep their exit status.
fly_image() (
  local output status retry=0 delay=5
  $dry && { fly_app "$@"; return; }
  output=$(mktemp "${TMPDIR:-/tmp}/mdp-registry.XXXXXX")
  # Keep cleanup local to this call; the deploy's EXIT trap still reports held runners.
  trap 'rm -f "$output"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  while :; do
    if fly_app "$@" 2>&1 | tee "$output"; then
      return 0
    else
      status=${PIPESTATUS[0]}
    fi
    if ! grep -qE 'MANIFEST_UNKNOWN|manifest unknown' "$output" \
      && ! { grep -qF 'failed to get manifest' "$output" && grep -qE '(^|[^[:alnum:]])404([^[:alnum:]]|$)' "$output"; }; then
      return "$status"
    fi
    (( retry < 4 )) || return "$status"
    retry=$((retry + 1))
    printf 'WAIT registry has not served %s yet; retry %s of 4 in %s s\n' "$image_label" "$retry" "$delay" >&2
    sleep "$delay"
    delay=$((delay * 2))
  done
)
machine_state() {
  bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app "$app" |
    python3 -c 'import json,sys; print(next((m["state"] for m in json.load(sys.stdin) if m["id"]==sys.argv[1]), "missing"))' "$1" || echo unknown
}
# Refuse a foreign hold before replacing an image or clearing its schedule.
check_runner_hold() {
  local machine=$1 owner
  $dry && return 0
  owner=$(bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app mdp-core-runner | python3 -c '
import json, sys
machine = next(m for m in json.load(sys.stdin) if m["id"] == sys.argv[1])
print((machine.get("config", {}).get("metadata") or {}).get("mdp_deploy_hold", ""))' "$machine")
  if [[ -n "$owner" && "$owner" != "$hold_id" && "${MDP_DEPLOY_TAKEOVER:-0}" != 1 ]]; then
    echo "FAIL runner $machine is held by deploy $owner. Confirm that deploy has stopped, then take over: MDP_DEPLOY_TAKEOVER=1 ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api" >&2
    return 1
  fi
}
# A core-runner machine is never updated while running. Recovery also accepts a created machine.
# Deploying mdp-postgres or mdp-functions during a running cycle still drops its
# connections and fails that cycle, so deploy between runs.
# Waits up to the cadence's bound (hourly 30 min, daily and weekly 3 h), or the given limit, then fails.
wait_stopped() {
  local machine=$1 cadence=$2 name=$3 limit=${4:-10800} note=${5:-'a core-runner machine is never updated while running'} waited=0 state allow_created=${6:-false}
  [[ -n "${4:-}" || "$cadence" != hourly ]] || limit=1800
  printf 'WAIT %s until stopped (poll 60 s, at most %s s); %s\n' "$name" "$limit" "$note"
  $dry && return 0
  while :; do
    state=$(machine_state "$machine")
    [[ "$state" == stopped || ( "$allow_created" == true && "$state" == created ) ]] && return 0
    if (( waited >= limit )); then
      echo "FAIL $name is still $state after ${limit} s" >&2
      return 1
    fi
    sleep 60; waited=$((waited + 60))
  done
}
# Restarting mdp-postgres or mdp-functions drops a running cycle's connections and fails it.
# Their images build first; the restart waits until no core-runner machine runs and none is due
# within MDP_QUIET_MARGIN_MIN minutes (default 15) of its schedule, at most MDP_QUIET_LIMIT s (3 h).
quiet_reason() {
  bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app mdp-core-runner | python3 -c '
import json, re, sys, time
margin = int(sys.argv[1]) * 60
# Every core-runner machine is created and released with --schedule hourly (run.sh decides the
# cadence inside the machine), so only the hourly entry is reached today.
period = {"hourly": 3600, "daily": 86400, "weekly": 604800}
now = time.time()
for m in json.load(sys.stdin):
    name, state = m.get("name"), m.get("state")
    if state not in ("stopped", "destroyed", "suspended", "created"):
        print(f"{name} is {state}")
        break
    config = m.get("config") or {}
    schedule = config.get("schedule") or ""
    every = period.get(schedule)
    if not every:
        # One-off rebuilds and retries have no recurring schedule. Their running state still blocks.
        recurring = re.fullmatch(r"mdp-(hourly|daily|weekly)(-[a-z][a-z0-9-]*)?", name or "")
        if not recurring and (config.get("env") or {}).get("MDP_RUN_REASON_CATEGORY") == "other":
            continue
        if not schedule and sys.argv[2] and (config.get("metadata") or {}).get("mdp_deploy_hold") == sys.argv[2]:
            continue
        print(f"{name} has no valid schedule and is not held by this deploy. Rerun: "
              "ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api")
        break
    # Only scheduler starts show the schedule: a deploy starts runners by hand (source user), and a
    # runner update clears the short event history. Without one on record, wait for the next tick.
    starts = [e["timestamp"] / 1000 for e in m.get("events") or []
              if e.get("type") == "start" and e.get("status") == "starting" and e.get("source") != "user"]
    if not starts:
        print(f"{name} has no scheduled start on record since its last update")
        break
    left = min(every - (now - start) % every for start in starts)
    if left < margin:
        print(f"{name} is due in {int(left // 60)} min")
        break' "${MDP_QUIET_MARGIN_MIN:-15}" "${hold_id:-}" || echo 'the core-runner machine list is unavailable'
}
wait_quiet() {
  local limit=${MDP_QUIET_LIMIT:-10800} waited=0 busy
  printf 'WAIT %s until no core-runner machine runs or is due within %s min (poll 60 s, at most %s s)\n' "$app" "${MDP_QUIET_MARGIN_MIN:-15}" "$limit"
  $dry && return 0
  while :; do
    busy=$(quiet_reason)
    [[ -n "$busy" ]] || return 0
    if [[ "$busy" == *"not held by this deploy"* ]]; then
      echo "FAIL $app was not restarted: $busy" >&2
      return 1
    fi
    if (( waited >= limit )); then
      echo "FAIL $app was not restarted: $busy after ${limit} s. The image is pushed as $image_label; deploy again between runs." >&2
      return 1
    fi
    if (( waited % 600 == 0 )); then echo "WAIT $busy"; fi
    sleep 60; waited=$((waited + 60))
  done
}
runner_id() {
  bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app mdp-core-runner |
    python3 -c 'import json,sys; print(next((m["id"] for m in json.load(sys.stdin) if m["name"]==sys.argv[1]), ""))' "$1"
}
# A Fly stop sends SIGINT, then kills after the stop timeout (default 5 s). run.sh's trap needs that
# time for a Replay's restore build, so runner machines get Fly's maximum, 300 s. flyctl has no
# --kill-timeout flag for machines; stop_config is the machine setting behind it.
runner_config='{"stop_config":{"timeout":"300s"}}'
# Omitting --schedule (or passing an empty flag) preserves an existing schedule in flyctl.
held_runner_config='{"schedule":"","stop_config":{"timeout":"300s"}}'
# Updates or creates each machine machines.py lists (one per cadence for global, one per cadence with
# tenant models and active tenant) without a schedule, only while stopped. start_runners restores
# the hourly Fly tick; run.sh's due gate (MDP_CORE_GATE=1) selects the job's schedule.
# bootstrap.py wrote the active tenants to $MDP_CORE_TENANTS_FILE; a dry run without one shows the
# global machines only. The first wait that times out aborts the deploy before mdp-functions, and
# nothing else is updated.
export MDP_CORE_TENANTS_FILE="${MDP_CORE_TENANTS_FILE:-$(mktemp "${TMPDIR:-/tmp}/mdp-core-tenants.XXXXXX")}"
to_start=''
update_runners() {
  local app=mdp-core-runner name cadence envs machine listed stale updated=''
  # The list is read first, so a bad tenant row fails the deploy under set -e before any machine moves.
  listed=$(python3 ops/fly/core-runner/machines.py "$MDP_CORE_TENANTS_FILE")
  while IFS=$'\t' read -r name cadence envs; do
    [[ -n "$name" ]] || continue
    local env_args=()
    for assignment in $envs; do env_args+=(--env "$assignment"); done
    machine=''
    $dry || machine=$(runner_id "$name")
    # A dry run shows the existing-machine path, then the path when none exists yet.
    if [[ -n "$machine" ]] || $dry; then
      if ! wait_stopped "$machine" "$cadence" "$name" "" 'a stopped or created runner can be updated without starting' true; then
        echo "FAIL mdp-functions, mdp-control-api and mdp-data-api are not deployed. Stopped on the new image:${updated:- none}; on the previous image: $name and every later machine. Rerun: ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api" >&2
        exit 1
      fi
      check_runner_hold "$machine"
      start_due=true
      fly_image machine update "${machine:-<$name-id>}" --image "registry.fly.io/mdp-core-runner:$image_label" "${env_args[@]}" --machine-config "$held_runner_config" --metadata "mdp_deploy_hold=$hold_id" --skip-start --yes
    fi
    if [[ -z "$machine" ]]; then
      $dry && echo "IF $name does not exist yet:"
      start_due=true
      fly_image machine create "registry.fly.io/mdp-core-runner:$image_label" "$cadence" --name "$name" --region "${FLY_REGION:-ewr}" --restart no --vm-size shared-cpu-2x --vm-memory 2048 "${env_args[@]}" --machine-config "$held_runner_config" --metadata "mdp_deploy_hold=$hold_id"
    fi
    updated+=" $name"; to_start+=" $name"
  done <<< "$listed"
  # A tenant machine the list no longer holds (the tenant went inactive, or its cadence lost its tenant
  # models) is destroyed once its current run ends, so it never binds, collects, or sends again; the
  # deploy after the tenant is active again recreates it. One-off Retry and Replay machines carry no
  # mdp-<cadence>- name and are left alone.
  if $dry; then
    echo 'IF a machine named mdp-<cadence>-<slug> is not in the list above:'
    wait_stopped '<stale-machine-id>' daily 'mdp-<cadence>-<slug>'
    fly_app machine destroy '<stale-machine-id>' --force
    return 0
  fi
  stale=$(bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app "$app" | python3 -c '
import json, re, sys
keep = set(sys.argv[1].split())
for m in json.load(sys.stdin):
    hit = re.fullmatch(r"mdp-(hourly|daily|weekly)-[a-z][a-z0-9-]*", m.get("name") or "")
    if hit and m["name"] not in keep:
        print(m["id"], m["name"], hit.group(1), sep="\t")' "$(cut -f1 <<< "$listed")")
  while IFS=$'\t' read -r machine name cadence; do
    [[ -n "$machine" ]] || continue
    echo "STALE $name ($machine) is not in the machine list"
    if ! wait_stopped "$machine" "$cadence" "$name" "" 'a stopped or created runner can be updated without starting' true; then
      echo "FAIL $name is not destroyed; mdp-functions, mdp-control-api and mdp-data-api are not deployed. Rerun the same deploy" >&2
      exit 1
    fi
    check_runner_hold "$machine"
    fly_app machine destroy "$machine" --force
  done <<< "$stale"
}
# Fly's scheduler never fires a scheduled machine left stopped by an update or a create until it is
# started once; each start runs one cycle now and re-arms the schedule.
start_runners() {
  local app=mdp-core-runner name machine
  [[ -n "$to_start" ]] || return 0
  run python3 ops/fly/check-release.py "$MDP_DEPLOY_REVISION"
  for name in $to_start; do
    machine="<$name-id>"
    $dry || machine=$(runner_id "$name")
    MDP_DEPLOY_TAKEOVER=0 check_runner_hold "$machine"
    fly_app machine update "$machine" --schedule hourly --metadata mdp_deploy_hold= --skip-start --yes
    fly_app machine start "$machine"
  done
  to_start=''
}
# The new data API parses each served relation against this release's contracts, and the gated starts
# above are rarely due, so each global cadence rebuilds its marts once on the new image after
# control-api deploys and before data-api does. The rebuild is Core's restore run (MDP_RESTORE=1, reason other): it
# attaches to the cadence's newest scheduled cycle, reruns no collection that already landed, and
# rebuilds the transforms; with no scheduled cycle there is nothing to rebuild. It waits for a run in
# flight (the runner lock), and a gated tick skips while it runs. A failed rebuild stops the deploy.
rebuild_marts() {
  local app=mdp-core-runner cadence name machine code bound
  run python3 ops/fly/check-release.py "$MDP_DEPLOY_REVISION"
  for cadence in hourly daily weekly; do
    name="mdp-rebuild-$cadence" bound=10800
    [[ "$cadence" != hourly ]] || bound=1800
    launch_rebuild "$cadence" "$name" "$bound"
  done
  for cadence in hourly daily weekly; do
    name="mdp-rebuild-$cadence" bound=21600
    [[ "$cadence" != hourly ]] || bound=3600
    machine="<$name-id>"
    $dry || machine=$(runner_id "$name")
    wait_stopped "$machine" "$cadence" "$name" "$bound" 'a rebuild waits for a run in flight, then transforms' || rebuild_failed "$name"
    code=0
    if ! $dry; then
      # flyctl prints fly-go's MachineExitEvent, whose exit_code is omitempty: a clean exit has no
      # exit_code key. No exit event at all, an OOM kill, or a non-zero code is a failure.
      code=$(bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app "$app" | python3 -c '
import json, sys
machine = next((m for m in json.load(sys.stdin) if m["id"] == sys.argv[1]), {})
exits = sorted((e for e in machine.get("events") or [] if e.get("type") == "exit"), key=lambda e: e.get("timestamp", 0))
event = ((exits[-1].get("request") or {}).get("exit_event") or {}) if exits else None
print("unknown" if event is None else "oom_killed" if event.get("oom_killed") else event.get("exit_code", 0))' "$machine")
    fi
    printf 'CHECK %s exit code %s (0 required)\n' "$name" "$code"
    [[ "$code" == 0 ]] || rebuild_failed "$name" "exited $code"
    fly_app machine destroy "$machine" --force
  done
}
# A machine fresh from `machine create` stays `created` while Fly prepares its image, and a start then
# fails with failed_precondition; recreating it only repeats that race. So a rebuild machine is created,
# polled until its launch is done, then started on the same id, retried up to five times 15 s apart
# while the start answers failed_precondition. Only a machine still created (or failed, or gone) after
# that is destroyed and created once more. In any other state the start took, and the wait and the
# exit event decide.
launched() {
  bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app "$app" | python3 -c '
import json, sys
machine = next((m for m in json.load(sys.stdin) if m["id"] == sys.argv[1]), {})
launches = [e for e in machine.get("events") or [] if e.get("type") == "launch"]
sys.exit(not (machine.get("state") == "created" and launches and all(e.get("status") != "pending" for e in launches)))' "$1"
}
launch_rebuild() {
  local cadence=$1 name=$2 bound=$3 attempt try waited machine state out
  for attempt in 1 2; do
    machine=''
    $dry || machine=$(runner_id "$name")
    # A rebuild machine that a stopped deploy or a failed launch left behind goes first.
    [[ -z "$machine" ]] || fly_app machine destroy "$machine" --force
    fly_image machine create "registry.fly.io/mdp-core-runner:$image_label" "$cadence" --name "$name" --region "${FLY_REGION:-ewr}" --restart no --vm-size shared-cpu-2x --vm-memory 2048 --env MDP_RESTORE=1 --env MDP_RUN_REASON_CATEGORY=other --env DBT_MDP_SCOPE=global --env "MDP_RUNNER_LOCK_WAIT_S=$bound" --machine-config "$runner_config"
    printf 'WAIT %s until created with no pending launch event (poll 5 s, at most 120 s)\n' "$name"
    if $dry; then
      fly_app machine start "<$name-id>"
      echo "IF the start answers failed_precondition, start the same machine again 15 s later (at most 5 starts)"
      echo "IF $name is still created after that, destroy it and create it once more:"
      fly_app machine destroy "<$name-id>" --force
      return 0
    fi
    machine=$(runner_id "$name") waited=0
    # A launch still pending after the bound goes on to the start, which retries anyway.
    until [[ -z "$machine" ]] || launched "$machine" || (( waited >= 120 )); do sleep 5; waited=$((waited + 5)); done
    for try in 1 2 3 4 5; do
      [[ -n "$machine" ]] || break
      if out=$(fly_app machine start "$machine" 2>&1); then printf '%s\n' "$out"; return 0; fi
      printf '%s\n' "$out"
      [[ "$out" == *failed_precondition* ]] || break
      echo "RETRY $name start answered failed_precondition (start $try of 5); the same machine starts again in 15 s"
      sleep 15
    done
    state=$(machine_state "$machine")
    case "$state" in created | failed | missing) ;; *) return 0 ;; esac
    echo "RETRY $name is $state after its starts failed (create $attempt of 2)"
  done
  [[ "$state" == missing ]] || fly_app machine destroy "$machine" --force
  rebuild_failed "$name" 'did not start after 2 creates'
}
rebuild_failed() {
  echo "FAIL $1 ${2:-did not finish}; mdp-data-api is not deployed, and the data API still serves the previous contract. Read the machine's logs, then rerun: ops/deploy.sh --app mdp-core-runner --app mdp-control-api --app mdp-data-api" >&2
  exit 1
}
# The api process passes its /v1/health check (functions/fly.toml) once start-up (registry sync, mirror
# catch-up) is done and control, warehouse and object store answer.
wait_healthy() {
  local limit=600 waited=0
  printf 'WAIT %s until every api machine runs %s and passes its health check (poll 15 s, at most %s s)\n' "$app" "$image_label" "$limit"
  $dry && return 0
  until bash ops/fly/fly.sh machine list --json --org "$FLY_ORG" --app "$app" | python3 -c '
import json, sys
api = [m for m in json.load(sys.stdin) if m["config"].get("metadata", {}).get("fly_process_group") == "api"]
sys.exit(not api or not all(
    m["state"] == "started" and m.get("image_ref", {}).get("tag") == sys.argv[1]
    and m.get("checks") and all(c["status"] == "passing" for c in m["checks"]) for m in api))' "$image_label"; do
    if (( waited >= limit )); then
      echo "FAIL $app is not healthy after ${limit} s; the runner machines stay stopped:${to_start:- none}" >&2
      exit 1
    fi
    sleep 15; waited=$((waited + 15))
  done
}
bootstrapped=false
# Set once the runners are on the new image; they start, and the rebuild runs, after mdp-control-api
# and before mdp-data-api deploys (or at the end).
start_due=false rebuild_due=false
runner_rerun='ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api'
# Gate every deployment path, including an app selected without postgres/functions.
# Build-only runs restart nothing and can warm the cache while CI runs.
if ! $build_only; then
  if [[ "${MDP_SKIP_CI_WAIT:-0}" == 1 ]]; then
    echo "WARNING CI WAIT BYPASSED for $MDP_DEPLOY_REVISION; inspect https://github.com/paulmikulskis/music-data-platform/actions before continuing the emergency deploy" >&2
  else
    run bash ops/ci-wait.sh "$MDP_DEPLOY_REVISION"
  fi
fi
# Prepare showcase facts in the pinned archive before any image replacement.
showcase_context=()
if wanted mdp-showcase && { $dry || [[ "$selected" != ' ' ]] || python3 ops/fly/secret-map.py check mdp-showcase >/dev/null 2>&1; }; then
  showcase_context+=(--showcase)
  if $dry; then
    echo 'COMMAND require the showcase name list; count tenants without logging records; enable public health checks'
  else
    if [[ -z "${MDP_SHOWCASE_DENY_NAMES:-}" || "${MDP_SHOWCASE_DENY_NAMES:-}" != *[![:space:]]* ]]; then
      echo 'links links_names_required' >&2
      echo 'Set MDP_SHOWCASE_DENY_NAMES in the deploy config. Read ops/showcase/README.md#recover.' >&2
      exit 1
    fi
    if [[ "$(git rev-parse --is-shallow-repository)" != false ]]; then
      echo 'links links_history' >&2
      echo 'Fetch full history with git fetch --unshallow, then retry the deploy.' >&2
      exit 1
    fi
    export MDP_SHOWCASE_DENY_NAMES
    export MDP_SHOWCASE_PROBE_HOSTS=1
    # An operator may count tenants with read-only SQL when the control API is private.
    if [[ "${MDP_SHOWCASE_TENANT_COUNT:-}" =~ ^[0-9]+$ ]]; then
      :
    elif ! MDP_SHOWCASE_TENANT_COUNT=$(MDP_API_KEY="${MDP_API_KEY:-${MDP_ADMIN_API_KEY:-}}" pnpm --silent --dir control mdp tenants list 2>/dev/null | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    if not isinstance(value, list):
        raise ValueError()
    print(len(value))
except (ValueError, TypeError):
    sys.exit(1)
'); then
      echo 'links links_tenants_unknown' >&2
      echo 'Check the control connection. Read ops/showcase/README.md#recover.' >&2
      exit 1
    fi
    export MDP_SHOWCASE_TENANT_COUNT
  fi
fi
preflight_secrets
if ! $dry; then
  fly orgs list --json | python3 -c 'import json,sys; import os; assert os.environ["FLY_ORG"] in json.load(sys.stdin)'
fi
run python3 ops/fly/secret-map.py provision
if $dry; then
  hold_id='<deploy-id>'
  build_root='<committed-runtime-build-context>'
  image_label='<release-sha-timestamp>'
  printf 'COMMAND python3 ops/fly/build-context.py <temporary-directory>'
  if ((${#showcase_context[@]})); then printf ' %s' "${showcase_context[@]}"; fi
  printf '\n'
else
  build_root=$(mktemp -d /tmp/mdp-release-context.XXXXXX)
  hold_id=${build_root##*/}
  # A reused tag does not move existing machines to the new digest; every build gets its own.
  image_label="release-${MDP_DEPLOY_REVISION:0:7}-$(date -u +%Y%m%d%H%M%S)"
  # A failure between the runner update and mdp-control-api leaves the runners stopped on purpose; say so.
  trap 'status=$?; rm -rf "$build_root"
    if ((status)) && $start_due; then echo "FAIL runner release is incomplete; held machines stay stopped or created, and earlier starts may already be running. Rerun: $runner_rerun" >&2; fi' EXIT
  python3 ops/fly/build-context.py "$build_root" ${showcase_context[@]+"${showcase_context[@]}"} >/dev/null
fi
for app in "${apps[@]}"; do
  if [[ "$app" == mdp-data-api ]]; then
    if $start_due; then start_runners; start_due=false; fi
    if $rebuild_due; then rebuild_marts; rebuild_due=false; fi
  fi
  wanted "$app" || continue
  case "$app" in
    mdp-postgres) config=ops/fly/postgres/fly.toml ;;
    mdp-pg-frontend) config=ops/fly/haproxy/fly.toml ;;
    mdp-functions) config=functions/fly.toml ;;
    *) config=ops/fly/${app#mdp-}/fly.toml ;;
  esac
  if [[ "$app" == mdp-alloy && ( -z "${OTLP_ENDPOINT:-}" || -z "${OTLP_AUTH_HEADER:-}" ) ]]; then
    echo 'PHASE_BLOCKED step=alloy reason=OTLP_ENDPOINT,OTLP_AUTH_HEADER; supply both keys through the secret_store stdin interface'
    $dry || continue
  fi
  # A full deploy before the showcase is configured skips it rather than failing the apps after it.
  if [[ "$app" == mdp-showcase && "$selected" == ' ' ]] && ! $dry \
    && ! python3 ops/fly/secret-map.py check mdp-showcase >/dev/null 2>&1; then
    echo "SKIP mdp-showcase: its secrets are not set; set them as in ops/fly/SECRETS.md, then run ops/deploy.sh --app mdp-showcase" >&2
    continue
  fi
  run python3 ops/fly/secret-map.py check "$app"
  if $dry || ! fly apps list --org "$FLY_ORG" --json | python3 -c 'import json,sys; sys.exit(not any(a["Name"]==sys.argv[1] for a in json.load(sys.stdin)))' "$app"; then
    fly_app apps create --yes
  fi
  if [[ "$app" != mdp-pg-frontend ]]; then
    printf 'COMMAND secret_store export json | python3 ops/fly/secret-map.py filter %s | bash ops/fly/fly.sh secrets import --stage --org %s --app %s\n' "$app" "$FLY_ORG" "$app"
    if ! $dry; then
      # JSON carries exact values; secret-map.py writes each in the one form flyctl's importer reads
      # back unchanged, or refuses the whole import. secret store's env format escapes quotes flyctl keeps.
      secret_store export json |
        python3 ops/fly/secret-map.py filter "$app" |
        bash ops/fly/fly.sh secrets import --stage --org "$FLY_ORG" --app "$app"
    fi
  fi
  if [[ "$app" == mdp-postgres || "$app" == mdp-functions ]]; then
    volume=pgdata; size=20
    if [[ "$app" == mdp-functions ]]; then volume=dumps; size=10; fi
    if $dry || ! bash ops/fly/fly.sh volumes list --json --org "$FLY_ORG" --app "$app" | python3 -c 'import json,sys; sys.exit(not any(v["name"]==sys.argv[1] for v in json.load(sys.stdin)))' "$volume"; then
      fly_app volumes create "$volume" --size "$size" --region "${FLY_REGION:-ewr}" --snapshot-retention 14 --yes
    fi
  fi
  dockerfile=$(python3 -c 'import sys; print(next(l.split("\"")[1] for l in open(sys.argv[1]) if "dockerfile =" in l))' "$config")
  args=(deploy "$build_root" --config "$PWD/$config" --dockerfile "$build_root/$(dirname "$config")/$dockerfile" --remote-only --ha=false --no-public-ips --yes)
  if [[ "$app" == mdp-core-runner ]] || $build_only; then args+=(--build-only --push --image-label "$image_label"); fi
  if [[ "$app" == mdp-functions || "$app" == mdp-control-api || "$app" == mdp-data-api ]]; then
    args+=(--build-arg "MDP_BUILD_SHA=$MDP_DEPLOY_REVISION")
  fi
  if [[ "$app" == mdp-showcase ]]; then args+=(--build-arg "MDP_DEPLOY_REVISION=$MDP_DEPLOY_REVISION"); fi
  if [[ "$app" == mdp-postgres ]]; then
    args+=(--build-arg "BUILD_JOBS=${MDP_PG_BUILD_JOBS:-4}")
    if [[ ${MDP_PG_WITH_PG_LAKE+x} ]]; then
      args+=(--build-arg "WITH_PG_LAKE=$MDP_PG_WITH_PG_LAKE")
    fi
    if [[ "${MDP_PG_DEPOT:-0}" == 1 ]]; then args+=(--depot); fi
  fi
  if [[ "$app" == mdp-functions ]]; then
    args+=(--process-groups "api,workbench")
    # The health wait knows the new machines by this label.
    $build_only || args+=(--image-label "$image_label")
  fi
  # The new functions image syncs the registry on start, and the core runner transforms at
  # once: declared raw tables, knob defaults, gates, and target sets must already exist.
  # Bootstrap is idempotent, so it also runs here, not only after mdp-postgres, once per deploy.
  if ! $build_only && ! $bootstrapped && [[ "$app" == mdp-functions || "$app" == mdp-core-runner ]]; then
    verify_pin bootstrap
    run bash ops/fly/bootstrap.sh
    bootstrapped=true
  fi
  if ! $build_only && [[ "$app" == mdp-postgres || "$app" == mdp-functions ]]; then
    [[ "$app" == mdp-functions ]] || args+=(--image-label "$image_label")
    fly_app "${args[@]}" --build-only --push
    wait_quiet
    restart=(deploy "$build_root" --config "$PWD/$config" --image "registry.fly.io/$app:$image_label" --ha=false --no-public-ips --yes)
    [[ "$app" != mdp-functions ]] || restart+=(--process-groups "api,workbench")
    fly_image "${restart[@]}"
  else
    fly_app "${args[@]}"
  fi
  if [[ "$app" == mdp-postgres ]] && ! $build_only; then verify_pin bootstrap; run bash ops/fly/bootstrap.sh; bootstrapped=true; fi
  if [[ "$app" == mdp-pg-frontend || "$app" == mdp-showcase ]] && ! $build_only; then
    # Fly lists a shared IPv4 as shared_v4. It serves the showcase's HTTPS; the frontend's raw TCP needs a dedicated v4.
    ipv4=v4; [[ "$app" != mdp-showcase ]] || ipv4='v4 shared_v4'
    if $dry || ! bash ops/fly/fly.sh ips list --json --org "$FLY_ORG" --app "$app" | python3 -c 'import json,sys; d=json.load(sys.stdin); d=d.get("addresses",d) if isinstance(d,dict) else d; sys.exit(not any(x.get("Type",x.get("type")) in sys.argv[1].split() for x in d))' "$ipv4"; then
      if [[ "$app" == mdp-showcase ]]; then fly_app ips allocate-v4 --shared --yes; else fly_app ips allocate-v4 --yes; fi
    fi
  fi
  if [[ "$app" == mdp-core-runner ]] && ! $build_only; then
    verify_pin 'the runner update'
    update_runners
    # A partial deploy still verifies both running service revisions before release.
    wanted mdp-functions || { start_due=true; rebuild_due=true; }
  fi
  if [[ "$app" == mdp-functions ]] && ! $build_only; then
    wait_healthy
    start_due=true
    wanted mdp-core-runner && rebuild_due=true
  fi
  echo "HOST $app.internal"
done
if $start_due; then start_runners; fi
if $rebuild_due; then rebuild_marts; fi

# Advisory checks start only after every app and rebuild completes. Never wait on them.
if ! $dry && ! $build_only; then
  canary_log=$(mktemp "${TMPDIR:-/tmp}/mdp-canaries.XXXXXX")
  nohup uv run --project functions python ops/fly/resilience-checks.py >"$canary_log" 2>&1 </dev/null &
  echo "CANARY advisory checks started; report: $canary_log"
fi
exit
}
