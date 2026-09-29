# Operations conventions


## Before merging

Main takes only branches whose current head passes `ops/merge-gate.sh <branch>`.
Commit the changes, merge main into the branch, and run the local checks first.
The gate pushes that commit, opens a draft PR or reuses its open PR, and waits for CI.
It prints the PR link and never merges. Review that PR before merging into main.
A new commit needs another gate run. Use `python3 ops/ready.py --ci` to print the command.

The five workflows run on every main push and every pull request without path filters,
including docs-only changes and draft PRs. For every SHA, the merge and deploy gates
require `control-ci`, `functions-ci`, `dbt-ci`, `conformance`, `generated-drift`, and
`showcase-artifacts-postgres`. A missing or skipped requirement never counts as green.
The merge gate checks that the checkout is clean before pushing and after CI finishes.
Run `uv run --project functions pytest functions/tests/test_ci_wait.py functions/tests/test_merge_gate.py -q`
after changing the gate or workflow triggers.


## Runtime and clock

`ops/run.sh <hourly|daily|weekly> [--reason-category scheduled|other] [--cycle-id ID]`
accepts `--target pg|pg_local`, JSON `--vars` and a no-side-effect `--dry-run`.
It runs bronze, source freshness, then transform with one run identity and merged vars.
`DBT_MDP_SCOPE` defaults global; tenant:<id> requires tenant_slug in vars or MDP_TENANT_SLUG.
Job registration writes a tenant job's `global_inputs` from the generated `mdp_global_inputs()`;
bind refuses a row that differs (`global_inputs_mismatch`), so rerun `mdp sources export` and
re-register after a tenant model changes its global reads.
MDP_CONTROL_RT_URL registers Core jobs; MDP_RUN_ID can pin an identity. Core and Cloud use
`control.runner_mode` for new bindings; existing bindings finish after a switch.
Full retries resume the bound cycle; replay requires a closed cycle. Do not use dbt retry.
A run holds the session lock `core:<cadence>:<scope>` (`ops/runner_lock.py`, through control_rt)
from start to end, so runs of one pair serialize; a second run waits up to
`MDP_RUNNER_LOCK_WAIT_S`, then exits 75 with `runner_busy`. `--cycle-id` refuses anything but a
closed global cycle of the cadence before it starts (both `run.sh` and `dbt.replay` refuse non-global scope with `replay_unavailable`;
use Retry for the current tenant build), takes `DBT_MDP_SCOPE` and the tenant slug from the
cycle, forces reason `other`, and runs the restore (a normal full run with a new run id) from an
EXIT trap before the lock is released, so a failed Replay still restores. Job registration upserts
`global_inputs` every time.
Selectors/counts and schema rules live in [dbt conventions](../dbt/CLAUDE.md).

Core is the interim deployed clock: one hourly-ticking Fly machine per cadence for global and per
(cadence, active tenant) with tenant models (daily and weekly), stopped between runs; `run.sh`'s due gate turns ticks
into the declared schedule, credits a period only with a closed scheduled cycle, retries a failed one
once per period (then `cadence_failed`), skips only behind this period's scheduled run and waits behind
an operator's run, and never runs an inactive tenant. A tenant weekly tick (Monday, for the call freeze)
also waits for the tenant's daily cycle of the current local day to close; the first period of a tenant's
weekly job, when it opens after the local Monday, freezes no call. A runner deploy destroys a tenant
machine the list no longer holds. Follow [deploy](../docs/operating.md#deploy) for app selection
and the [core runner guide](fly/core-runner/README.md) for machine configuration.
Every passing build except a Replay records its success (`cadence_health --succeeded`), so a Retry
or restore that closes the current cycle resolves its cadence alerts.
After a scheduled build whose cycle closed and mirrored, `run.sh` runs `ops/heartbeat.py`: one GET to
`MDP_HEARTBEAT_URL` under a ten-second hard deadline, so a slow monitor cannot hold the cadence lock.
Each build writes one `heartbeat.sent`, `heartbeat.skipped` (URL unset), or `heartbeat.failed` audit row
with its cadence. `/ops` shows the latest result and its age. Manual work, Replay and restore send nothing
([external heartbeat](fly/SECRETS.md#external-heartbeat)).
Cloud setup is implemented in `dbtcloud/jobs.json` and `dbtcloud/apply.sh`: five jobs, TLS
extended attributes, control.dbt_job registration and DBT_MDP_CADENCE/DBT_MDP_SCOPE variables.
Connection/webhook instructions are in connection and
webhook. Cloud live verification requires missing inputs.
Quiesce Core, verify Cloud, then change `control.runner_mode` as control_rt before enabling
Cloud schedules. Leave Cloud when storage sensors/external waits are required, or a dated
operating comparison favors Core plus Dagster with the workbench replacing the IDE.
Measure H, D and W from `bash ops/ci/lint-dbt.sh -v` and the generated dbt manifest for the
current hourly, daily and weekly selectors, including each active tenant; monthly model
executions are `720*H + 30*D + 4*W + P*C`, plus headroom
(P pull requests, C materialized models per CI build). There is no live Cloud usage reading.


## Fly deployment and storage

The Fly apps run in your configured Fly organization and region. Open their configuration under `ops/fly/`:

| App | Runtime and exposure |
|---|---|
| mdp-postgres | Postgres 17 with pg_lake, private 5432, retained pgdata volume |
| mdp-pg-frontend | public TCP 5432; HAProxy PROXY v2 source allowlist |
| mdp-functions | private api 8080 on a dedicated performance CPU (a shared CPU throttles in the daily bronze burst) and workbench 8085; dumps volume on api only |
| mdp-control-api | private operator API; production auth, Clerk inputs absent |
| mdp-data-api | private typed mart API |
| mdp-core-runner | hourly/daily/weekly scheduled machines, no service listener |
| mdp-mb-db | private MusicBrainz mirror and validated generation metadata |
| mdp-mb-import | MusicBrainz dump importer, no public service listener |
| mdp-showcase | public HTTPS viewer app; reads data-api, control-api and the warehouse as `showcase_wh`; included in a configured full deploy after data-api; prepares its artifact overlay inside the pinned archive |

`mdp-alloy` has configuration but is not deployed without OTLP inputs.
Follow [deploy](../docs/operating.md#deploy) for the command and required app selections.
The guarded Fly wrapper checks org membership and app ownership; builds are remote linux/amd64.
See [the secret map](fly/SECRETS.md) for each app's inputs.
A run pins one revision (`MDP_DEPLOY_REVISION`, HEAD resolved once at start and logged as
`REVISION`): every image, bootstrap's migrations and the rebuild machines come from it, and the build
context holds nothing from the worktree. The deploy refuses a modified tracked file under an archived path.


### Deploy checks and secrets

[Deploy](../docs/operating.md#deploy) owns the flyctl floor, CI gate, secret import formats
and registry retry rules. Check those steps before changing [deploy.sh](deploy.sh).
The showcase accepts a shared IPv4 for public HTTPS. The Postgres frontend needs its dedicated IPv4.
Follow [viewer access](../docs/operating.md#give-a-viewer-access) to configure the showcase.


### Deploy hold

[Deploy](../docs/operating.md#deploy) describes which selections hold runners, check service
revisions and rebuild marts. Use its takeover command only after the holding deploy stops.
See [held runners](runbooks/runners_held.md) for inspection and the outside cadence watcher.


### Postgres and storage

The Postgres image pins pg_lake source 3.5.1; deployed SQL extensions report 3.5.
The Dockerfile owns the `WITH_PG_LAKE` default. Deploy passes this build argument only
when `MDP_PG_WITH_PG_LAKE` is set; `=0` explicitly selects a heap-only image.
Use `--dry-run --app mdp-postgres` to inspect the build command.
Iceberg requires object storage and a passing restore gate; retained non-empty locations
fail the recorded restore check. See [image/gates](fly/postgres/README.md).
First boot runs database/role SQL as superuser, installs extensions, warehouse grants,
private mdp configuration and the UDF. Drizzle separately applies control migrations.
Existing volumes require explicit migrations/UDF installation; init scripts do not rerun.
PGDATA uses a pgdata subdirectory. Supervisord manages Postgres and pgduck_server;
the sidecar has a memory limit, local socket and shared PostgreSQL temporary directory.
Postgres terminates TLS and rejects plaintext; HAProxy enforces the client allowlist after
PROXY v2 decoding. pg_hba sees the forwarded connection, not the public client address.
The root [credentials table](../CLAUDE.md#credentials) is authoritative. Supply secrets per
role; changing environment secrets alone does not rotate existing database passwords.

Until R2 is configured, API dumps use file:///data/dumps on one volume writer. Preserve
that volume and snapshots until retained manifests and destination receipts are verified.
Workbench artifacts use its machine filesystem. `/v1/migrate` copies retained output into
an explicitly registered destination; it does not switch the production warehouse pin.
There is no warehouse-swap runbook or certified swap acceptance in this checkout.


## Inputs, observability and runbooks

`ops/preflight.sh` loads inputs/inputs.env then secret store; process environment takes precedence.
[Status](../docs/DEVELOPING.md#status) points to the generated roll-up, provisioning guide.
Keep credentials and provider payloads out of source, images, logs and evidence.
Service `/v1/health` exposes aggregate dependency status without authentication; detail and
all other service routes require the bearer token. OTLP exports require a configured collector.
The external heartbeat is `ops/heartbeat.py` on the core runner, keyed by `MDP_HEARTBEAT_URL`; the
monitor, its destination and the outage drill live outside this repository
([runbook](runbooks/service_unreachable.md#external-heartbeat)).
Control verifies dbt webhook HMAC and forwards events; the service deduplicates them.
The email outbox requires Resend or SMTP plus sender and recipient settings.
Incomplete setup writes one `email.skipped` row per selected alert with a setup step and makes no attempt.
A delivery failure backs off from 30 seconds to one hour and stops after ten attempts.
Explicit dev auth uses console delivery when no transport is selected.
Open [alert email setup](runbooks/service_unreachable.md#alert-email) for configuration and recovery.
Seed Markdown guides with `pnpm --dir control --filter @mdp/control-api seed-runbooks`.
The [runbook index](runbooks/README.md) covers every guide; file stems map to hyphenated slugs.


## Acceptance

`streamlines.canaries` checks enabled invoke functions through
[canaries.ts](../control/apps/control-api/src/canaries.ts). Each deployment calls the functions
service's dry-run probe. [canary.py](../functions/src/mdp_functions/canary.py) declares a sample
size of one active target and one output record. A single read freezes the live target and spec
in memory. Playlist probes keep the platform, cadence and weekday gate.
The probe uses the collector's guarded HTTP, parser and declared schema validator.
It creates no cycle, run, export, cursor, dump, raw row or completion record. It takes no scheduled
runner lock. Each request uses the shared host slot, rate limit and backoff. A busy or blocked
host reports `skipped` instead of waiting.
Only manifests declaring `canary=True` may probe. All others report `not declared probe-safe`.
Apple and Spotify pages, Shazam charts, Billboard, Bandcamp, iTunes discography and KEXP
opt in through their declarations. These requests need no key, quota account or paid transport.
Bandcamp POST reads still report `skipped`; probes permit only GET and HEAD.
Inspect each declaration's comment before adding a source.
Sources requiring warehouse inputs, preparation, completion state, scheduled context or paid
request accounting report `skipped` with the reason. Inspect the linked function's normal run.
The health policy declares one 25-second probe deadline and a five-second HTTP response allowance.
The service and generated control policy use those values. Client timeouts report `timed_out`.
The RPC has a two-minute work budget. Sources beyond it are reported as skipped.
Each source reports `passed`, `failed`, `not_due`, `skipped` or `timed_out`, with a function link.
Outcomes and validation counts go to the `source.canary` audit row. Only failed and timed-out
checks open `source_canary_failed`. Audit errors leave collected results intact.
Error logs use the error class and catalog message, never exception bodies.
Cadence health and weekly-screen freshness use `scheduled_cycle_sql` from the health policy
and its generated console copy. Manual, canary and backfill cycles never refresh a cadence.
Inspect `/ops` for scheduled freshness and the deploy report for probes.
The advisory registry check runs inside the deployed functions image before probes.
Every enabled source absent from its registry gets one open `source_unregistered` warning,
even if it has no canary declaration. Disable the named streamline or deploy its function,
then rerun the check and resolve the warning at `/ops`.
After deployment, [resilience-checks.py](fly/resilience-checks.py) uses `MDP_ADMIN_API_KEY`
and prints each source plus outcome counts. A refused key stops checks immediately.
The process has a five-minute limit and never fails the deployment.
Read its report, then open the linked function or [canary runbook](runbooks/source_canary_failed.md).

The [acceptance index](ci/README.md) lists all four aggregate scripts and evidence folders.
Lifecycle cases require matching control/warehouse/admin/dbt endpoints, service URL/token,
MDP_CONTROL_RT_URL, ten synthetic targets and MDP_FIXTURE_MODE=1 on a disposable stack.
Before its first reset, explicitly claim it with
`ops/ci/lifecycle.sh --target pg_local --reset --claim`. Later resets require that exact
host:port:database audit marker, core runner mode, fixture identity and no active workers.
Resets delete lifecycle-owned data and references, not shared tables globally.
Only loopback or opted-in .internal/.flycast fixture endpoints pass the host guard;
MDP_LIFECYCLE_ALLOW_REMOTE=1 does not permit the public .example.invalid frontend.
The DuckDB loop rejects --target pg. Never bypass these guards or claim production data.
MDP_LIFECYCLE_SERVICE_SESSION and MDP_SERVICE_COMMAND select an isolated stop/restart target.

The 41-case adversarial catalog checks literal detector output, inventory and runbook links,
then audits declared keys and committed receipt cardinality. Offline runs/skips cannot pass
acceptance. dbt ls detects graph cycles. CI provisions Compose with an explicit heap image;
that suite does not certify pg_lake. Logs live in immutable evidence/adversarial/run-<UTC> folders.
[Status](../docs/DEVELOPING.md#status) links the gate evidence.
Check each gate's target and result before treating it as acceptance.


## Measurement

Run `bash ops/backtest/weekly.sh <report-directory>` for the weekly song measurement.
Read [the backtest guide](backtest/README.md#weekly-report) for method choices, scheduling,
output and cleanup. If private inputs remain after a failure, follow [recovery](backtest/README.md#recover).
