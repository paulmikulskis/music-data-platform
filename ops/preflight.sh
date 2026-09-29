#!/usr/bin/env bash
# ops/preflight.sh: verifies every input a local run needs and reports.
#
#   bash ops/preflight.sh [--stage minimal|full] [--json]
#
# One line per input group:
#   PASS <name> <detail>            the probe succeeded; detail names an identity (email, org, project), never a credential
#   MISSING <name> <how to supply>  the input is absent; detail is the one command or URL that yields it
#   FAIL <name> <probe error>       the input is present but the probe failed
#   NOTE <name> <text>              informational; never affects the exit code
# Load order (first non-empty value wins): process env > inputs/inputs.env > secret store.
# --stage minimal requires fly.token fly.org fly.region r2 dbtcloud secret_store fixture_targets billboard budgets github and every local.*;
# --stage full adds litellm otlp clerk snowflake; without --stage everything is required.
# Every line is always printed. Exit 0 when every required line is PASS, 1 otherwise.
# The report is also written to ops/evidence/preflight/<UTC timestamp>.txt and its path is printed last
# (on stderr in --json mode so stdout stays a JSON array); when that write fails the last line is a NOTE instead.
# Exit 2 when temp files cannot be created (FAIL local.tmp) or on a bad flag.
# Deliberately not set -e: every probe must run. Runs on bash 3.2 (macOS) and bash 5 (CI).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

# ---------------------------------------------------------------- flags
STAGE="all"
JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    --stage)
      if [ $# -lt 2 ]; then echo "--stage needs a value" >&2; exit 2; fi
      STAGE="$2"; shift 2 ;;
    --stage=*) STAGE="${1#--stage=}"; shift ;;
    --json) JSON=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1 (see --help)" >&2; exit 2 ;;
  esac
done
case "$STAGE" in minimal|full|all) ;; *) STAGE="all" ;; esac

# ---------------------------------------------------------------- helpers
if ! ERRF="$(mktemp 2>/dev/null)" || [ -z "$ERRF" ]; then
  echo "FAIL local.tmp cannot create temp files"; exit 2
fi
if ! BODYF="$(mktemp 2>/dev/null)" || [ -z "$BODYF" ]; then
  rm -f "$ERRF"; echo "FAIL local.tmp cannot create temp files"; exit 2
fi
trap 'rm -f "$ERRF" "$BODYF"' EXIT

NOTES=()
NAMES=()
STATUSES=()
DETAILS=()
LOADED_N=0
FLY_TOKEN_SOURCE=""
FLY_TOKEN_OK=0
FLY_REGION_DEFAULTED=0

# Fly region codes as printed by `fly platform regions --json` (deprecated=false), copied 2026-09-18 UTC.
# Used only when the live list cannot be fetched (no working Fly token).
FLY_REGIONS_STATIC="ams arn cdg dfw ewr fra gru iad jnb lax lhr nrt ord sin sjc syd yyz"

# values that must never appear in output; redact() scrubs them from every detail line
SECRET_VARS="FLY_API_TOKEN R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY DBT_CLOUD_TOKEN SECRET_STORE_TOKEN LITELLM_ADMIN_KEY OTLP_AUTH_HEADER CLERK_SECRET_KEY SNOWFLAKE_PASSWORD"

# every CLI call is bounded; without a timeout utility the CLI-bound probes do not run at all (they FAIL with NO_TIMEOUT_REASON)
NO_TIMEOUT_REASON="no timeout utility (install coreutils)"
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
elif command -v perl >/dev/null 2>&1; then
  TIMEOUT_BIN="perl"
fi

with_timeout() { # with_timeout SECONDS cmd args... ; exit 126 without running the command when nothing can bound it
  local secs="$1"
  shift
  case "$TIMEOUT_BIN" in
    timeout|gtimeout) "$TIMEOUT_BIN" "$secs" "$@" ;;
    perl) perl -e 'alarm shift; exec @ARGV' "$secs" "$@" ;;
    *) printf '%s\n' "$NO_TIMEOUT_REASON" >&2; return 126 ;;
  esac
}

note() { NOTES+=("NOTE $*"); }

redact() { # replace any known secret value found in $1 with ***
  local s="$1" name v
  for name in $SECRET_VARS; do
    v="${!name:-}"
    if [ -n "$v" ]; then s="${s//"$v"/***}"; fi
  done
  printf '%s' "$s"
}

one_line() { # collapse whitespace to a single line and cap the length
  local s
  s="$(printf '%s' "$1" | tr '\r\n\t' '   ' | sed -E 's/  +/ /g; s/^ //; s/ $//')"
  if [ ${#s} -gt 400 ]; then s="${s:0:400}..."; fi
  printf '%s' "$s"
}

result() { # result STATUS NAME DETAIL ; the raw detail is redacted in full before it is normalized or truncated
  local detail
  detail="$(one_line "$(redact "$3")")"
  NAMES+=("$2")
  STATUSES+=("$1")
  DETAILS+=("$detail")
}

err_line() { one_line "$(redact "$(cat "$ERRF")")"; } # whole file redacted first, truncated after

missing_vars() { # prints the names among "$@" that are unset or empty
  local out="" n
  for n in "$@"; do
    if [ -z "${!n:-}" ]; then out="$out $n"; fi
  done
  printf '%s' "${out# }"
}

json_field() { # json_field FILE JQ_PATH ; empty when jq is absent or the path is missing
  if command -v jq >/dev/null 2>&1; then jq -r "$2 // empty" "$1" 2>/dev/null; fi
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

is_uint() { local re='^[0-9]+$'; [[ "$1" =~ $re ]]; }

dbt_api_base() { # https://<host>/api, or <scheme>://<host>/api when DBT_CLOUD_HOST carries a scheme (local mock)
  case "$DBT_CLOUD_HOST" in
    http://*|https://*) printf '%s/api' "${DBT_CLOUD_HOST%/}" ;;
    *) printf 'https://%s/api' "$DBT_CLOUD_HOST" ;;
  esac
}

repo_full_name() { # owner/name from a repository full_name or a git remote url; empty stays empty
  local r="$1"
  r="${r%/}"
  r="${r%.git}"
  case "$r" in
    git@*:*) r="${r#*:}" ;;
    *://*) r="${r#*://}"; r="${r#*/}" ;;
  esac
  printf '%s' "$r"
}

http_code() { # http_code URL curl-args... ; prints the status code, 000 on a connection error; body in $BODYF, stderr in $ERRF
  local url="$1" code
  shift
  if code="$(curl -sS --max-time 10 -o "$BODYF" -w '%{http_code}' "$@" "$url" 2>"$ERRF")"; then
    printf '%s' "${code:-000}"
  else
    printf '000'
  fi
}

# ---------------------------------------------------------------- load inputs
unescape_env_value() { # strips one layer of quotes; inside double quotes handles \" \\ and \n
  local v="$1" ph=$'\001'
  case "$v" in
    \"*\")
      v="${v#\"}"; v="${v%\"}"
      v="${v//\\\\/$ph}"; v="${v//\\\"/\"}"; v="${v//\\n/$'\n'}"; v="${v//$ph/\\}" ;;
    \'*\')
      v="${v#\'}"; v="${v%\'}" ;;
  esac
  printf '%s' "$v"
}

load_env_lines() { # stdin: KEY=value lines; exports keys that are still unset or empty; count in LOADED_N
  local line key val keyre='^[A-Za-z_][A-Za-z0-9_]*$'
  LOADED_N=0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    line="${line#export }"
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    val="${line#*=}"
    if ! [[ "$key" =~ $keyre ]]; then continue; fi
    if [ -z "${!key:-}" ]; then
      val="$(unescape_env_value "$val")"
      export "$key=$val"
      LOADED_N=$((LOADED_N + 1))
    fi
  done
}

# 1. process env is already in place. 2. inputs/inputs.env
if [ -f inputs/inputs.env ]; then
  load_env_lines < inputs/inputs.env
  note "inputs.env inputs/inputs.env loaded ($LOADED_N new keys; process env wins)"
fi

SECRET_STORE_PROJECT="${SECRET_STORE_PROJECT:-}"
SECRET_STORE_CONFIG="${SECRET_STORE_CONFIG:-}"
export SECRET_STORE_PROJECT SECRET_STORE_CONFIG

# 3. Operator-supplied secret-store adapter (configured authentication or SECRET_STORE_TOKEN)
SECRET_STORE_LOAD="skipped"
if command -v secret_store >/dev/null 2>&1; then
  if secret_store_env="$(with_timeout 15 secret_store export env 2>/dev/null)" && [ -n "$secret_store_env" ]; then
    load_env_lines <<< "$secret_store_env"
    note "secret_store $SECRET_STORE_PROJECT/$SECRET_STORE_CONFIG loaded ($LOADED_N new keys)"
    SECRET_STORE_LOAD="ok"
  else
    note "secret_store $SECRET_STORE_PROJECT/$SECRET_STORE_CONFIG not loaded (adapter not authenticated, or project/config unreachable); continuing with env and inputs.env"
    SECRET_STORE_LOAD="error"
  fi
  unset secret_store_env
else
  note "Operator-supplied secret_store adapter unavailable; configuration load skipped"
fi

# 4. Fly token fallback
if [ -z "${FLY_API_TOKEN:-}" ] && [ -f "$HOME/.fly/config.yml" ]; then
  FLY_API_TOKEN="$(sed -nE 's/^access_token: *//p' "$HOME/.fly/config.yml" | sed -E 's/^"//; s/"$//' | head -1)"
  if [ -n "$FLY_API_TOKEN" ]; then
    export FLY_API_TOKEN
    FLY_TOKEN_SOURCE="fly config.yml"
    note "fly.token FLY_API_TOKEN unset; using the access_token from ~/.fly/config.yml (the CLI login, not an operator org token)"
  fi
fi

# 5. defaults
if [ -z "${FLY_REGION:-}" ]; then FLY_REGION="ewr"; FLY_REGION_DEFAULTED=1; fi
DBT_CLOUD_HOST="${DBT_CLOUD_HOST:-cloud.getdbt.com}"
GITHUB_REPO="${GITHUB_REPO:-}"
MDP_FIXTURE_TARGETS="${MDP_FIXTURE_TARGETS:-inputs/fixture_targets.csv}"
export FLY_REGION DBT_CLOUD_HOST GITHUB_REPO MDP_FIXTURE_TARGETS

# ---------------------------------------------------------------- probes
probe_fly_token() {
  local out rc
  if ! command -v fly >/dev/null 2>&1; then
    result MISSING fly.token "install flyctl: curl -L https://fly.io/install.sh | sh"
    return
  fi
  if [ -z "${FLY_API_TOKEN:-}" ]; then
    result MISSING fly.token "fly auth login, or set FLY_API_TOKEN from: fly tokens create org -o <FLY_ORG>"
    return
  fi
  out="$(with_timeout 15 fly auth whoami 2>"$ERRF")"
  rc=$?
  out="$(redact "$out")"
  if [ $rc -eq 0 ] && [ -n "$out" ]; then
    FLY_TOKEN_OK=1
    result PASS fly.token "$out${FLY_TOKEN_SOURCE:+ (token from $FLY_TOKEN_SOURCE)}"
  else
    result FAIL fly.token "fly auth whoami exit $rc: $(err_line)"
  fi
}

probe_fly_org() {
  local out rc
  if [ -z "${FLY_ORG:-}" ]; then
    result MISSING fly.org "fly orgs create <slug> (operator-owned), add the org card at https://fly.io/dashboard/<slug>/billing, then set FLY_ORG=<slug>"
    return
  fi
  if [ -z "${FLY_API_TOKEN:-}" ] || ! command -v fly >/dev/null 2>&1; then
    result MISSING fly.org "needs fly.token first (FLY_ORG=$FLY_ORG is set)"
    return
  fi
  out="$(with_timeout 15 fly orgs list --json 2>"$ERRF")"
  rc=$?
  out="$(redact "$out")"
  if [ $rc -ne 0 ]; then
    result FAIL fly.org "fly orgs list exit $rc: $(err_line)"
    return
  fi
  if printf '%s' "$out" | grep -q -- "\"$FLY_ORG\": *\""; then
    result PASS fly.org "$FLY_ORG (token is a member)"
  else
    result FAIL fly.org "token is not a member of org $FLY_ORG (fly orgs list)"
  fi
}

probe_fly_region() {
  local re='^[a-z]{3}$' out rc source="" name="" deprecated="" suffix=""
  if [ "$FLY_REGION_DEFAULTED" -eq 1 ]; then suffix=" (default)"; fi
  if ! [[ "$FLY_REGION" =~ $re ]]; then
    result FAIL fly.region "FLY_REGION=$FLY_REGION is not a three-letter Fly region code (fly platform regions)"
    return
  fi
  if [ "$FLY_TOKEN_OK" -eq 1 ] && command -v jq >/dev/null 2>&1; then
    out="$(with_timeout 15 fly platform regions --json 2>"$ERRF")"
    rc=$?
    out="$(redact "$out")"
    if [ $rc -eq 0 ] && [ -n "$out" ]; then
      source="fly platform regions"
      name="$(printf '%s' "$out" | jq -r --arg c "$FLY_REGION" '.[] | select(.code == $c) | .name // empty' 2>/dev/null | head -1)"
      deprecated="$(printf '%s' "$out" | jq -r --arg c "$FLY_REGION" '.[] | select(.code == $c) | .deprecated // false' 2>/dev/null | head -1)"
      if [ -z "$name" ]; then
        result FAIL fly.region "FLY_REGION=$FLY_REGION is not a Fly region ($source)"
        return
      fi
      if [ "$deprecated" = "true" ]; then
        result FAIL fly.region "FLY_REGION=$FLY_REGION ($name) is deprecated ($source)"
        return
      fi
      result PASS fly.region "$FLY_REGION $name$suffix ($source)"
      return
    fi
    note "fly.region fly platform regions failed (exit $rc); checking FLY_REGION against the static list copied 2026-09-18"
  fi
  source="static list copied 2026-09-18"
  case " $FLY_REGIONS_STATIC " in
    *" $FLY_REGION "*) result PASS fly.region "$FLY_REGION$suffix ($source)" ;;
    *) result FAIL fly.region "FLY_REGION=$FLY_REGION is not a Fly region ($source; run fly platform regions)" ;;
  esac
}

probe_r2() {
  local miss endpoint key stamp
  miss="$(missing_vars R2_ACCOUNT_ID R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)"
  if [ -n "$miss" ]; then
    result MISSING r2 "set $miss: https://dash.cloudflare.com -> R2 -> Manage R2 API Tokens (Object Read & Write on the bucket)"
    return
  fi
  endpoint="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  key="mdp-preflight/${stamp}-$$.txt"
  if command -v rclone >/dev/null 2>&1; then
    export RCLONE_CONFIG_MDP_TYPE=s3 RCLONE_CONFIG_MDP_PROVIDER=Cloudflare RCLONE_CONFIG_MDP_ENDPOINT="$endpoint" \
      RCLONE_CONFIG_MDP_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" RCLONE_CONFIG_MDP_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
      RCLONE_CONFIG_MDP_ACL=private RCLONE_CONFIG_MDP_NO_CHECK_BUCKET=true
    if ! with_timeout 15 rclone lsf "mdp:$R2_BUCKET" --max-depth 1 --contimeout 10s --timeout 10s >/dev/null 2>"$ERRF"; then
      result FAIL r2 "list bucket $R2_BUCKET at $endpoint: $(err_line)"
    elif ! printf 'preflight %s\n' "$stamp" | with_timeout 15 rclone rcat "mdp:$R2_BUCKET/$key" --contimeout 10s --timeout 10s 2>"$ERRF"; then
      result FAIL r2 "put $key in $R2_BUCKET: $(err_line)"
    elif ! with_timeout 15 rclone deletefile "mdp:$R2_BUCKET/$key" --contimeout 10s --timeout 10s 2>"$ERRF"; then
      result FAIL r2 "delete $key in $R2_BUCKET: $(err_line)"
    else
      result PASS r2 "bucket $R2_BUCKET list+put+delete ok via rclone ($endpoint)"
    fi
    unset RCLONE_CONFIG_MDP_TYPE RCLONE_CONFIG_MDP_PROVIDER RCLONE_CONFIG_MDP_ENDPOINT RCLONE_CONFIG_MDP_ACCESS_KEY_ID \
      RCLONE_CONFIG_MDP_SECRET_ACCESS_KEY RCLONE_CONFIG_MDP_ACL RCLONE_CONFIG_MDP_NO_CHECK_BUCKET
  elif command -v aws >/dev/null 2>&1; then
    export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" AWS_DEFAULT_REGION=auto AWS_EC2_METADATA_DISABLED=true
    printf 'preflight %s\n' "$stamp" > "$BODYF"
    if ! with_timeout 15 aws s3api list-objects-v2 --bucket "$R2_BUCKET" --max-keys 1 --endpoint-url "$endpoint" >/dev/null 2>"$ERRF"; then
      result FAIL r2 "list bucket $R2_BUCKET at $endpoint: $(err_line)"
    elif ! with_timeout 15 aws s3api put-object --bucket "$R2_BUCKET" --key "$key" --body "$BODYF" --endpoint-url "$endpoint" >/dev/null 2>"$ERRF"; then
      result FAIL r2 "put $key in $R2_BUCKET: $(err_line)"
    elif ! with_timeout 15 aws s3api delete-object --bucket "$R2_BUCKET" --key "$key" --endpoint-url "$endpoint" >/dev/null 2>"$ERRF"; then
      result FAIL r2 "delete $key in $R2_BUCKET: $(err_line)"
    else
      result PASS r2 "bucket $R2_BUCKET list+put+delete ok via aws s3api ($endpoint)"
    fi
    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION AWS_EC2_METADATA_DISABLED
  else
    result FAIL r2 "neither rclone nor the aws cli is installed (brew install rclone)"
  fi
}

dbt_env_check() { # dbt_env_check ROLE ENV_ID -> sets DBT_ENV_NAME; prints a mismatch reason or nothing
  local role="$1" want="$2" got_id got_proj dtype etype
  got_id="$(json_field "$BODYF" '.data.id')"
  got_proj="$(json_field "$BODYF" '.data.project_id')"
  dtype="$(json_field "$BODYF" '.data.deployment_type')"
  etype="$(json_field "$BODYF" '.data.type')"
  DBT_ENV_NAME="$(json_field "$BODYF" '.data.name')"
  if [ "$got_id" != "$want" ]; then printf 'environment %s returned id %s, expected %s' "$role" "${got_id:-none}" "$want"; return; fi
  if [ "$got_proj" != "$DBT_CLOUD_PROJECT_ID" ]; then printf 'environment %s (%s) belongs to project %s, expected %s' "$role" "$want" "${got_proj:-none}" "$DBT_CLOUD_PROJECT_ID"; return; fi
  case "$role" in
    prod)
      if [ -n "$dtype" ]; then
        if [ "$dtype" != "production" ]; then printf 'environment prod (%s) has deployment_type %s, expected production' "$want" "$dtype"; fi
      else printf 'environment prod (%s) has no deployment_type (type %s); cannot confirm it is the production environment' "$want" "${etype:-none}"; fi ;;
    ci)
      if [ "$dtype" = "production" ]; then printf 'environment ci (%s) has deployment_type production; the CI environment must not be the production one' "$want"; fi ;;
  esac
}

dbt_delete() { # dbt_delete URL FALLBACK_URL -> prints the final http code; tries the fallback path on 404/405
  local code
  code="$(http_code "$1" -X DELETE -H "$DBT_AUTH" -H 'Accept: application/json')"
  case "$code" in 404|405) code="$(http_code "$2" -X DELETE -H "$DBT_AUTH" -H 'Accept: application/json')" ;; esac
  printf '%s' "$code"
}

DBT_AUTH=""
DBT_ENV_NAME=""
probe_dbtcloud() {
  local miss code name env_prod env_ci repo base acct="${DBT_CLOUD_ACCOUNT_ID:-}" proj="${DBT_CLOUD_PROJECT_ID:-}" why
  local epoch probe_name job_id="" hook_id="" job_body hook_body job_del="" hook_del="" leftover=""
  miss="$(missing_vars DBT_CLOUD_ACCOUNT_ID DBT_CLOUD_PROJECT_ID DBT_CLOUD_ENV_ID_PROD DBT_CLOUD_ENV_ID_CI DBT_CLOUD_TOKEN)"
  if [ -n "$miss" ]; then
    result MISSING dbtcloud "set $miss: service token (Job Admin + Webhooks) at https://$DBT_CLOUD_HOST/settings/accounts/<account>/pages/service-tokens; ids are in the project and environment URLs; link $GITHUB_REPO as the project repository"
    return
  fi
  if ! command -v jq >/dev/null 2>&1; then
    result FAIL dbtcloud "jq is needed to read the dbt Cloud API responses (brew install jq)"
    return
  fi
  if ! is_uint "$acct" || ! is_uint "$proj" || ! is_uint "$DBT_CLOUD_ENV_ID_PROD" || ! is_uint "$DBT_CLOUD_ENV_ID_CI"; then
    result FAIL dbtcloud "DBT_CLOUD_ACCOUNT_ID, DBT_CLOUD_PROJECT_ID, DBT_CLOUD_ENV_ID_PROD and DBT_CLOUD_ENV_ID_CI must be numeric ids"
    return
  fi
  if [ "$DBT_CLOUD_ENV_ID_PROD" = "$DBT_CLOUD_ENV_ID_CI" ]; then
    result FAIL dbtcloud "DBT_CLOUD_ENV_ID_PROD and DBT_CLOUD_ENV_ID_CI are both $DBT_CLOUD_ENV_ID_CI; the two environments must differ"
    return
  fi
  base="$(dbt_api_base)"
  DBT_AUTH="Authorization: Token $DBT_CLOUD_TOKEN"
  # 1. project read; the record carries the linked repository
  code="$(http_code "$base/v2/accounts/$acct/projects/$proj/" -H "$DBT_AUTH" -H 'Accept: application/json')"
  case "$code" in
    200) ;;
    000) result FAIL dbtcloud "no HTTP response from $DBT_CLOUD_HOST: $(err_line)"; return ;;
    *) result FAIL dbtcloud "http $code from project $proj read (401/403: token rejected; 404: account or project id)"; return ;;
  esac
  name="$(json_field "$BODYF" '.data.name')"
  repo="$(repo_full_name "$(json_field "$BODYF" '.data.repository.full_name // .data.repository.remote_url')")"
  # 2. both environments: id echoes back, same project, prod is production, ci is not
  code="$(http_code "$base/v2/accounts/$acct/environments/$DBT_CLOUD_ENV_ID_PROD/" -H "$DBT_AUTH" -H 'Accept: application/json')"
  if [ "$code" != "200" ]; then result FAIL dbtcloud "http $code reading environment $DBT_CLOUD_ENV_ID_PROD (DBT_CLOUD_ENV_ID_PROD)"; return; fi
  why="$(dbt_env_check prod "$DBT_CLOUD_ENV_ID_PROD")"
  if [ -n "$why" ]; then result FAIL dbtcloud "$why"; return; fi
  env_prod="$DBT_ENV_NAME"
  code="$(http_code "$base/v2/accounts/$acct/environments/$DBT_CLOUD_ENV_ID_CI/" -H "$DBT_AUTH" -H 'Accept: application/json')"
  if [ "$code" != "200" ]; then result FAIL dbtcloud "http $code reading environment $DBT_CLOUD_ENV_ID_CI (DBT_CLOUD_ENV_ID_CI)"; return; fi
  why="$(dbt_env_check ci "$DBT_CLOUD_ENV_ID_CI")"
  if [ -n "$why" ]; then result FAIL dbtcloud "$why"; return; fi
  env_ci="$DBT_ENV_NAME"
  # 3. read checks: jobs of the project and webhook subscriptions must list
  code="$(http_code "$base/v2/accounts/$acct/jobs/?project_id=$proj" -H "$DBT_AUTH" -H 'Accept: application/json')"
  case "$code" in 2??) ;; *) result FAIL dbtcloud "http $code listing jobs for project $proj (token lacks the Job Admin permission set)"; return ;; esac
  code="$(http_code "$base/v3/accounts/$acct/webhooks/subscriptions" -H "$DBT_AUTH" -H 'Accept: application/json')"
  case "$code" in 2??) ;; *) result FAIL dbtcloud "http $code listing webhook subscriptions (token lacks the Webhooks permission set)"; return ;; esac
  # 4. GitHub integration: a repository must be linked and it must be GITHUB_REPO
  if [ -z "$repo" ]; then
    result FAIL dbtcloud "dbt Cloud GitHub integration not linked to $GITHUB_REPO (project ${name:-$proj} has no repository)"
    return
  fi
  if [ "$(lower "$repo")" != "$(lower "$GITHUB_REPO")" ]; then
    result FAIL dbtcloud "dbt Cloud GitHub integration not linked to $GITHUB_REPO (project ${name:-$proj} is linked to $repo)"
    return
  fi
  # 5. Job Admin proven by a reversible write: create an unscheduled probe job in the CI environment, then delete it
  epoch="$(date -u +%s)"
  probe_name="mdp-preflight-probe-$epoch"
  job_body="$(printf '{"account_id":%s,"project_id":%s,"environment_id":%s,"name":"%s","description":"ops/preflight.sh scope probe; created and deleted by the same run; safe to delete","dbt_version":null,"execute_steps":["dbt --version"],"triggers":{"github_webhook":false,"git_provider_webhook":false,"schedule":false},"settings":{"threads":1,"target_name":"default"},"state":1,"generate_docs":false,"run_generate_sources":false,"schedule":{"cron":"0 * * * *","date":{"type":"every_day"},"time":{"type":"every_hour","interval":1}},"job_type":"other"}' "$acct" "$proj" "$DBT_CLOUD_ENV_ID_CI" "$probe_name")"
  code="$(http_code "$base/v2/accounts/$acct/jobs/" -X POST -H "$DBT_AUTH" -H 'Accept: application/json' -H 'Content-Type: application/json' --data "$job_body")"
  case "$code" in 2??) job_id="$(json_field "$BODYF" '.data.id')" ;; esac
  if [ -z "$job_id" ]; then
    result FAIL dbtcloud "http $code creating probe job $probe_name in environment $DBT_CLOUD_ENV_ID_CI (token lacks the Job Admin permission set)"
    return
  fi
  # 6. Webhooks proven the same way: an inactive subscription on the probe job, then deleted
  hook_body="$(printf '{"name":"%s","description":"ops/preflight.sh scope probe; created and deleted by the same run","event_types":["job.run.completed"],"client_url":"https://example.invalid/mdp-preflight","job_ids":[%s],"active":false}' "$probe_name" "$job_id")"
  code="$(http_code "$base/v3/accounts/$acct/webhooks/subscriptions" -X POST -H "$DBT_AUTH" -H 'Accept: application/json' -H 'Content-Type: application/json' --data "$hook_body")"
  case "$code" in 2??) hook_id="$(json_field "$BODYF" '.data.id')" ;; esac
  if [ -n "$hook_id" ]; then
    hook_del="$(dbt_delete "$base/v3/accounts/$acct/webhooks/subscription/$hook_id" "$base/v3/accounts/$acct/webhooks/subscriptions/$hook_id")"
    case "$hook_del" in 2??) ;; *) leftover="webhook subscription $hook_id (delete http $hook_del)" ;; esac
  fi
  job_del="$(dbt_delete "$base/v2/accounts/$acct/jobs/$job_id/" "$base/v3/accounts/$acct/jobs/$job_id/")"
  case "$job_del" in 2??) ;; *) leftover="${leftover:+$leftover, }job $job_id (delete http $job_del)" ;; esac
  if [ -n "$leftover" ]; then
    result FAIL dbtcloud "probe created but not deleted, remove by hand: $leftover (account $acct on $DBT_CLOUD_HOST)"
    return
  fi
  if [ -z "$hook_id" ]; then
    result FAIL dbtcloud "http $code creating webhook subscription for probe job $job_id (token lacks the Webhooks permission set); the probe job was deleted"
    return
  fi
  result PASS dbtcloud "project ${name:-$proj}; environments prod=${env_prod:-$DBT_CLOUD_ENV_ID_PROD} ci=${env_ci:-$DBT_CLOUD_ENV_ID_CI}; Job Admin proven (job $job_id created and deleted); Webhooks proven (subscription $hook_id created and deleted); repository $repo (account $acct on $DBT_CLOUD_HOST)"
}

probe_secret_store() {
  local out rc mode
  if ! command -v secret_store >/dev/null 2>&1; then
    result MISSING secret_store "Provide and authenticate the operator-supplied secret_store adapter described in ops/fly/SECRETS.md"
    return
  fi
  mode="adapter-session"
  if [ -n "${SECRET_STORE_TOKEN:-}" ]; then mode="service-token"; fi
  # prove a value read, not a name listing: the reserved SECRET_STORE_PROJECT secret must read back as the expected project
  out="$(with_timeout 15 secret_store get SECRET_STORE_PROJECT 2>"$ERRF")"
  rc=$?
  out="$(redact "$out")"
  if [ $rc -ne 0 ]; then
    result FAIL secret_store "$mode: secret_store get SECRET_STORE_PROJECT ($SECRET_STORE_PROJECT/$SECRET_STORE_CONFIG) exit $rc: $(err_line)"
    return
  fi
  if [ "$out" != "$SECRET_STORE_PROJECT" ]; then
    result FAIL secret_store "$mode: SECRET_STORE_PROJECT read back as '$out', expected $SECRET_STORE_PROJECT ($SECRET_STORE_CONFIG)"
    return
  fi
  result PASS secret_store "$SECRET_STORE_PROJECT/$SECRET_STORE_CONFIG value read ok via $mode (load=$SECRET_STORE_LOAD)"
  if [ "$mode" = "adapter-session" ]; then
    note "Configure noninteractive authentication for your secret_store adapter before using deploy CI; see ops/fly/SECRETS.md"
  fi
}

probe_litellm() {
  local miss base code healthy unhealthy
  miss="$(missing_vars LITELLM_BASE_URL LITELLM_ADMIN_KEY)"
  if [ -n "$miss" ]; then
    result MISSING litellm "set $miss: deploy the proxy from ops/, then LITELLM_BASE_URL=https://<app>.example.invalid and LITELLM_ADMIN_KEY=<its LITELLM_MASTER_KEY>"
    return
  fi
  base="${LITELLM_BASE_URL%/}"
  code="$(http_code "$base/health" -H "Authorization: Bearer $LITELLM_ADMIN_KEY")"
  if [ "$code" = "200" ]; then
    healthy="$(json_field "$BODYF" '.healthy_count')"
    unhealthy="$(json_field "$BODYF" '.unhealthy_count')"
    result PASS litellm "$base/health 200${healthy:+ (healthy=$healthy unhealthy=${unhealthy:-0})}"
  elif [ "$code" = "000" ]; then
    result FAIL litellm "no HTTP response from $base: $(err_line)"
  else
    result FAIL litellm "http $code from $base/health (401: admin key rejected)"
  fi
}

probe_otlp() {
  local miss url code
  miss="$(missing_vars OTLP_ENDPOINT OTLP_AUTH_HEADER)"
  if [ -n "$miss" ]; then
    result MISSING otlp "set $miss: Grafana Cloud -> the stack -> OpenTelemetry -> Configure (OTLP endpoint URL and a Basic auth header from instanceId:token)"
    return
  fi
  url="${OTLP_ENDPOINT%/}/v1/traces"
  code="$(http_code "$url" -X POST -H "Authorization: $OTLP_AUTH_HEADER" -H 'Content-Type: application/x-protobuf' --data-binary '')"
  case "$code" in
    000) result FAIL otlp "no HTTP response from $url: $(err_line)" ;;
    401|403) result FAIL otlp "http $code from $url (OTLP_AUTH_HEADER rejected)" ;;
    *) result PASS otlp "$url reachable (http $code on an empty POST)" ;;
  esac
}

probe_clerk() {
  local miss code mode
  miss="$(missing_vars CLERK_SECRET_KEY CLERK_PUBLISHABLE_KEY)"
  if [ -n "$miss" ]; then
    result MISSING clerk "set $miss: https://dashboard.clerk.com -> the MDP app -> Configure -> API keys"
    return
  fi
  case "$CLERK_PUBLISHABLE_KEY" in
    pk_test_*) mode="test" ;;
    pk_live_*) mode="live" ;;
    *) mode="unknown-prefix" ;;
  esac
  code="$(http_code "https://api.clerk.com/v1/users?limit=1" -H "Authorization: Bearer $CLERK_SECRET_KEY")"
  if [ "$code" = "200" ]; then
    result PASS clerk "api.clerk.com users read ok (publishable key mode: $mode)"
  elif [ "$code" = "000" ]; then
    result FAIL clerk "no HTTP response from api.clerk.com: $(err_line)"
  else
    result FAIL clerk "http $code from api.clerk.com/v1/users (401: secret key rejected)"
  fi
}


# fixture targets are inspected once; and fixture_targets both read the outcome
FIX_OK=0
FIX_HANDLE=""
FIX_ERR=""
inspect_fixture_targets() { # python3 csv module: row width must match the header; display_name is optional
  local f="$MDP_FIXTURE_TARGETS" out rc
  if [ ! -f "$f" ]; then FIX_ERR="missing"; return; fi
  if ! command -v python3 >/dev/null 2>&1; then FIX_ERR="python3 is needed to parse the CSV (brew install python)"; return; fi
  out="$(with_timeout 15 python3 - "$f" 2>"$ERRF" <<'PY'
import csv, sys
path = sys.argv[1]
need = ["platform", "platform_account_id", "handle"]
allowed = ("fixture",)
def done(msg):
    print(msg); sys.exit(0)
try:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
except (OSError, UnicodeDecodeError, csv.Error) as e:
    done("ERR cannot parse: %s" % e)
rows = [r for r in rows if any(c.strip() for c in r) and not r[0].lstrip().startswith("#")]
if not rows:
    done("ERR no header row")
header = [c.strip() for c in rows[0]]
if len(set(header)) != len(header):
    done("ERR header repeats a column (have: %s)" % ",".join(header))
missing = [c for c in need if c not in header]
if missing:
    done("ERR header lacks: %s (have: %s)" % (" ".join(missing), ",".join(header)))
idx = {c: header.index(c) for c in header}
seen = {}
first_handle = ""
n = 0
for r in rows[1:]:
    n += 1
    if len(r) != len(header):
        done("ERR row %d: %d fields, header has %d" % (n, len(r), len(header)))
    cell = {c: r[i].strip() for c, i in idx.items()}
    if not cell["platform"]:
        done("ERR row %d: platform is empty" % n)
    if cell["platform"] not in allowed:
        done("ERR row %d: platform '%s' is not fixture" % (n, cell["platform"]))
    if not cell["platform_account_id"]:
        done("ERR row %d: platform_account_id is empty" % n)
    if cell["platform_account_id"] in seen:
        done("ERR row %d: platform_account_id '%s' repeats row %d" % (n, cell["platform_account_id"], seen[cell["platform_account_id"]]))
    seen[cell["platform_account_id"]] = n
    if not cell["handle"]:
        done("ERR row %d: handle is empty" % n)
    if n == 1:
        first_handle = cell["handle"].lstrip("@")
if n == 0:
    done("ERR %d data rows (need at least one)" % n)
print("OK %d %s" % (n, first_handle))
PY
)"
  rc=$?
  out="$(redact "$out")"
  case "$out" in
    OK\ *) read -r _ _ FIX_HANDLE <<< "$out"; FIX_OK=1 ;;
    ERR\ *) FIX_ERR="${out#ERR }" ;;
    *) FIX_ERR="csv parse failed (python3 exit $rc): $(err_line)" ;;
  esac
}




probe_fixture_targets() {
  if [ "$FIX_OK" -eq 1 ]; then
    result PASS fixture_targets "$MDP_FIXTURE_TARGETS: data rows validated by python3 csv (row width matches header; platform is fixture; platform_account_id unique; handle present; display_name optional)"
  elif [ "$FIX_ERR" = "missing" ]; then
    result MISSING fixture_targets "write $MDP_FIXTURE_TARGETS with header platform,platform_account_id,handle,display_name (display_name optional) and resolved fixture accounts (operator data; gitignored)"
  else
    result FAIL fixture_targets "$MDP_FIXTURE_TARGETS: $FIX_ERR"
  fi
}

probe_billboard() {
  local url="https://www.billboard.com/charts/hot-100/" code
  code="$(http_code "$url" -L -A 'Mozilla/5.0 (mdp-preflight)')"
  if [ "$code" = "200" ]; then
    result PASS billboard "$url http 200 (the billboard Python package reads this page)"
  elif [ "$code" = "000" ]; then
    result FAIL billboard "no HTTP response from $url: $(err_line)"
  else
    result FAIL billboard "http $code from $url"
  fi
}

probe_budgets() {
  local numre='^[0-9]+$' names="MDP_BUDGET_GLOBAL_CENTS:10000 MDP_BUDGET_STREAMLINE_CENTS:2000 MDP_BUDGET_LLM_STEP_CENTS:500 MDP_BUDGET_CEILING_CENTS:50000"
  local pair name def val detail="" from_env=0
  for pair in $names; do
    name="${pair%%:*}"; def="${pair#*:}"
    val="${!name:-}"
    if [ -z "$val" ]; then
      val="$def"
    else
      from_env=1
      if ! [[ "$val" =~ $numre ]]; then
        result FAIL budgets "$name=$val is not an integer number of cents"
        return
      fi
    fi
    detail="$detail ${name#MDP_BUDGET_}=$val"
  done
  if [ $from_env -eq 0 ]; then
    result PASS budgets "default (${detail# })"
  else
    result PASS budgets "${detail# }"
  fi
}

probe_snowflake() {
  local miss
  miss="$(missing_vars SNOWFLAKE_ACCOUNT SNOWFLAKE_USER SNOWFLAKE_PASSWORD SNOWFLAKE_WAREHOUSE SNOWFLAKE_DATABASE SNOWFLAKE_ROLE)"
  if [ -n "$miss" ]; then
    result MISSING snowflake "set $miss: open a trial at https://signup.snowflake.com/ (format check only)"
  else
    result PASS snowflake "account $SNOWFLAKE_ACCOUNT user $SNOWFLAKE_USER warehouse $SNOWFLAKE_WAREHOUSE database $SNOWFLAKE_DATABASE role $SNOWFLAKE_ROLE (presence only; not connected)"
  fi
}

probe_github() {
  local out rc
  if ! command -v gh >/dev/null 2>&1; then
    result MISSING github "brew install gh && gh auth login"
    return
  fi
  out="$(with_timeout 15 gh repo view "$GITHUB_REPO" --json name,nameWithOwner --jq '.nameWithOwner' 2>"$ERRF")"
  rc=$?
  out="$(redact "$out")"
  if [ $rc -eq 0 ] && [ -n "$out" ]; then
    result PASS github "$out readable via gh (proves repo access only; the dbt Cloud GitHub integration is checked under dbtcloud)"
  else
    result FAIL github "gh repo view $GITHUB_REPO exit $rc: $(err_line)"
  fi
}

local_probe() { # local_probe NAME "how to install" cmd args... ; PASS detail is the first output line
  local name="$1" how="$2" out rc
  shift 2
  if ! command -v "$1" >/dev/null 2>&1; then
    result MISSING "local.$name" "$how"
    return
  fi
  out="$(with_timeout 15 "$@" 2>"$ERRF")"
  rc=$?
  out="$(redact "$out")"
  if [ $rc -eq 0 ]; then
    result PASS "local.$name" "$(printf '%s' "$out" | head -1)"
  elif [ $rc -eq 124 ]; then
    result FAIL "local.$name" "$* timed out after 15s"
  elif [ $rc -eq 126 ] && [ -z "$TIMEOUT_BIN" ]; then
    result FAIL "local.$name" "$NO_TIMEOUT_REASON"
  else
    result FAIL "local.$name" "$* exit $rc: $(err_line)"
  fi
}

probe_local_docker() {
  local out rc
  if ! command -v docker >/dev/null 2>&1; then
    result MISSING local.docker "install Docker Desktop or OrbStack (https://orbstack.dev), then start it"
    return
  fi
  out="$(with_timeout 15 docker info --format '{{.ServerVersion}}' 2>"$ERRF")"
  rc=$?
  out="$(redact "$out")"
  if [ $rc -eq 0 ] && [ -n "$out" ]; then
    result PASS local.docker "daemon reachable (server $out)"
  elif [ $rc -eq 126 ] && [ -z "$TIMEOUT_BIN" ]; then
    result FAIL local.docker "$NO_TIMEOUT_REASON"
  else
    result FAIL local.docker "docker daemon not reachable (exit $rc): $(err_line)"
  fi
}

probe_local_dbt() {
  local out rc core duckdb
  if [ ! -f dbt/pyproject.toml ]; then
    result NOTE local.dbt "dbt/pyproject.toml not present yet; skipped (uv run --project dbt dbt --version)"
    return
  fi
  if ! command -v uv >/dev/null 2>&1; then
    result MISSING local.dbt "needs uv (see local.uv)"
    return
  fi
  out="$(with_timeout 15 uv run --project dbt dbt --version 2>"$ERRF")"
  rc=$?
  out="$(redact "$out")"
  if [ $rc -eq 0 ]; then
    core="$(printf '%s' "$out" | sed -nE 's/^ *- installed: *//p' | head -1 | sed -E 's/ - .*$//')"
    duckdb="$(printf '%s' "$out" | sed -nE 's/^ *- duckdb: *//p' | head -1 | sed -E 's/ - .*$//')"
    result PASS local.dbt "dbt-core ${core:-?}${duckdb:+, dbt-duckdb $duckdb} (uv run --project dbt)"
  elif [ $rc -eq 124 ]; then
    result FAIL local.dbt "uv run --project dbt dbt --version timed out after 15s; run: uv sync --project dbt"
  elif [ $rc -eq 126 ] && [ -z "$TIMEOUT_BIN" ]; then
    result FAIL local.dbt "$NO_TIMEOUT_REASON"
  else
    result FAIL local.dbt "uv run --project dbt dbt --version exit $rc: $(err_line)"
  fi
}

# ---------------------------------------------------------------- run every probe, in the report order
probe_fly_token
probe_fly_org
probe_fly_region
probe_r2
probe_dbtcloud
probe_secret_store
probe_litellm
probe_otlp
probe_clerk
inspect_fixture_targets
probe_fixture_targets
probe_billboard
probe_budgets
probe_snowflake
probe_github
# LOCAL
if [ -n "$TIMEOUT_BIN" ]; then
  result PASS local.timeout "$TIMEOUT_BIN bounds every CLI call (15s)"
else
  result FAIL local.timeout "$NO_TIMEOUT_REASON"
fi
probe_local_docker
local_probe uv   "curl -LsSf https://astral.sh/uv/install.sh | sh" uv --version
local_probe pnpm "npm i -g pnpm (or: corepack enable pnpm)" pnpm --version
local_probe node "nvm install 22 && nvm use 22 (https://github.com/nvm-sh/nvm)" node --version
local_probe psql "brew install libpq && brew link --force libpq (apt: postgresql-client)" psql --version
probe_local_dbt
local_probe tmux "brew install tmux (apt: tmux)" tmux -V
local_probe codex "npm i -g @openai/codex" codex --version

# ---------------------------------------------------------------- required set and exit code
is_required() { # is_required NAME -> 0 when the name is required for $STAGE
  case "$STAGE" in
    minimal)
      case "$1" in
        fly.token|fly.org|fly.region|r2|dbtcloud|secret_store|fixture_targets|billboard|budgets|github|local.*) return 0 ;;
      esac
      return 1 ;;
    full)
      case "$1" in
        fly.token|fly.org|fly.region|r2|dbtcloud|secret_store|fixture_targets|billboard|budgets|github|local.*|litellm|otlp|clerk|snowflake) return 0 ;;
      esac
      return 1 ;;
    *) return 0 ;;
  esac
}

REQUIRED=0; REQ_PASS=0; REQ_MISSING=0; REQ_FAIL=0
i=0
while [ $i -lt ${#NAMES[@]} ]; do
  if [ "${STATUSES[$i]}" != "NOTE" ] && is_required "${NAMES[$i]}"; then
    REQUIRED=$((REQUIRED + 1))
    case "${STATUSES[$i]}" in
      PASS) REQ_PASS=$((REQ_PASS + 1)) ;;
      MISSING) REQ_MISSING=$((REQ_MISSING + 1)) ;;
      *) REQ_FAIL=$((REQ_FAIL + 1)) ;;
    esac
  fi
  i=$((i + 1))
done
EXIT=1
if [ "$REQ_PASS" -eq "$REQUIRED" ]; then EXIT=0; fi
SUMMARY="SUMMARY stage=$STAGE required=$REQUIRED pass=$REQ_PASS missing=$REQ_MISSING fail=$REQ_FAIL lines=${#NAMES[@]} exit=$EXIT"

# ---------------------------------------------------------------- report: evidence file, then stdout
TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="ops/evidence/preflight"
EVIDENCE="$EVIDENCE_DIR/$TS.txt"
EVIDENCE_LINE=""
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

text_report() {
  local n
  printf '# ops/preflight.sh %s stage=%s repo=%s commit=%s\n' "$TS" "$STAGE" "$GITHUB_REPO" "$COMMIT"
  if [ ${#NOTES[@]} -gt 0 ]; then
    for n in "${NOTES[@]}"; do printf '%s\n' "$n"; done
  fi
  i=0
  while [ $i -lt ${#NAMES[@]} ]; do
    printf '%s %s %s\n' "${STATUSES[$i]}" "${NAMES[$i]}" "${DETAILS[$i]}"
    i=$((i + 1))
  done
  printf '%s\n' "$SUMMARY"
}

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  printf '%s' "$s"
}

json_report() {
  printf '['
  i=0
  while [ $i -lt ${#NAMES[@]} ]; do
    if [ $i -gt 0 ]; then printf ','; fi
    printf '\n  {"name":"%s","status":"%s","detail":"%s"}' "$(json_escape "${NAMES[$i]}")" "${STATUSES[$i]}" "$(json_escape "${DETAILS[$i]}")"
    i=$((i + 1))
  done
  printf '\n]\n'
}

if { mkdir -p "$EVIDENCE_DIR" && text_report > "$EVIDENCE"; } 2>"$ERRF"; then
  EVIDENCE_LINE="$EVIDENCE"
else
  EVIDENCE_LINE="NOTE evidence not written: $(err_line | sed -E 's/^[^:]*: line [0-9]+: //')"
fi
if [ "$JSON" -eq 1 ]; then
  if [ ${#NOTES[@]} -gt 0 ]; then
    for n in "${NOTES[@]}"; do printf '%s\n' "$n" >&2; done
  fi
  json_report
  printf '%s\n' "$SUMMARY" >&2
  printf '%s\n' "$EVIDENCE_LINE" >&2
else
  text_report
  printf '%s\n' "$EVIDENCE_LINE"
fi
exit "$EXIT"
