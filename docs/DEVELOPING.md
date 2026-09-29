# Development

![Music Data Platform](images/banner.svg)

Python functions land raw records, dbt builds contracted marts, and TypeScript APIs manage
operations and typed reads. Postgres is the deployed warehouse; DuckDB supports local modeling.

Source authors: read [add a source](operating.md#data-engineer-add-a-source) and [Contributing](../CONTRIBUTING.md) before setup.
Mart authors: read [ship a mart](operating.md#data-scientist-ship-a-mart) and [Contributing](../CONTRIBUTING.md) before setup.
Movement authors: read [movement](movement.md) and [measure the movement rules](operating.md#data-scientist-measure-the-movement-rules).
Showcase authors: read the [showcase guide](../control/apps/showcase/README.md).

| Layer | Entry point | Location |
|---|---|---|
| Bronze | External fetch, raw landing, typed/deduplicated staging | functions sources; dbt/models/{bronze,sources,staging} |
| Silver | Declared warehouse inputs; SQL joins, rights annotations and statistics | dbt/models/intermediate and marts/global |
| Gold | Budgeted enrichment and joins to its output | functions sources |

Rights annotate serving rows; derivative consumers apply learning_gate. Scope defaults global.
See [operating steps](operating.md) and each component's CLAUDE.md for authoring rules.

For SQL exploration and notebooks, start with [analyst access](analyst-access.md).

## Layout

```text
dbt/                  project, selectors, profiles, locked Python environment
  models/             bronze, sources, staging, intermediate, marts/global
  macros/             invocation, cycles, schema routing, rights, priority, input identity
  seeds/              rights registry, source priority, track candidates, imported reference
  tests/ lifecycle/
functions/            Python runtime, sources/fixtures, workbench, tests, OpenAPI
  udf/                postgres and snowflake invocation adapters
r/                    mdpr R package, tests and analysis templates
analyses/             analyst projects under <handle>/<topic>/
control/              pnpm workspace
  apps/               control-api, data-api, showcase (the viewer app)
  packages/           control-db, contracts, data-sdk, mdp-cli, showcase-auth
ops/                  runner, preflight, deployment, heartbeat
  fly/                postgres, haproxy, functions, control-api, data-api, showcase, core-runner, mb-db, mb-import, alloy
  backtest/           song backtest harness: capture, replay, label, score, report
  showcase/           query inventory, adapter checks, lineage, link previews, Stack facts, source registry and map
  local/ ci/ runbooks/ readme/
inputs/               input template; ignored local secrets and target CSV
docs/                 public context, source catalog, operating steps, movement rules, README images
```

## Quickstart

Install Docker, Python 3.12 via uv, Node 22, pnpm 10.28.0, tmux, psql and curl. Start Docker.
From a fresh clone, run:

```sh
bash ops/local/up.sh
```

- Functions API: http://127.0.0.1:18080/v1/health; workbench service: http://127.0.0.1:18085/health.

- Operator console: http://127.0.0.1:18090/ops; browser workbench: http://127.0.0.1:18090/workbench.

- Explorer: http://127.0.0.1:18090/explorer; data API: http://127.0.0.1:18091, with synthetic demo rows.

[env.sh](../ops/local/env.sh) supplies every local service and dbt connection from the running
Postgres container. Source it in another Bash or Zsh terminal before CLI work. The launcher installs
locked dependencies, initializes control, loads a synthetic week and builds the analyst starter marts.
`dry_run: true` keeps dbt invocations inert; the fixture runner loads the raw rows separately.
Connect as `analyst_local` using the printed settings or `psql -X "$MDP_ANALYST_URL"`.
The [analyst guide](analyst-access.md#local) has ten queries that return rows immediately.

All listeners bind loopback. The fixed passwords and API keys are disposable fixtures; the launcher
starts with a clean environment so provider credentials and external transports are absent.
Override `MDP_LOCAL_PG_PORT`, `MDP_LOCAL_FUNCTIONS_PORT`, `MDP_LOCAL_WORKBENCH_PORT`,
`MDP_LOCAL_CONTROL_PORT` or `MDP_LOCAL_DATA_PORT` on the first `up.sh` invocation. Defaults are
56432, 18080, 18085, 18090 and 18091; the script prints the actual URLs and retains those ports
until shutdown. Each checkout owns a container and a private tmux directory.

Run `bash ops/local/down.sh` to stop the four services and remove the database container and its
volume. Repeating either command is safe; another `up.sh` preserves the running stack and fixtures.
Logs remain at the printed path. After stopping, a new `up.sh` creates a fresh fixture database.
[Advanced database stacks](../ops/local/README.md) covers image and object-storage gates.

### R and psql files

Use the actual port printed by startup. For example, when `MDP_LOCAL_PG_PORT=56540`:

```r
mdpr::mdp_setup(target = "local", port = 56540)
con <- mdpr::mdp_connect("mdp_local")
mdpr::mdp_tbl(con, "mart_song_day")
DBI::dbDisconnect(con)
```

`mdp_setup()` writes service `mdp_local` to `~/.pg_service.conf` and its password to
`~/.pgpass`, both with mode `0600`. Then run `psql service=mdp_local`.
For separate files, set `PGSERVICEFILE` and `PGPASSFILE` before setup in R:

```r
Sys.setenv(PGSERVICEFILE = "/tmp/mdp-local.service", PGPASSFILE = "/tmp/mdp-local.pgpass")
mdpr::mdp_setup(target = "local", port = 56540)
```

Use those same paths in the shell:

```sh
PGSERVICEFILE=/tmp/mdp-local.service PGPASSFILE=/tmp/mdp-local.pgpass psql service=mdp_local
```

A container uses its own files and network. In the database container's network namespace,
use `port = 5432`. See [R setup](r.md#set-up) to install `mdpr`.

### Local marts

<!-- local-marts:start -->
<!-- Generated by: uv run --project functions python ops/analyst-doc.py -->
The local week builds these marts and checks analyst access before startup finishes:

- `marts.mart_arrivals_current`
- `marts.mart_chart_history`
- `marts.mart_early_signals_current`
- `marts.mart_editorial_entries`
- `marts.mart_editorial_presence`
- `marts.mart_playlist_coverage`
- `marts.mart_playlist_events`
- `marts.mart_playlist_membership_current`
- `marts.mart_playlist_profile`
- `marts.mart_readiness`
- `marts.mart_shazam_chart_daily`
- `marts.mart_song_aliases`
- `marts.mart_song_cluster_members`
- `marts.mart_song_day`
- `marts.mart_top_movers`
- `marts.mart_top_movers_current`
- `marts.mart_track_daily_streams`

The list comes from [models.txt](../ops/local/models.txt). Run `bash ops/local/up.sh` to build it.
<!-- local-marts:end -->

### Before a PR

Run `bash ops/ready.sh` from the repository root, or `uv run --project functions mdp ready`.
It creates a missing local dbt profile from the example and preserves an existing one.
It installs locked control dependencies before SDK generation or conformance checks.
It checks paths changed since the merge base with `main`, including uncommitted and new files.
Generated health policy, source readers and lineage are checked first; refresh a stale output with the reported command, then run `bash ops/ready.sh` again.
Docs get local file and heading checks. External links are not fetched.
The glossary order check runs offline with `python3 ops/ci/check_glossary.py`.
For a SQL edit, `bash ops/ready.sh` checks generated files, parses and lints the project, then builds changed models, downstream models and their inputs in a fresh DuckDB file.
Macro, contract, seed and deletion changes build the full project.
These local builds use empty raw tables and reference seeds; CI also tests PostgreSQL behavior.
The review gate checks that every served mart carries all upstream writers.
After a dbt macro, model, seed, function declaration or peek review changes, regenerate
`control/apps/showcase/lib/lineage.generated.json` before the PR. From the repository root, run:

```sh
cp -n dbt/profiles/profiles.example.yml dbt/profiles/profiles.yml
MDP_PG_PASSWORD=dbt_transform uv run --project dbt dbt parse --project-dir dbt --profiles-dir dbt/profiles --target pg_local
uv run --project functions python ops/showcase/lineage/generate.py
uv run --project functions python ops/showcase/lineage/generate.py --check
```

Parsing needs no running database. Review and commit the generated diff.
A SQL change under a reviewed peek needs a field review before its review hash changes.
Follow [showcase artifacts](../ops/showcase/README.md) for that review and the permission gate.

The SDK check uses YAML contracts. CI also checks types against a built catalog.
Python changes get lint on touched files and tests selected through imports and source keys.
A source key inside one test selects that test. Shared imports and fixtures select their whole file.
Shared Python configuration or an unmapped module uses the full functions suite.
Only selected tests marked `docker` start a fresh Postgres container on a free loopback port.
Control changes get typecheck and tests for the touched packages.
Contract changes also run the same `ops/ci/jobs/conformance-contracts.sh` script as CI on a disposable copy.
Visible copy (console, showcase, CLI, docs headings) gets `uv run --project functions python ops/ci/lint_copy.py`
in CI; run it by hand after changing a headline. The rules are in `ops/ci/lint_copy.py`.
The summary gives each check's time and explains skips. `--list` shows the selection without running it.
Paths without a local check are listed as CI-only. Every main push and pull request runs
all five CI workflows without path filters, including docs-only changes.
Main takes only branches whose current head passes `ops/merge-gate.sh <branch>`.
Commit the changes and merge main, then run that command to push and open a draft PR.
It requires all five workflows and `showcase-artifacts-postgres` on the pinned SHA.
The deploy gate has the same requirements. Missing and skipped requirements do not pass.
The merge gate requires a clean checkout before pushing and after the checks finish.
It prints the PR link for review and never merges.
Run `python3 ops/ready.py --ci` to print the command for the current branch.
`bash ops/ci/lint-dbt.sh` prints one PASS line; add `-v` to see every rule and selector.

Use the commands below to run individual checks or reproduce CI.

### Functions tests

```sh
cp -n dbt/profiles/profiles.example.yml dbt/profiles/profiles.yml
docker run -d --name mdp-test-pg --shm-size=1g -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:5433:5432 postgres:17
bash ops/local/init.sh                      # databases, roles and control migrations on MDP_PG_PORT (5433)
bash functions/tests/checks.sh          # ruff and the whole suite; extra arguments go to pytest
uv run --project functions pytest functions/tests -q -m "not docker"   # no Postgres needed
```

## Control checks and generated types

Use the local environment from [env.sh](../ops/local/env.sh). The launcher runs migrations and
seeds runbooks; control checks run independently:

```sh
pnpm --dir control typecheck
pnpm --dir control test
uv run --project functions python ops/ci/lint_error_catalog.py
```

Error guidance lives in `docs/errors/catalog.yml`. After editing a row, run
`pnpm --dir control --filter @mdp/contracts error-catalog` to update the console, Python and R copies.
The catalog lint checks named errors, recovery instructions, runbook links and generated files.

Health defaults live in `functions/src/mdp_functions/health_policy.py`.
After changing a cadence overdue interval, scheduled-cycle predicate, coverage floor or probe deadline,
regenerate the shared contracts module, console export and dbt copy.
Reader display windows and grace margins live in `ops/ci/generate_health_policy.py`.
The generator writes them to the contracts module and `functions/src/mdp_functions/health_policy_generated.py`.
These margins apply to the showcase; cadence alerts and `/health/status` keep their existing rules.
Run:

```sh
uv run --project functions python ops/ci/generate_health_policy.py
uv run --project functions python ops/ci/generate_health_policy.py --check
```

The generated-drift workflow checks all four outputs beside the sandbox policy.

After building all contracted marts, regenerate types:

```sh
uv run --project dbt dbt docs generate --project-dir dbt --profiles-dir dbt/profiles --target dev
pnpm --dir control --filter @mdp/data-sdk generate
```

Generation rejects an incomplete supplied catalog. For contract-only authoring use
`pnpm --dir control --filter @mdp/data-sdk generate --contract-only` explicitly.

## Status

Read the generated platform status with `pnpm --dir control mdp status` or the Platform status
card at `/ops` (also available at `/status`). Both report scheduled cadence closes, open scheduled cycles,
alerts and running API builds. Follow [Reach control-api](operating.md#reach-control-api) for the
private proxy and admin-key setup; the [local environment](../ops/local/env.sh) supplies local URLs.

Use [preflight](../ops/preflight.sh) to check provisioning and the [secret map](../ops/fly/SECRETS.md)
for configuration. Local listeners and shutdown commands are in [Quickstart](#quickstart).

## Source catalog

[Sources](sources.md) is generated: facts come from the function declarations, `streamline_defaults`
and the rights registry, and judgment (role, method, speed and breadth, market edge, API) comes from
`docs/sources/annotations.csv`, one row per source key. A `<key>_weekly` twin shares its daily key's
row. A planned source has only an annotation row, with its planned facts filled in; when its function
registers, clear its status and planned-only columns, since code then owns them.

```sh
uv run --project functions mdp sources export   # first, when a function is added or removed
uv run --project functions mdp sources doc      # regenerate docs/sources.md
uv run --project functions mdp sources doc --check
```

`--check` runs in the generated-drift workflow and in `functions/tests/test_sources_doc.py`. It fails
when a registered function, retired key or rights row has no annotation, when an annotation names a
key that is none of those and is not planned, when an API cell omits a declared host, or when the doc
is stale.

## README visuals

The README and developer guide use generated art. Refresh it after a visual change:

```sh
uv run ops/readme/cards.py   # hero, banner and outcome cards, drawn from the design-system fonts
```

Card copy and each card's live, landing or next status sit in `CARDS` in `ops/readme/cards.py`.
The generator needs `rsvg-convert` (librsvg) for the app’s PNG marks.
Fonts are bundled under `control/apps/showcase/public/fonts`; pass `--fonts` to use another directory.

### Relation labels

After changing source declarations, rights or dbt models, run `dbt parse`, then
`uv run --project functions python ops/label-catalog.py`.
`--check` compares the generated catalog with the manifest in CI.
The bootstrap installs its SQL definitions and labels existing relations.
A Postgres DDL hook labels new relations and columns, including tenant schemas.

### Optional secret-manager adapter

`secret_store` is an operator-supplied executable on PATH, not a bundled or installable CLI.
Deployment commands require an implementation of the [adapter interface](../ops/fly/SECRETS.md#secret-store-adapter-contract).
The local quickstart uses fixture configuration and does not need it.
