# R analyses

`mdpr` connects R to the warehouse through a libpq service name. It provides lazy queries,
rights guards, local model pins and an analysis scaffold. R does not run in production.
The platform test runner runs all R commands in Docker; it needs no R installation on the host.


## Set up

An analyst workstation needs R 4.5 or later, an IDE and a repository clone. The operator
provides an individual analyst handle, a password and the service block privately.
A private warehouse connection also needs authorized network access and a running proxy.

| Minute | Where | Command |
|---|---|---|
| 0–1 | Terminal | Open the repository clone. |
| 1–2 | Second terminal, left open | `fly proxy 15472:5432 -a mdp-postgres --bind-addr 127.0.0.1` |
| 2–4 | R console at the clone root | `install.packages("pak")`, then `pak::local_install("r/mdpr")` |
| 4–5 | R console | `mdpr::mdp_setup("<handle>")`; enter the password in the masked prompt. |
| 5–6 | R console | `mdpr::mdp_sitrep()` prints checks and fixes. |
| 6–7 | R console | `con <- mdpr::mdp_connect()`; `mdpr::mdp_tbl(con, "mart_shazam_chart_daily") \|> dplyr::count(chart) \|> dplyr::collect()` |
| 7–9 | R console | `mdpr::mdp_new_analysis("chart-momentum")`; library initialization time depends on the workstation and cache. |
| 9–10 | Analysis console | `testthat::test_dir("tests/testthat")`; `usethis::pr_init("<handle>-chart-momentum")`. Commit source, then use `usethis::pr_push()` to propose it. |

The service file is `PGSERVICEFILE`, or `~/.pg_service.conf`; on Windows its default is
`%APPDATA%/postgresql/.pg_service.conf`. The password file is `PGPASSFILE`, or `~/.pgpass`;
on Windows it is `%APPDATA%/postgresql/pgpass.conf`. Setup replaces the named block and
password entry and sets private file permissions on Unix. It never accepts a password argument.
Do not put a password in `.Renviron`, code, reports or git. Windows paths have no CI coverage.


## IDE connections

Each IDE uses `con <- mdpr::mdp_connect()`. `MDP_DB` selects a service or snapshot.
Close a connection with `DBI::dbDisconnect(con)` or the pane's disconnect action.

| Surface | Connections pane | Project and notes |
|---|---|---|
| RStudio Desktop | Observer registration; “Music Data Platform warehouse” New Connection snippet | `mdp_new_analysis()` opens the project. Dock launches do not need shell startup files for credentials. |
| Posit Workbench | Same observer contract | Service files live in the server home; its host needs its own network route. |
| Positron | Observer contract integration | Scaffold and open the folder. Pane rendering requires a manual IDE check. |
| VS Code with vscode-R | Use `mdp_catalog()` | Open the analysis folder; install `languageserver` inside the project. |
| Jupyter with IRkernel or ark | Use `mdp_catalog()` | Keep notebooks at the analysis root; clear outputs and execution counts before commit. |
| Rscript, targets and CI | No pane required | Missing password entries produce setup instructions without prompting. |

The automated pane checks exercise the observer calls and previews. Visual checks in RStudio
and Positron are separate manual checks. The [Connections contract](https://rstudio.github.io/rstudio-extensions/connections-contract.html)
defines the callbacks used by the package.


## Explore

```r
con <- mdpr::mdp_connect()
q <- mdpr::mdp_tbl(con, "mart_chart_history") |>
  dplyr::filter(chart_position <= 10)
mdpr::mdp_catalog(con)
mdpr::mdp_check_rights(q)
mdpr::mdp_sql(q)
mdpr::mdp_sql(q, "sql/chart_momentum.sql")
rows <- dplyr::collect(q)
```

`mdp_catalog()` reads `catalog.relations` when available, otherwise the bundled contract
inventory generated from the same YAML as the analyst guide. It includes excluded tenant
contracts for discovery; listing a contract does not grant access.
`mdp_sql()` renders PostgreSQL SQL, including from a DuckDB lazy query. Review any backend-specific
expressions before promotion; SQL remains subject to normal dbt validation.

| Limit | How to work |
|---|---|
| 30 s statement timeout | Bound live reads; use a snapshot for heavy exploration. |
| No TEMP privilege | `copy_to()` and `compute()` fail. Use `dbplyr::copy_inline()` for a small frame or write to your sandbox. |
| `source_keys` is JSON text | Keep it intact with both rights flags; decode explicitly when needed. |
| Big integers arrive with `bigint = "numeric"` | Double precision avoids an integer64 dependency; values beyond exact double precision need an explicit SQL text cast. |
| UTC sessions | Compare timestamps in UTC. |


## Work offline

Start the [local fixture stack](DEVELOPING.md#quickstart), then configure its service:

```r
mdpr::mdp_setup(target = "local")
Sys.setenv(MDP_DB = "mdp_local")
con <- mdpr::mdp_connect()
```

A non-default stack port needs `mdp_setup(target = "local", port = 58532)`.
Stop the stack with `bash ops/local/down.sh` after use.

Snapshots require the platform snapshot writer in the checkout and `uv` on PATH:

```r
mdpr::mdp_snapshot("data/warehouse.duckdb", db = "mdp_local")
Sys.setenv(MDP_DB = "data/warehouse.duckdb")
con <- mdpr::mdp_connect()
```

Snapshot connections are read-only. The wrapper launches the platform writer; it does not copy
tables in R. A checkout without the writer reports its absence. Snapshots remain outside git.


## Save work

[chart-momentum](../analyses/example/chart-momentum/) passes the guard and the R checks; scaffold your own and compare.

`mdp_new_analysis("chart-momentum")` creates `analyses/<handle>/chart-momentum/`, refuses an
existing folder, installs the base packages and writes `renv.lock`. Its default handle comes
from the service file. Use `handle` and `repo` explicitly when working outside a configured clone.

Run `source("setup.R")` after clone or pull: it restores the lockfile and installs `mdpr` from
this checkout. `mdpr`, `IRkernel` and `languageserver` are renv-ignored so workstation tools
and a local package path do not enter the lockfile. Commit R and SQL source, Quarto source,
tests, `renv.lock` and `models.yml`. Data, pins, snapshots, rendered reports and credentials
stay outside git. The guard also checks tracked files that later become ignored.

`R/features.R` keeps the chart/week/position grain and the three rights columns.
`tests/testthat/test-data.R` checks uniqueness and eligibility; without a working `MDP_DB`
it skips with a named reason. The optional `_targets.R` runs features → learnable training
rows → fit → pin → report. Delete it when unused. Rendering `explore.qmd` needs Quarto in the
analyst environment; the Docker checks parse its R chunks without installing Quarto on the host.
The example fit illustrates the pipeline and is not a validated predictive model.

In Jupyter, run `renv::install("IRkernel")` and
`IRkernel::installspec(name = "mdp-chart-momentum")` inside the project. In VS Code, install
`languageserver` there. Both use the same service files as scripts.


## Models

```r
train <- mdpr::mdp_learnable(rows)
fit <- stats::glm(chart_position ~ 1, data = train)
con <- mdpr::mdp_connect("mdp")
version <- mdpr::mdp_pin_model(fit, "chart-baseline", train, con = con,
                              inputs = "marts.mart_chart_history")
```

False and unknown eligibility drop from `mdp_learnable()`. `mdp_pin_model()` refuses a training
frame containing either or missing rights columns. It then checks every distinct `source_keys`
entry against `catalog.learning_rights`, a live view of the same registry used by `mdp_annotate()`.
An ineligible or unknown key refuses the pin; the error reports a count without source values.
A successful check writes a versioned local pin under `_pins/` and upserts `models.yml`.

Pinning needs an open PostgreSQL warehouse connection passed as `con = con`.
Offline work and DuckDB snapshots cannot authorize a pin: reconnect with `mdp_connect("mdp")`.
Keep source keys intact through joins; the check cannot recover removed or replaced lineage.
`mdp_check_rights()` only checks column presence. Use `mdp_pin_model()` for the warehouse check.
Metadata records input names, training row count, the eligibility filter, git SHA, lockfile hash,
R version and available snapshot provenance. Keep rights columns until the training guard runs.
Commit `models.yml`, not fitted model binaries. A shared pins bucket is an owner decision;
the default board stays local to the analysis. Code, lockfile, snapshot provenance and pin
version together identify the inputs and environment of a result.


## Propose a change

| Output | Landing place | Gate | Served? |
|---|---|---|---|
| Exploration, plots and report source | Analysis folder | Review, R checks and analysis guard | No |
| Reusable query | Review SQL, then a dbt model | Enforced contract, relation lint, rights annotation and dbt CI | Through dbt |
| SQL-expressible scorer | Scoring SQL over a feature query | Same checks; learnable training only | Through dbt |
| Small mapping or coefficient table | Reviewed CSV and YAML under `dbt/seeds/` | Seed review; model ends in `mdp_annotate` | As a model input |
| Row results SQL cannot express | Own sandbox with rights columns | Analysis review | No; serving requires a Python gold function proposal |
| Fitted model | Local versioned pin; `models.yml` in the proposal | Provenance review | No |

With the platform toolchain:

1. Create a branch with `usethis::pr_init("<handle>-chart-momentum")`.
2. Build a query that retains grain and rights, then run `mdp_check_rights(q)`.
3. Write the review copy with `mdp_sql(q, "sql/chart_momentum.sql")`.
4. Use `mdp_save_view(q, "chart_momentum", con)` for the sandbox handoff.
5. At the repository root, run `pnpm --dir control mdp new mart mart_chart_momentum --from sandbox_<handle>.chart_momentum`.
6. Complete column descriptions, `meta.grain` and one cadence tag in the enforced contract.
   Follow [ship a mart](operating.md#data-scientist-ship-a-mart) for local dbt builds and generated artifacts.
7. Run `bash ops/ready.sh`, commit source and model changes, and open a proposal using the
   [R proposal template](../.github/PULL_REQUEST_TEMPLATE/r-proposal.md).

With R only, perform steps 1–4 and open the proposal carrying `sql/<name>.sql` and the view
name. Add the `promote` label so a platform engineer runs the lift and model gates on that
branch. When the sandbox lift is unavailable, retain the SQL file and have the engineer use
`mdp new mart <name>` and put the reviewed query into its contract-first scaffold.
There is one platform lift; R does not render dbt models.

For a SQL-expressible model, `orbital` or `tidypredict` can produce scoring SQL for a
`mart_<name>_scores` model over the feature query. This route is **not exercised in CI**;
validate its scoring behavior and rights through the normal model review.
For results SQL cannot express, the R code and model pin specify a separate Python gold
function proposal. R has no production runtime or serving writer.


## Rules

Preserve `learning_eligible`, `resale_permitted` and `source_keys` through joins and exports;
combine rights conservatively over every contributing input. Access does not grant resale rights.
Train only on learnable rows. Do not reverse pseudonyms, join them to outside identity data,
or put personal values in reports or pins. Tenant schemas and tenant-specific joins stay out
of this workflow. Do not request a service role to bypass an analyst denial.

Other analysts can read your sandbox.
`mdp_write()` and `mdp_save_view()` require rights columns and write only into the login's
own sandbox. A sandbox is not a dbt dependency or a serving mart.


## ODBC alternative

Install the driver and R adapter in the analyst environment:

```sh
brew install unixodbc psqlodbc
```

```r
install.packages("odbc")
```

Configure an `[MDP]` DSN in `~/.odbc.ini` with the installed driver's path and the same
host, port, database, user and SSL mode as the service block; leave passwords in the libpq
password file. Connect with `DBI::dbConnect(odbc::odbc(), dsn = "MDP", bigint = "numeric")`.


## Troubleshooting

Run `mdpr::mdp_sitrep()` from the clone. Each failed line names a fix: package version,
service block, password entry and Unix mode, TCP proxy, login, role settings, sandbox,
catalog, `flyctl`, `git`, `uv` and `pnpm`. Set `MDP_FLYCTL=<path>` in `~/.Renviron` if an IDE
cannot see the executable; that setting is not a credential. If the sandbox or catalog is missing, ask the operator to deploy it. Missing `uv` affects snapshots and missing
`pnpm` affects the lift; reads need neither.

For platform checks, run `bash ops/r/check.sh --quick`. `--smoke` also checks the local
fixture stack, and reports sandbox, lift and snapshot checks as skipped when its modules are absent.
The Docker image uses R 4.6.1 and the dated P3M snapshot, 2026-09-24.
Manual IDE rendering, workstation renv
initialization timing and nested-project Git UI behavior remain outside these checks.
