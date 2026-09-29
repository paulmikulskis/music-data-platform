#!/usr/bin/env bash
set -euo pipefail
mode=${1:---quick}
[[ $# -le 1 && ($mode == --quick || $mode == --smoke) ]] || { echo 'usage: check.sh [--quick|--smoke]' >&2; exit 2; }
root=$(cd "$(dirname "$0")/../.." && pwd -P)
cd "$root"
mkdir -p /tmp/mdp-w-r "${XDG_CACHE_HOME:-$HOME/.cache}/mdp-r/renv"
image=mdp-w-r:check
step='Docker image'
trap 'echo "FAIL $step"' ERR
docker build -t "$image" -f ops/r/Dockerfile . > /tmp/mdp-w-r/image.log 2>&1 || { cat /tmp/mdp-w-r/image.log; false; }
run=(docker run --rm --tmpfs /tmp:rw,exec,size=2g --user "$(id -u):$(id -g)" -e HOME=/tmp/rhome
  -e RENV_PATHS_CACHE=/renv-cache -e RENV_CONFIG_AUTOLOADER_ENABLED=false
  -v "$root:/work" -v /tmp/mdp-w-r:/tmp/mdp-w-r
  -v "${XDG_CACHE_HOME:-$HOME/.cache}/mdp-r/renv:/renv-cache" -w /work)
step='1 roxygen drift'
"${run[@]}" "$image" Rscript -e 'dir.create(Sys.getenv("HOME"), showWarnings=FALSE); roxygen2::roxygenise("r/mdpr")'
git diff --exit-code -- r/mdpr/NAMESPACE r/mdpr/man
# New generated files must also be tracked, otherwise git diff alone misses them.
[[ -z $(git ls-files --others --exclude-standard r/mdpr/NAMESPACE r/mdpr/man) ]]
echo "PASS $step"
# The source is public to read, with no redistribution license. Accept only R's exact
# non-standard-license warning; all errors and every other warning still fail.
step='2 R CMD check'
"${run[@]}" "$image" Rscript -e 'dir.create(Sys.getenv("HOME"), showWarnings=FALSE); result <- rcmdcheck::rcmdcheck("r/mdpr", args="--no-manual", build_args="--no-build-vignettes", check_dir="/tmp/rhome/check", error_on="never"); expected <- "checking DESCRIPTION meta-information ... WARNING Non-standard license specification: All rights reserved Standardizable: FALSE"; unexpected <- result$warnings[vapply(result$warnings, function(x) gsub("[[:space:]]+", " ", trimws(x)) != expected, logical(1))]; if (length(unexpected)) print(unexpected); stopifnot(length(result$errors) == 0L, length(unexpected) == 0L); cat(sprintf("R CMD check: %d errors, %d warnings, %d notes\n", length(result$errors), length(result$warnings), length(result$notes)))'
echo "PASS $step"
step='3 analysis template'
"${run[@]}" "$image" bash -ec 'mkdir -p "$HOME/library"; export R_LIBS_USER="$HOME/library"; R CMD INSTALL --library="$HOME/library" r/mdpr >/dev/null; Rscript ops/r/check-template.R'
echo "PASS $step"
step='4 changed analyses'
python3 -m unittest discover -s ops/r -p test_changed_analyses.py
python3 ops/r/changed_analyses.py > /tmp/mdp-w-r/analyses.txt
"${run[@]}" "$image" bash -ec 'mkdir -p "$HOME/library"; export R_LIBS_USER="$HOME/library"; R CMD INSTALL --library="$HOME/library" r/mdpr >/dev/null; while IFS= read -r path || [[ -n "$path" ]]; do Rscript ops/r/check-analysis.R "$path"; done < /tmp/mdp-w-r/analyses.txt'
echo "PASS $step"
if [[ $mode == --smoke ]]; then
  step='5 live smoke'
  MDP_LOCAL_ROOT=$root
  source ops/local/common.sh
  mdp_local_owned
  # Pinning needs one explicitly eligible synthetic key. Shipped provider rights remain false.
  cleanup_registry() {
    docker exec --user postgres "$MDP_LOCAL_CONTAINER" psql -U postgres -d warehouse -v ON_ERROR_STOP=1 -c "DELETE FROM reference.rights_registry WHERE source_key IN ('r_smoke_eligible','r_smoke_ineligible')" >/dev/null
  }
  trap cleanup_registry EXIT
  cleanup_registry
  docker exec -i --user postgres "$MDP_LOCAL_CONTAINER" psql -U postgres -d warehouse -v ON_ERROR_STOP=1 <<'SQL'
INSERT INTO reference.rights_registry
(source_key,provider,category,license_ref,learning_eligible,resale_permitted,refresh_cadence,notes,review_ref,review_required)
VALUES ('r_smoke_eligible','Synthetic fixture','fixture','fixture',true,false,'daily','R smoke only','fixture',false),
       ('r_smoke_ineligible','Synthetic fixture','fixture','fixture',false,false,'daily','R smoke only','fixture',false);
SQL
  # The container sees PostgreSQL on its own loopback; no host routing assumption.
  "${run[@]}" --network "container:$MDP_LOCAL_CONTAINER" "$image" bash -ec 'mkdir -p "$HOME/library"; export R_LIBS_USER="$HOME/library"; R CMD INSTALL --library="$HOME/library" r/mdpr >/dev/null; Rscript ops/r/smoke.R'
  if [[ -f functions/src/mdp_functions/warehouse_lift.py && -f /tmp/mdp-w-r/sandbox.ready ]]; then
    source ops/local/env.sh
    pnpm --dir control mdp new mart mart_r_smoke --from sandbox_local.r_smoke_view
    test -f dbt/models/marts/global/mart_r_smoke.sql
    test -f dbt/models/marts/global/mart_r_smoke.yml
    uv run --project functions python -c 'import yaml; from pathlib import Path; data=yaml.safe_load(Path("dbt/models/marts/global/mart_r_smoke.yml").read_text()); assert data["models"][0]["config"]["contract"]["enforced"]'
    rm dbt/models/marts/global/mart_r_smoke.sql dbt/models/marts/global/mart_r_smoke.yml
    rm -f control/packages/data-sdk/src/generated/mart_r_smoke.zod.ts
    uv run --project dbt dbt parse --project-dir dbt --profiles-dir dbt/profiles --target ci
    uv run --project functions python ops/label-catalog.py
    # Undo the lift's contract-only generation; this smoke never builds a full catalog.
    pnpm --dir control --filter @mdp/data-sdk generate --contract-only
    echo 'PASS lift'
  else
    echo 'SKIPPED: R checks not in main (sandbox/lift)'
  fi
  if [[ -f functions/src/mdp_functions/warehouse_snapshot.py ]]; then
    source ops/local/env.sh
    "${run[@]}" -e MDP_LOCAL_PG_PORT="$MDP_LOCAL_PG_PORT" "$image" Rscript -e 'source("r/mdpr/R/setup.R"); Sys.setenv(PGSERVICEFILE="/tmp/mdp-w-r/service", PGPASSFILE="/tmp/mdp-w-r/password"); mdp_setup(target="local", port=as.integer(Sys.getenv("MDP_LOCAL_PG_PORT", "56432")))'
    PGSERVICEFILE=/tmp/mdp-w-r/service PGPASSFILE=/tmp/mdp-w-r/password MDP_ANALYST_URL=service=mdp_local uv run --project functions python -m mdp_functions.warehouse_snapshot --schemas marts,intermediate,staging --out /tmp/mdp-w-r/snap.duckdb
    "${run[@]}" --network "container:$MDP_LOCAL_CONTAINER" -e MDP_R_SNAPSHOT=/tmp/mdp-w-r/snap.duckdb "$image" bash -ec 'mkdir -p "$HOME/library"; export R_LIBS_USER="$HOME/library"; R CMD INSTALL --library="$HOME/library" r/mdpr >/dev/null; Rscript ops/r/smoke.R'
    echo 'PASS snapshot'
  else
    echo 'SKIPPED: R checks not in main (snapshot)'
  fi
  "${run[@]}" --network "container:$MDP_LOCAL_CONTAINER" -e MDP_R_CLEANUP=1 "$image" bash -ec 'mkdir -p "$HOME/library"; export R_LIBS_USER="$HOME/library"; R CMD INSTALL --library="$HOME/library" r/mdpr >/dev/null; Rscript ops/r/smoke.R'
  rm -f /tmp/mdp-w-r/service /tmp/mdp-w-r/password /tmp/mdp-w-r/sandbox.ready /tmp/mdp-w-r/snap.duckdb
  echo "PASS $step"
fi
