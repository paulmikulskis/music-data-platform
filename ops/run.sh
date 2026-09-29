#!/usr/bin/env bash
# Core's three-phase cadence job. Every command shares the same identity and vars.
set -euo pipefail
usage() { echo 'usage: ops/run.sh <hourly|daily|weekly> [--reason-category scheduled|other] [--cycle-id X] [--target pg|pg_local] [--vars JSON] [--dry-run]' >&2; exit 2; }
[[ $# -ge 1 ]] || usage
original_args=("$@")
cadence=$1; shift
case "$cadence" in hourly|daily|weekly) ;; *) usage ;; esac
reason=${MDP_RUN_REASON_CATEGORY:-scheduled}
run_target=${DBT_TARGET:-pg}
cycle_id=''
caller_vars='{}'
dry_run=false
while [[ $# -gt 0 ]]; do
  if [[ "$1" == --dry-run ]]; then
    dry_run=true; shift; continue
  fi
  [[ $# -ge 2 ]] || usage
  case "$1" in
    --reason-category) reason=$2 ;;
    --cycle-id) cycle_id=$2 ;;
    --target) run_target=$2 ;;
    --vars) caller_vars=$2 ;;
    *) usage ;;
  esac
  shift 2
done
case "$reason" in scheduled|other) ;; *) usage ;; esac
case "$run_target" in pg|pg_local) ;; *) usage ;; esac
repo_root=$(cd "$(dirname "$0")/.." && pwd)
if [[ -n "$cycle_id" ]]; then
  # A Replay and its restore are never scheduled runs.
  reason=other
  if ! $dry_run && [[ -z "${MDP_RUNNER_LOCKED:-}" ]]; then
    # The Replay's scope and tenant slug come from the cycle, never from the app default.
    export MDP_CONTROL_RT_URL="${MDP_CONTROL_RT_URL:-${MDP_CONTROL_RT_DATABASE_URL:-}}"
    : "${MDP_CONTROL_RT_URL:?Set the control_rt connection URL to read the replayed cycle}"
    # Only a closed cycle of this cadence replays; a refused Replay starts nothing and restores nothing.
    cycle_row=$(uv run --project "$repo_root/functions" python - "$cycle_id" "$cadence" <<'PY'
import os
import sys
import psycopg
# health_policy.AD_HOC_CYCLE_PREFIXES is the canonical list for scheduled cycles.
from mdp_functions.health_policy import scheduled_cycle_sql
with psycopg.connect(os.environ['MDP_CONTROL_RT_URL']) as conn:
    row = conn.execute(
        "SELECT c.scope,t.slug,c.status::text,c.cadence FROM control.cycle c "
        "LEFT JOIN control.tenant t ON c.scope='tenant:'||t.id::text WHERE c.id::text=%s",
        (sys.argv[1],),
    ).fetchone()
if not row or row[2] != 'closed' or row[3] != sys.argv[2]:
    raise SystemExit('replay_refused: Replay requires a closed cycle in this cadence and scope')
# Tenant Replay is not implemented; dbt.replay answers replay_unavailable.
if row[0] != 'global':
    raise SystemExit('replay_unavailable: tenant Replay is disabled until ')
print(row[0], row[1] or '')
PY
)
    read -r DBT_MDP_SCOPE cycle_slug <<< "$cycle_row"
    export DBT_MDP_SCOPE
    [[ -n "$cycle_slug" ]] && export MDP_TENANT_SLUG="$cycle_slug"
  fi
fi
export DBT_MDP_CADENCE=$cadence DBT_MDP_SCOPE="${DBT_MDP_SCOPE:-global}" MDP_RUNNER=core
case "$DBT_MDP_SCOPE" in
  global) scope=global ;;
  tenant:?*) scope=tenant ;;
  *) echo 'DBT_MDP_SCOPE must be global or tenant:<id>' >&2; exit 2 ;;
esac
# Scheduled runs, Retries and Replays of one (cadence, scope) serialize under one session lock for
# the whole run, as runs of one dbt Cloud job do; bind takes the separate cycle:<cadence>:<scope> lock.
lock_key="core:${cadence}:${DBT_MDP_SCOPE}"
if ! $dry_run && [[ "${MDP_RUNNER_LOCKED:-}" != "$lock_key" ]]; then
  export MDP_CONTROL_RT_URL="${MDP_CONTROL_RT_URL:-${MDP_CONTROL_RT_DATABASE_URL:-}}"
  : "${MDP_CONTROL_RT_URL:?Set the control_rt connection URL for the runner lock and job registration}"
  # The holder names its kind. A gated tick skips only behind this period's scheduled run; behind an
  # operator's Retry, Replay or restore, or the deploy's rebuild, it waits (30 min for hourly, 3 h
  # otherwise), then the gate decides.
  export MDP_RUNNER_KIND=other
  if [[ "$reason" == scheduled && -z "$cycle_id" && -z "${MDP_RESTORE:-}" ]]; then
    export MDP_RUNNER_KIND=scheduled
    if [[ "${MDP_CORE_GATE:-}" == 1 ]]; then
      export MDP_RUNNER_GATED=1
      [[ "$cadence" == hourly ]] && tick_wait=1800 || tick_wait=10800
      export MDP_RUNNER_LOCK_WAIT_S="${MDP_RUNNER_LOCK_WAIT_S:-$tick_wait}"
    fi
  fi
  export MDP_RUN_ID="${MDP_RUN_ID:-core:$(uv run --project "$repo_root/functions" python -c 'from uuid import uuid4; print(uuid4())')}"
  export DBT_CLOUD_RUN_ID="$MDP_RUN_ID" DBT_CLOUD_JOB_ID="core-${cadence}-${DBT_MDP_SCOPE}"
  exec uv run --project "$repo_root/functions" python "$repo_root/ops/runner_lock.py" "$lock_key" \
    bash "$repo_root/ops/run.sh" "${original_args[@]}"
fi
merged_vars=$(uv run --project "$repo_root/functions" python - "$caller_vars" "$cycle_id" <<'PY'
import json
import os
import sys
try:
    values = json.loads(sys.argv[1])
except json.JSONDecodeError:
    raise SystemExit('--vars must be a JSON object') from None
if not isinstance(values, dict):
    raise SystemExit('--vars must be a JSON object')
if sys.argv[2]:
    values['cycle_id'] = sys.argv[2]
if os.environ['DBT_MDP_SCOPE'].startswith('tenant:'):
    if 'tenant_slug' not in values and os.environ.get('MDP_TENANT_SLUG'):
        values['tenant_slug'] = os.environ['MDP_TENANT_SLUG']
    if not isinstance(values.get('tenant_slug'), str) or not values['tenant_slug'].strip():
        raise SystemExit('tenant scope requires tenant_slug in --vars or MDP_TENANT_SLUG')
print(json.dumps(values, separators=(',', ':'), sort_keys=True))
PY
)
# The restore after a Replay runs with the same vars, minus the replayed cycle.
restore_vars=$merged_vars
if [[ -n "$cycle_id" ]]; then
  restore_vars=$(uv run --project "$repo_root/functions" python -c \
    'import json,sys; v=json.loads(sys.argv[1]); v.pop("cycle_id"); print(json.dumps(v, separators=(",", ":"), sort_keys=True))' \
    "$merged_vars")
fi
export DBT_CLOUD_RUN_ID="${MDP_RUN_ID:-core:$(uv run --project "$repo_root/functions" python -c 'from uuid import uuid4; print(uuid4())')}"
export DBT_CLOUD_JOB_ID="core-${cadence}-${DBT_MDP_SCOPE}"
export DBT_CLOUD_RUN_REASON_CATEGORY=$reason
if ! $dry_run; then
  export MDP_CONTROL_RT_URL="${MDP_CONTROL_RT_URL:-${MDP_CONTROL_RT_DATABASE_URL:-}}"
  : "${MDP_CONTROL_RT_URL:?Set the control_rt connection URL for job registration}"
  uv run --project "$repo_root/functions" python - <<'PY'
import os
import psycopg
from mdp_functions.core_gate import defaults
from mdp_functions.exporter import declared_export_kinds, declared_global_inputs
job = (os.environ['DBT_CLOUD_JOB_ID'], os.environ['DBT_MDP_CADENCE'], os.environ['DBT_MDP_SCOPE'])
# A tenant job's global inputs come from the generated mdp_global_inputs(); bind checks the same list.
inputs = declared_global_inputs().get(job[1], []) if job[2].startswith('tenant:') else []
with psycopg.connect(os.environ['MDP_CONTROL_RT_URL']) as conn:
    # Every registration upserts global_inputs, so a changed mdp_global_inputs() never leaves a stale list.
    # A new schedule starts in UTC; registration preserves operator schedule settings.
    conn.execute(
        "INSERT INTO control.dbt_job(job_id,runner,cadence,scope,global_inputs,due_hour,due_weekday,timezone) "
        "VALUES (%s,'core',%s,%s,%s,%s,%s,'UTC') "
        "ON CONFLICT(job_id) DO UPDATE SET global_inputs=EXCLUDED.global_inputs",
        (*job, inputs, *defaults(job[1], job[2])),
    )
    row = conn.execute('SELECT runner,cadence,scope FROM control.dbt_job WHERE job_id=%s', (job[0],)).fetchone()
    if row != ('core', job[1], job[2]):
        raise SystemExit('scope_mismatch: existing Core job registration differs')

PY
else
  preview_dir=$(mktemp -d)
fi
restore_state() { uv run --project "$repo_root/functions" python "$repo_root/ops/runner_restore.py" "$@"; }
if ! $dry_run && [[ -z "${MDP_RESTORE:-}" ]]; then
  # A kill or a machine stop can end a Replay before its restore. Every later run under the lock,
  # except a restore itself, first runs the restore that control.runner_restore holds for it.
  pending=$(restore_state pending "$lock_key")
  if [[ -n "$pending" ]]; then
    read -r pending_cycle pending_target pending_vars <<< "$pending"
    echo "RESTORE PENDING ${lock_key}: the Replay of ${pending_cycle} ended before its restore"
    if MDP_RESTORE=1 env -u MDP_RUN_ID bash "$repo_root/ops/run.sh" "$cadence" --reason-category other \
        --target "$pending_target" --vars "$pending_vars"; then
      restore_state clear "$lock_key" "$pending_cycle"
    else
      echo "RESTORE PENDING FAILED ${lock_key}; kept for the next run under this lock" >&2
    fi
  fi
fi
if [[ "${MDP_CORE_GATE:-}" == 1 && "$reason" == scheduled && -z "$cycle_id" && -z "${MDP_RESTORE:-}" ]]; then
  # Fly schedules are fuzzy hourly ticks, not cron: a scheduled machine's tick (MDP_CORE_GATE=1) runs
  # only when the job's declared schedule is due, and exits before binding otherwise.
  if $dry_run; then
    echo "GATE skipped in a dry run: a scheduled tick runs only when ${DBT_CLOUD_JOB_ID} is due"
  else
    gate=0
    decision=$(uv run --project "$repo_root/functions" python -m mdp_functions.core_gate "$DBT_CLOUD_JOB_ID") || gate=$?
    echo "GATE ${DBT_CLOUD_JOB_ID}: ${decision}"
    if ((gate == 3)); then exit 0; fi
    ((gate == 0)) || exit "$gate"
  fi
fi
if [[ -n "${MDP_RESTORE:-}" && -z "$cycle_id" ]] && ! $dry_run; then
  # A restore rebuilds the build of the newest non-superseded scheduled cycle (the deploy's rebuild is
  # one); with no such cycle the previous release built nothing here, so there is nothing to restore.
  scheduled_cycles=$(uv run --project "$repo_root/functions" python - <<'PY'
import os
import psycopg
# health_policy.AD_HOC_CYCLE_PREFIXES is the canonical list for scheduled cycles.
from mdp_functions.health_policy import scheduled_cycle_sql
with psycopg.connect(os.environ['MDP_CONTROL_RT_URL']) as conn:
    print(conn.execute(
        "SELECT count(*) FROM control.cycle WHERE cadence=%s AND scope=%s AND status<>'superseded' "
        f"AND {scheduled_cycle_sql()}",
        (os.environ['DBT_MDP_CADENCE'], os.environ['DBT_MDP_SCOPE']),
    ).fetchone()[0])
PY
)
  if [[ "$scheduled_cycles" == 0 ]]; then
    echo "RESTORE SKIPPED ${cadence}; scope=${DBT_MDP_SCOPE}: no scheduled cycle to rebuild"
    exit 0
  fi
fi
if [[ -n "$cycle_id" ]] && ! $dry_run; then
  # Durable before the Replay touches a live relation; cleared only after its restore passes.
  restore_state mark "$lock_key" "$cycle_id" "$run_target" "$restore_vars"
fi
on_exit() {
  local status=$?
  trap - EXIT
  if ((status)) && ! $dry_run; then
    uv run --project "$repo_root/functions" python -m mdp_functions.cadence_health \
      "${DBT_CLOUD_RUN_ID:-}" "$repo_root/dbt/target" "$status" || echo 'ALERT cadence_failed delivery failed' >&2
    # A build that read a MusicBrainz generation retention deleted failed with
    # reference_generation_incomplete; the service opens its alert for this run.
    uv run --project "$repo_root/functions" python -m mdp_functions.reference report-incomplete \
      "${DBT_CLOUD_RUN_ID:-}" "$repo_root/dbt/target" || true
  fi
  if [[ -n "$cycle_id" ]]; then
    # A Replay rebuilt live relations from an older manifest. Still under the lock, and even when
    # the Replay failed or was interrupted, a normal full run with a new run id restores the current
    # build, bound to the newest non-superseded scheduled cycle.
    echo "RESTORE ${cadence}; scope=${DBT_MDP_SCOPE}: current build after the Replay of ${cycle_id}"
    restore=("$cadence" --reason-category other --target "$run_target" --vars "$restore_vars")
    $dry_run && restore+=(--dry-run)
    local restored=0
    MDP_RESTORE=1 env -u MDP_RUN_ID bash "$repo_root/ops/run.sh" "${restore[@]}" || restored=$?
    if ((restored == 0)) && ! $dry_run; then
      restore_state clear "$lock_key" "$cycle_id" || restored=$?
    fi
    ((status)) || status=$restored
  fi
  [[ -n "${preview_dir:-}" ]] && rm -rf "$preview_dir"
  exit "$status"
}
trap on_exit EXIT
# INT and TERM end the run, so the EXIT trap runs the restore. dbt runs as a background job in its
# own process group, because bash defers a trap while a foreground command runs, and a background
# command without job control ignores SIGINT. The trap sends SIGINT to that group either way:
# uv does not forward SIGINT, and dbt cancels its open queries on SIGINT.
child=''
interrupted=0
on_signal() {
  # Once: Ctrl-C reaches this shell directly and again through runner_lock.py.
  [[ "$interrupted" == 0 ]] || return 0
  interrupted=$1
  [[ -z "$child" ]] || kill -s INT -- "-$child" 2>/dev/null || true
}
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
wait_child() {
  local status=0
  # wait returns early for each trapped signal; the child is waited for until it exits.
  while :; do
    wait "$child" && status=0 || status=$?
    kill -0 "$child" 2>/dev/null || break
  done
  child=''
  [[ "$interrupted" == 0 ]] || return 1
  return "$status"
}
dbt_cmd() {
  [[ "$interrupted" == 0 ]] || return 1
  local command=(uv run --project "$repo_root/dbt" dbt "$@" --project-dir "$repo_root/dbt"
    --profiles-dir "$repo_root/dbt/profiles" --target "$run_target" --vars "$merged_vars")
  if $dry_run; then
    printf 'COMMAND'; printf ' %q' "${command[@]}"; printf '\n'
    if [[ "$1" == build ]]; then
      # ls parses the same graph offline; it neither runs hooks nor connects to PG.
      MDP_PG_PASSWORD="${MDP_PG_PASSWORD:-unused}" MDP_DEV_DB="${MDP_DEV_DB:-$preview_dir/dev.duckdb}" \
        uv run --project "$repo_root/dbt" dbt ls --project-dir "$repo_root/dbt" \
        --profiles-dir "$repo_root/dbt/profiles" --target ci --vars "$merged_vars" \
        --target-path "$preview_dir/target" --log-path "$preview_dir/logs" \
        --selector "$3" --resource-type model --output json --output-keys name config --quiet |
        uv run --project "$repo_root/functions" python -c '
import json, sys
scope = sys.argv[1]
for line in sys.stdin:
    if not line.startswith("{"):
        continue
    model = json.loads(line)
    tags = [t for t in model["config"]["tags"] if t.startswith("scope:")]
    if tags != ["scope:" + scope]:
        raise SystemExit("scope_mismatch: " + model["name"])
    print("SELECTED " + model["name"] + " " + tags[0])
' "$scope"
    fi
  else
    # A failed command must never report a previous phase's model error.
    rm -f "$repo_root/dbt/target/run_results.json"
    set -m
    "${command[@]}" &
    child=$!
    set +m
    wait_child
  fi
}
echo "PHASE 1 bronze: ${cadence}; scope=${DBT_MDP_SCOPE}; run=${DBT_CLOUD_RUN_ID}"
dbt_cmd build --selector "${cadence}_${scope}_bronze"
$dry_run || echo 'PHASE 1 PASS'
# Reads and landings must belong to this job's cadence AND scope.
freshness=$(uv run --project "$repo_root/functions" python - <<'PY'
import os
from mdp_functions.registry import discover
is_tenant = os.environ['DBT_MDP_SCOPE'].startswith('tenant:')
catalog = [m for m in discover().values()
           if m.cadence == os.environ['DBT_MDP_CADENCE'] and m.kind == 'invoke'
           and m.tenant_bound == is_tenant]
reads = {r for m in catalog for r in m.reads}
landed = {r for m in catalog if m.layer == 'bronze' for r in m.writes}
print(' '.join('source:' + r for r in sorted(reads - landed) if r.startswith('raw.')))
PY
)
if [[ -n "$freshness" ]]; then
  read -r -a source_selectors <<< "$freshness"
  dbt_cmd source freshness --select "${source_selectors[@]}"
  $dry_run || echo 'PHASE 2 PASS source freshness'
else
  echo "PHASE 2 NOTE: ${cadence}/${scope} reads no external-to-cadence sources; freshness skipped"
fi
dbt_cmd build --selector "${cadence}_${scope}_transform"
$dry_run || echo 'PHASE 3 PASS transform'

if [[ -z "$cycle_id" ]]; then
  # Every build but a Replay is bound to the current scheduled cycle, so its success resolves that
  # cycle's cadence alerts: a scheduled build, a Retry and a restore. A Replay rebuilt an older
  # manifest and proves nothing about the cadence. A lost notice leaves the alert for the next build.
  if $dry_run; then
    echo "WOULD RECORD build success for ${DBT_CLOUD_RUN_ID}"
  else
    uv run --project "$repo_root/functions" python -m mdp_functions.cadence_health --succeeded "$DBT_CLOUD_RUN_ID" ||
      echo 'BUILD success not recorded; the next build resolves its alert. Check /ops.' >&2
  fi
  if [[ "$reason" == scheduled && -z "${MDP_RESTORE:-}" ]]; then
    # The heartbeat stays scheduled-only: manual work, Retry and restore send nothing.
    if $dry_run; then
      echo "WOULD SEND heartbeat for ${DBT_CLOUD_RUN_ID}"
    else
      uv run --project "$repo_root/functions" python "$repo_root/ops/heartbeat.py" "$DBT_CLOUD_RUN_ID"
    fi
  fi
fi
