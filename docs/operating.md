# Ops

Use the matching local or deployed environment from [setup and status](DEVELOPING.md).
Commands run from the repository root; operator pages use the authenticated control API.
For every secret store command, pass `--scope .` explicitly.
Authenticate to the project's workspace first, then run the command for your task below.

## Reach control-api

With flyctl installed and signed in to an authorized Fly identity, keep this terminal open
(choose another free local port if 18090 is occupied), as in the
[analyst proxy recipe](analyst-access.md#fly-account-and-connection):

```sh
fly proxy 18090:8090 -a mdp-control-api --bind-addr 127.0.0.1
```

In another terminal, run `export MDP_CONTROL_API_URL=http://127.0.0.1:18090` and load your admin
key into `MDP_API_KEY` from its secret manager, then run `pnpm --dir control mdp status`.
The owner mints that key once with [admin-add.sh](../ops/fly/postgres/admin-add.sh), using a
privileged `MDP_CONTROL_DATABASE_URL`; [admin-key setup](../ops/fly/SECRETS.md#admin-keys)
covers issuance, direct secret store storage and revocation. Reader keys cannot administer
control. CLI keys work without Clerk; browser sign-in uses Clerk with `mdp_admin=true` metadata.

Staff identities open every console GET page for reading, except the sandbox operator view.
They can use Workbench Run, Preview, Backtest and Save as PR, their linked Sandbox,
Queries, Explorer, Reference, status and function pages.
Staff previews read global marts, intermediate and staging with the analyst privacy grants,
plus generated labels and catalog. Raw and tenant previews need admin.
Mutating operator forms stay disabled; ask an admin to perform those actions.
Read the [access runbook](../ops/runbooks/forbidden.md) for role refusals.

An admin issues a console key with:

```sh
pnpm --dir control mdp keys create --role staff --label analyst-console

# Link an existing SQL login when Sandbox status is needed:
pnpm --dir control mdp keys create --role staff --label analyst-console --warehouse-role analyst_<handle>
pnpm --dir control mdp keys list
pnpm --dir control mdp keys revoke <id>
```

Send the returned key privately and use it as the browser's `x-api-key` header value.
An existing Clerk session can use public metadata `mdp_staff: true` and optional
`mdp_warehouse_role: "analyst_<handle>"`. `mdp_admin: true` takes precedence.
A reader key does not open the console. See [analyst console access](analyst-access.md#console-access).

## Deploy

These commands require the operator-supplied `secret_store` executable described in the [adapter contract](../ops/fly/SECRETS.md#secret-store-adapter-contract).

Run the full deploy from a clean checkout of the pinned revision:

```sh
secret_store run -- bash ops/deploy.sh
```

Use one invocation for all apps a change needs. Add `--dry-run` to read the plan first.
Use flyctl v0.3.226 or later; run `fly version update` if the version check refuses it.
Repeat `--app <name>` to select a subset:

| Change | Apps to select |
|---|---|
| dbt models, served contracts, function declarations or runner behavior | `mdp-core-runner`, `mdp-functions`, `mdp-control-api`, `mdp-data-api`. Add `mdp-showcase` when its code or contracts change too. The full command above includes them. |
| Data API code compatible with the existing tables and services | `mdp-data-api`. This selection runs no restore rebuild and no release check. |
| Showcase UI compatible with the existing APIs | `mdp-showcase`. Include `mdp-control-api` when people or link secrets change, so both receive the same configuration. |

Use the full deploy when a change spans services and its smaller selection is uncertain.
Read [the app map](../ops/CLAUDE.md#fly-deployment-and-storage) before selecting infrastructure apps.

The script waits for green CI on that commit. Skipped and neutral rows neither block nor
satisfy a required workflow. Missing or pending checks keep the gate waiting; failures block it.
`ops/ci-wait.sh <sha>` runs the gate alone. It waits up to an hour for conformance, control-ci,
dbt-ci, functions-ci and generated-drift, plus r-ci when its watched paths change.
The `showcase-artifacts-postgres` check must also pass on that revision. It proves the
reviewed peek permissions on PostgreSQL. A missing or skipped check keeps the gate waiting.
Every observed workflow must pass. GitHub read errors keep it waiting.
`MDP_CI_WAIT_TIMEOUT_S` and `MDP_CI_WAIT_POLL_S` set the wait and poll intervals.
Build-only runs skip CI. `MDP_SKIP_CI_WAIT=1` bypasses it for an emergency hotfix;
inspect the pinned revision's Actions page before using that override.

After CI, secret pre-flight checks every selected app's import values before any remote write,
including provisioning, staging secrets, bootstrap and runner holds.
An unsafe value names its key without printing its contents. Follow its recovery step in secret store.

Secrets come from secret store JSON. The mapper emits plain double-quoted values or single-line
triple-double-quoted values so Fly receives the original bytes. Fly does not unescape an env file.
Keep `MDP_SHOWCASE_PEOPLE` compact and on one line, with `#` encoded as `\u0023`.
Read [the secret map](../ops/fly/SECRETS.md) before changing a value.

Showcase deploys prepare their Stack and link preview artifacts inside the pinned archive.
Collection and validation finish before any image replacement. Prepare the
[showcase deploy inputs](#showcase-deploy-inputs) before running the command.
`MDP_SHOWCASE_OVERLAY` optionally supplies measured Stack facts from the same revision.
See [showcase artifacts](../ops/showcase/README.md#deploy-overlay) for image checks.
If preparation fails, follow [Recover](../ops/showcase/README.md#recover), then rerun the deploy.

Every selected image, bootstrap and restore rebuild uses the same revision.
Selecting Postgres, functions or core-runner runs bootstrap once to apply migrations, grants
and raw schemas. See [bootstrap.sh](../ops/fly/bootstrap.sh) before changing that step.

Runner holds, release checks and restore rebuilds run only when `mdp-core-runner` is selected.
In the full invocation, runners update while stopped, with no schedule and an `mdp_deploy_hold`
owner marker. Functions then passes its health check and control-api deploys.
Before runner starts, the release check reads the revision and health of every serving
functions and control-api machine. It checks those services even when a subset omits them;
they must already serve the pinned revision. Select both services with the runner to update them.

The script restores the hourly schedule, clears the marker and starts each held runner once.
A second release check precedes restore runs for the hourly, daily and weekly global marts.
Each restore uses its cadence's newest scheduled cycle; without one, there is nothing to rebuild.
These runs happen before any selected data-api replacement. A failed rebuild stops the deploy.
Selections without core-runner run neither this release check nor these rebuilds.
Showcase follows data-api when both are selected. Open `/ops` after the deploy.

Image-consuming calls retry only missing registry manifests, four times after waits of
5, 10, 20 and 40 seconds. Other failures stop immediately. If the manifest stays unavailable,
rerun the same deploy command from the same revision.

A stopped deploy that selects core-runner can leave held machines `stopped` or `created`.
Earlier starts may be running.
A foreign hold refuses an update. Confirm that its deploy has stopped before taking it over:

```sh
MDP_DEPLOY_TAKEOVER=1 secret_store run -- bash ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api --app mdp-showcase
```

Run this from the same revision. It waits for running machines and releases held machines only
after the release check. Run one deploy at a time; the hold check is not an atomic lock.
A full deploy skips an unconfigured showcase. Complete [viewer access](#give-a-viewer-access)
then deploy it with control-api. See [held runners](../ops/runbooks/runners_held.md) for recovery.

### Showcase deploy inputs

A configured full deploy includes showcase after data-api. Its artifact collection needs these inputs
on the operator machine, in addition to the [runtime secrets](../ops/fly/SECRETS.md#showcase):

- `MDP_SHOWCASE_DENY_NAMES` in the selected secret-manager configuration: the private names to reject, one per line.
  Keep its values out of Git and logs. Check it with `python3 ops/fly/secret-map.py check mdp-showcase`.
- Full Git history for the linked files and commit subjects. Run `git rev-parse --is-shallow-repository`;
  if it prints `true`, run `git fetch --unshallow` before deploying.
- A current tenant count. Supply `MDP_SHOWCASE_TENANT_COUNT` as a non-negative integer from
  a read-only row count of `control.tenant` taken with the control role (operator SQL, not a
  Workbench query), then export it in the deploy shell.
  Without a numeric value, the deploy counts the JSON records from `pnpm --silent --dir control mdp tenants list`.
  That path needs a reachable `MDP_CONTROL_API_URL` and `MDP_API_KEY` (or `MDP_ADMIN_API_KEY`).
  Follow [Reach control-api](#reach-control-api) when the API is private.

The deploy enables `MDP_SHOWCASE_PROBE_HOSTS=1` for public health checks.
A positive tenant count also needs the reviewed `console_tenant_review` entry in the link manifest.
The deny list and tenant count stay out of the Fly runtime secret map.
The dry run prints the preparation step but does not validate these inputs or contact the API.
After checking them, run the deploy command above with `--dry-run --app mdp-showcase`.

## Daily status

Start the daily check with the Platform status card at the top of `/ops` (or `/status`),
then follow its alert groups to their runbooks; “no guide” means no matching guide is seeded.
`pnpm --dir control mdp status` prints the same roll-up using the normal control API credentials
and exits non-zero on `broken`, for cron. The card explains the verdict rules and shows each
cadence and scope's last close, age and opener build, open cycles, the last 24 hours of alert
email activity, and the running data API build compared with control. An unknown build is
visible as `attention`; the version comparison describes code, not whether every mart rebuilt.
Heartbeat delivery appears with its result and age, backed by `control.audit_log`.
`/health/status` is a separate public cadence summary: check time, health, overdue cadence
names and a recovery step. It returns 200 when healthy and 503 otherwise, without identifiers
or credentials. Showcase forwards it for the outside cadence watcher. Open `/ops` for detail.

## Give a viewer access

Run these commands from the repository root. Replace `<handle>` with a short lowercase handle.
Keep names, addresses and keys in the secret manager. Do not paste them into tickets or evidence.
First [reach control-api](#reach-control-api) and load the operator's admin key into `MDP_API_KEY`.
For step 1 only, supply a privileged `MDP_CONTROL_DATABASE_URL` through a private Postgres connection;
follow [admin-key setup](../ops/fly/SECRETS.md#admin-keys). Later link requests need only the API URL and admin key.

1. Create one admin key for this person. Capture its one-time output in a shell variable:

   ```sh
   showcase_person_key="$(ops/fly/postgres/admin-add.sh showcase-<handle>)"
   ```

   Save that value securely for the `admin_key` field in step 3. Keep the operator key in `MDP_API_KEY`.

2. Read the new key's id through the API:

   ```sh
   pnpm --dir control mdp keys list
   ```

   Find the row whose label is `showcase-<handle>`. Copy its `id` into `api_key_id` below.

3. Set `MDP_SHOWCASE_PEOPLE` in secret store as an array with one object per person.
   Use compact one-line JSON; write any `#` in a name as `\u0023`, then store it with the command below.
   This is the shape only; fill every empty string with a runtime value:

   ```json
   [{"handle":"","display_name":"","admin_key":"","api_key_id":""}]
   ```

   Use the key from step 1 and the id from step 2. Keep all existing people in the array.
   Put the filled JSON in the secret manager with `secret_store set MDP_SHOWCASE_PEOPLE`.
   Choose the optional `MDP_SHOWCASE_IDLE_DAYS` and `MDP_SHOWCASE_MAX_DAYS` settings if the
   14-day idle and 60-day total defaults do not fit. Both accept 1 through 365, with idle no greater than total.
   Check the other required inputs in the [showcase secret map](../ops/fly/SECRETS.md#showcase).
   Continue with the reader key.

4. Mint the showcase's reader key and store it in secret store. Do this once per platform;
   skip it when secret store already has `MDP_SHOWCASE_READER_KEY`.
   The key goes straight into secret store and is not printed:

   ```sh
   showcase_reader_key="$(pnpm --silent --dir control mdp keys create --role reader --global --label showcase-reader | jq -er .api_key)" &&
     printf '%s' "$showcase_reader_key" | secret_store set MDP_SHOWCASE_READER_KEY > /dev/null
   ```

   A reader key reads global marts through the data API and nothing else: control-api refuses it,
   and so does every tenant mart. To replace a leaked key, run the same command, deploy in step 5,
   then revoke the old one with `pnpm --dir control mdp keys revoke <id>` (`keys list` shows its id).

5. Deploy the showcase and refresh control-api's copy of the people and link secret.
   A first platform install also needs the bootstrap described in the [showcase guide](../control/apps/showcase/README.md).
   Prepare the [showcase deploy inputs](#showcase-deploy-inputs), then preview the apps and deploy:

   ```sh
   secret_store run -- bash ops/deploy.sh --dry-run --app mdp-control-api --app mdp-showcase
   secret_store run -- bash ops/deploy.sh --app mdp-control-api --app mdp-showcase
   ```

   The deploy creates the Fly app if it is missing. After its health check passes, mint the link.

6. Mint the first link through the API:

   ```sh
   pnpm --dir control mdp showcase link --person <handle>
   ```

   Share the printed URL privately with the person. They open it or scan the QR and press **Sign in**.
   If their sign-in ends, run the same command for a fresh link. Links come from the platform operator;
   email delivery needs an email provider key and a delivery path. Use the command above today.

## Data engineer: add a source

Start with the [glossary](glossary.md) and [Contributing](../CONTRIBUTING.md).
Use the smallest real collector,
[functions/src/mdp_functions/sources/kexp_plays/function.py](../functions/src/mdp_functions/sources/kexp_plays/function.py),
as the declaration to copy; its parsing and paging live in [kexp.py](../functions/src/mdp_functions/kexp.py).
For a mid-size collector with its parser in the same file, copy
[billboard](../functions/src/mdp_functions/sources/billboard/function.py).

0. Check `fetch/forbidden.py` before you start:
   [the forbidden-path list](../functions/src/mdp_functions/fetch/forbidden.py) names requests the runtime refuses.
1. Run `pnpm --dir control mdp new function example_source --class bronze --cadence daily`.
   Copy the starter's declaration into `functions/src/mdp_functions/sources/example_source/function.py`.
   Replace its source key, table, record schema, hosts and parser with your source's values.
   Declare targets if each request reads a configured account or playlist.
   Add fixture responses and count every observed row as yielded or rejected.
2. Run `source ops/local/env.sh`, then
   `uv run --project functions mdp run example_source --fixture --target pg_local`.
   Inspect receipts and rejected records.
3. Run `uv run --project functions mdp sources export`; review generated source YAML, raw
   bootstrap, uniqueness tests and editable invoke/export/close stubs. Add staging and a
   same-cadence dependent with manifest filtering before deduplication.
   Add the source's row to `docs/sources/annotations.csv`, run
   `uv run --project functions mdp sources doc`, and review the catalog.
4. Run `bash ops/ready.sh` before the PR. It selects checks for your changed files; CI runs the full set.
   Submit declarations/tests/generated artifacts
   through a PR; after review, the maintainer deploys the service and runs
   `pnpm --dir control mdp register` with control auth.
5. Open `/functions/example_source` in the local console to configure targets/knobs, run a fixture or invocation,
   and follow `/runs/:id` and its trace. Runtime settings and code ownership are in
   [functions conventions](../functions/CLAUDE.md).

Fixtures live under `sources/<source>/fixtures/`, one request/response pair per JSONL line.
The [fixture transport](../functions/src/mdp_functions/http.py) matches the HTTP method, URL and query exactly.
Use the cases that apply to your source:

- `normal.jsonl`: a successful response with the expected records.
- `drift.jsonl`: a response whose required shape changes, so the collector detects drift.
- `partial.jsonl`: an incomplete result or bad row alongside valid data, so coverage stays honest.
- `not_found.jsonl`: a missing resource response, usually HTTP 404.
- `unauthorized.jsonl`: an authentication refusal, such as HTTP 401, and any recovery response the source tests.

The default case is `normal`; select another with `MDP_FIXTURE_SCENARIO=drift` before the fixture command.
See the [KEXP cases](../functions/src/mdp_functions/sources/kexp_plays/fixtures/)
and the [SoundCloud authentication case](../functions/src/mdp_functions/sources/sc_playlist/fixtures/unauthorized.jsonl).

### Launch a new source

1. Run its normal and failure fixtures. Declare `min_target_coverage` when the default
   is unsuitable. The default is 90% for ten or more targets and 100% for smaller sets.
2. Run `pnpm --dir control mdp targets probe <set>` before activation. Fix stale URLs.
3. Read the canary report path printed after deploy. Checks run in the background for
   at most five minutes and use `MDP_ADMIN_API_KEY`. Each line links to a function;
   the last line counts `passed`, `failed`, `not_due`, `skipped` and `timed_out`.
   A dry-run probe samples one active target and validates one output record in memory.
   It uses the collector's fetch, parser and declared schema. It creates no run, cycle,
   close number, cursor, dump or warehouse row. Weekly targets keep their weekday gate.
   Only sources declaring free public probe requests may run. `am_playlist` and
   `am_playlist_weekly` read public Apple playlist pages without API credentials or paid transport.
   Other sources report `skipped` with `not declared probe-safe`. Shared host health, rate limits
   and backoff apply; unavailable capacity reports `skipped` without waiting.
   No active target or tenant means `skipped`. Sources requiring stateful execution or paid
   request accounting also report `skipped` with a reason; inspect their normal function run.
   Only `failed` and `timed_out` open `source_canary_failed`. Outcomes go to the audit log.
   Probes never refresh cadence health or block deployment. Open the linked function to inspect
   a failure, then rerun `uv run --project functions python ops/fly/resilience-checks.py`.
4. On the first scheduled run, check successful targets against the floor, stale-target
   warnings, drift column names and the cycle's close. Status shows the Retry launcher.

Imports and activation record a pending probe and return without a network check.
The control worker checks up to 20 resolved targets each minute, with a 15-second
request budget per pass. Unavailable checks stay pending for a later pass. Probes share
the host request cap; their challenges and retry delays never pause collection.

Set a source's enabled state through the audited CLI:

```sh
pnpm --dir control mdp knobs set <source_key> --enable --batch-size 10 --max-concurrency 2
pnpm --dir control mdp knobs set <source_key> --disable
pnpm --dir control mdp status <source_key>
```

The API records the key holder as the actor. Omit either size option to keep its current value.

### What the platform refuses

| Refused | Why and what to do instead |
|---|---|
| Cookie headers and cookie jars | Visitor sessions stay private; fetch public pages and observe only Set-Cookie names ([transport](../functions/src/mdp_functions/fetch/forbidden.py)). |
| Login and session reuse | Collection uses public surfaces; use a documented public feed ([source guide](#data-engineer-add-a-source)). |
| Own network clients | Every request needs lineage; use `ctx.http` and run `uv run --project functions python -m mdp_functions.source_lint` ([transport](../functions/src/mdp_functions/fetch/forbidden.py)). |
| Browser and render tiers | Page scripts and challenges cannot run; parse public HTML or static script text (fetch rules). |
| User-agent rotation | Collection identifies itself honestly; keep the platform user agent (fetch rules). |
| Retrying through a block | A challenge ends collection; review the block and use another public surface ([runbook](../ops/runbooks/scrape_blocked.md)). |
| Model calls outside gold | Enrichment needs a prompt, budget and lineage; land source text in bronze, then use a gold `llm_step` ([local starter](#add-a-local-llm-step)) or `ctx.jev` ([typed decisions](jev.md)). |

A blocked fixture stops the current client but leaves no lasting host pause. To clear an older local
pause, run `pnpm --dir control mdp host unpause example.org` after sourcing `ops/local/env.sh`.
This command refuses production connections. Response fixtures can use a list of header pairs to
preserve repeated Set-Cookie headers; never land their values.

### Add a local LLM step

Start the [local stack](DEVELOPING.md#quickstart), then run:

```sh
source ops/local/env.sh
pnpm --dir control mdp new llm-step chart_label
uv run --project functions mdp run chart_label --fixture --target pg_local
```

The command creates a gold function that labels rows of `marts.mart_chart_history`, a prompt under
`control/prompts/chart_label/1.md`, and responses under the source's `fixtures/llm.json`.
It inserts the immutable prompt, the model step with its computed hash, and a daily budget into
local control. The `local-stub` model replays those responses in memory without a key or listener.
Calls still pass through the transport refusal and budget ledger. Fixture usage records no spend.

Edit the function's declared inputs and prompt for your task. Inputs need `input_ref` and
`input_version` from `mdp_input_identity`; the starter mart already has them.
Before submitting a new source, add its catalog annotation and run `mdp sources export` and
`mdp sources doc` as described above. Local model configuration never enables a live provider.

## Data scientist: ship a mart

Start with the [glossary](glossary.md) and [Contributing](../CONTRIBUTING.md).
Copy [mart_chart_history.sql](../dbt/models/marts/global/mart_chart_history.sql), an 18-line mart,
and only its `mart_chart_history` model entry in
[_marts__models.yml](../dbt/models/marts/_marts__models.yml).
The example selects chart rows and attaches rights flags without hiding rows.

1. Run `pnpm --dir control mdp new mart mart_example`; write its enforced YAML contract before
   SQL, using the example entry in the generated YAML stub and the example SQL in the new model.
   Rename the model entry and adapt its columns, types and `meta.grain` (the columns that identify one row).
   Set one cadence and the appropriate scope/layer; the example inherits global scope and silver from
   [dbt_project.yml](../dbt/dbt_project.yml).
2. Open `/workbench`, create a session, select the model/draft and a closed input cycle, then
   Preview. Inspect Explain and results; use Backtest with two cycles to compare business rows.
   Staff builds use current permitted global inputs for both cycle contexts. They do not rebuild raw-reading
   ancestors. If an input is restricted, use the global relation named in the error or ask an operator for the full build.
   Invoke models stay inert in the scratch schema.
3. Use Save as PR after a successful preview/backtest of the exact draft; review the diff and
   confirm publication. GitHub access enables publication from a disposable checkout.
   Without GitHub access, the page gives the reviewed SQL and contract patch to download or copy.
   Save it as `<model>.patch` in your clone's root. Use the branch and model names from the page:

   ```sh
   git fetch origin && git switch -c <branch> origin/main
   git apply <model>.patch
   bash ops/ready.sh
   ```

   Then push the branch and open a pull request with the `workbench` label.
   No push access? Send the patch file to the data team.
   For a new mart, finish and enforce the generated contract stub during PR review.
4. Validate your model in the fixture environment with
   `uv run --project dbt dbt build --project-dir dbt --profiles-dir dbt/profiles --target pg_local --vars '{dry_run: true}' --select mart_example`.
   Build its upstream fixtures/models first; scheduled selectors do not expand across cadences.
   To try the unchanged chart example locally, run
   `uv run --project functions mdp run billboard_hot100 --fixture --target pg_local`, then
   `uv run --project dbt dbt build --project-dir dbt --profiles-dir dbt/profiles --target pg_local --vars '{dry_run: true}' --select +mart_chart_history`.
5. Generate the complete catalog with `uv run --project dbt dbt docs generate --project-dir dbt --profiles-dir dbt/profiles --target pg_local`,
   then `pnpm --dir control --filter @mdp/data-sdk generate`. If the mart needs an API,
   run `pnpm --dir control mdp scaffold api mart_example`.
For a showcase page, follow [Add a room read](../control/apps/showcase/README.md#add-a-room-read) through the browser check.

   Run `bash ops/ready.sh` before the PR. It selects checks for your changed files; CI runs the full set.
   Include regenerated artifacts and validation in the PR.

## Data scientist: measure the movement rules

The movement rules live in seeds (`dbt/seeds/movement_parameters.csv`, `song_age_parameters.csv`,
`artist_stage_thresholds.csv`, `song_cluster_rules.csv`, `playlist_reach_tiers.csv`); [movement](movement.md)
explains each one. Edit a value and run `dbt seed`. Before changing a rule for good, measure it:

Commit each method's SHA and actual rule-selection day (`chosen_on`) in
[choices.json](../ops/backtest/choices.json) before capture. The harness reads the committed file;
a changed method needs a new name. Read [the backtest guide](../ops/backtest/README.md), then run:

```sh
MDP_BACKTEST_SCRATCH=/tmp/mdp-backtest-weekly bash ops/backtest/weekly.sh /tmp/mdp-backtest-report
```

The script captures, replays, labels and scores under `nice -n 10`. It installs no schedule;
the caller runs it weekly. Capture reads the configured warehouse as `service_read` through the existing proxy.
Set `MDP_BACKTEST_READ_URL`, or leave it unset to use secret store's `MDP_SERVICE_READ_URL`.
Runner start mirrors define estimated quiet windows. A wait stops after one hour.
Check runner timing in `/ops` before retrying a refused capture.

Replay uses a private container at each pinned commit. The weekly command attempts cleanup on exit.
If cleanup fails, copied inputs can remain. Follow [backtest recovery](../ops/backtest/README.md#recover)
to remove the private container, volume and scratch data.
Open `/tmp/mdp-backtest-report/report.md` and `report.json`; `labels.json` records the outcome reference.
Under fourteen distinct history days, the report withholds precision and recall.
Only observations after the method-selection cutoff count toward performance.

The harness measures fixed rules. Searching weights or thresholds for a better score is fitting,
which requires the [learning boundary](architecture.md#rights-annotation-and-learning_gate).

## Onboard a tenant

Resolve and activate the imported members at `/targets/:id`; imports start pending.
`/tenants` lists tenant status and timezone, supports creation and edits, and links to the keys
command. From the CLI, use `pnpm --dir control mdp tenants list` or
`pnpm --dir control mdp tenants patch --tenant <slug> --name "<display name>" --status active`.
The slug stays fixed; status is `active` or `inactive`. A duplicate slug and a missing tenant return
plain errors. An inactive tenant cannot use its keys or run scheduled jobs.

## Read the weekly screen

1. Open `/screen/weekly` for delivery status, freshness, activity and accounting.
2. Follow affected runs to `/runs/:id` and unresolved alerts to `/ops`; use linked traces,
   audits and runbooks to distinguish data coverage from transport or input failures.
3. Change source settings at `/functions/:source_key`. Route
   cadence, schema, prompt/model and mart changes through PRs; approved knobs stay in the UI.
4. Check [deployment/acceptance status](DEVELOPING.md#status) before treating fixture or local
   results as live-provider coverage. Missing inputs and incomplete acceptance remain explicit.

## Alerts

Open alerts need attention: they are unresolved and unacknowledged.
Acknowledged alerts have a separate quiet count at `/ops`.
Failures open alerts. Coverage opens one when a floor is missed or a source is partial in two consecutive scheduled runs.
A full scheduled run resolves older run warnings for that source, cadence and scope.
A canary warning also needs a passing canary result newer than its last failure.
Schema drift and parked inputs keep their own review steps.
A build that closes the current scheduled cycle resolves older failures for that cadence and scope once its transform passes and its close stamps reach the warehouse.
That build can be the scheduled one, a Retry or a deploy restore; a Replay of an older cycle resolves nothing.
A bronze close alone resolves nothing. The failure banner offers **Retry cycle** only while the current cycle is open. A new run attempt can open a fresh alert;
settling the same attempt cannot reopen its resolved alert.
Manual runs, fixture runs and canary cycles do not clear scheduled alerts.
Each automatic resolution records `system:recovery` and a reason in the alert and audit log.

Open `/ops#alerts`, follow the runbook, then use **Acknowledge** to record that someone is handling it.
Acknowledging does not claim recovery.
**Resolve** closes an alert by hand; both are admin actions and audited (`alerts.acknowledge`,
`alerts.resolve`). Each alert row carries `resolved_by` and `resolution_reason`, so one closed by
recovery reads `system:recovery` with the run or cycle that recovered it.
The service has no update grant on alert rows: runtime recovery resolves them only through
`control.resolve_recovered_alerts`, and reference alerts through `control.resolve_reference_alert`.

A function's knobs (enabled state, batch size, concurrency) change through the audited CLI,
`pnpm --dir control mdp knobs set <source_key> --enable|--disable [--batch-size n] [--max-concurrency n]`,
or its function page. Pausing a source stops its scheduled runs until you resume it; it does not
resolve the source's open alerts.

Once the owner sets `RESEND_API_KEY` or `SMTP_URL`, `MDP_EMAIL_FROM`, and `MDP_EMAIL_TO`, email goes to `MDP_EMAIL_TO` after the next authorized deployment.
The worker sends open critical alerts and selected warning classes.
Check `email.sent` in `/audit`, then follow [email setup](../ops/fly/SECRETS.md#alert-email).
An external heartbeat is separate from email: with `MDP_HEARTBEAT_URL` set on `mdp-core-runner`,
the runner sends one GET after each scheduled build whose cycle closed and mirrored.
The request has a ten-second hard deadline. `control.audit_log` records `heartbeat.sent`,
`heartbeat.skipped` when the URL is unset, or `heartbeat.failed`, without the secret URL.
Open `/ops` for the latest result and age, then follow
[external heartbeat](../ops/fly/SECRETS.md#external-heartbeat) to set it up and drill it.

For held runner inspection and release, follow [deploy recovery](#deploy).
