# Core production clock

`ops/deploy.sh --app mdp-core-runner` builds linux/amd64 remotely and pushes
`registry.fly.io/mdp-core-runner:<label>`, then creates (`fly machine create`) or updates the
machines that [machines.py](machines.py) lists: `mdp-<cadence>` for the global scope, and
`mdp-<cadence>-<slug>` for every active tenant and every cadence with tenant transform models
(`DBT_MDP_SCOPE=tenant:<id>`, `MDP_TENANT_SLUG=<slug>`). `bootstrap.py` writes the active
tenants for it on every deploy. Each machine is a 2 GB shared-cpu-2x VM, no services, restart
policy `no`, a 300 s stop timeout, `--schedule hourly`, with `MDP_RUN_REASON_CATEGORY=scheduled`
and `MDP_CORE_GATE=1`; the image entrypoint is `ops/run.sh` and the command is its cadence. Each is
updated only while stopped, with `--skip-start`, and keeps its name. The machines start after
mdp-functions is healthy, or at once when mdp-functions is not in the deploy; each start runs one
gated tick and re-arms the schedule. A tenant machine the list no longer holds (an inactive tenant)
is destroyed once it is stopped; the deploy after the tenant is active again recreates it. Before
control-api and data-api deploy, one `mdp-rebuild-<cadence>` machine per global cadence runs Core's
restore (`MDP_RESTORE=1`, reason other) on the new image, so every served mart matches the new data
API's contracts. Each is created and polled until its launch is done, then started on the same id; a
start that answers `failed_precondition` (the image is still being prepared) is retried up to five
times, 15 s apart. Only a machine still `created` after that is destroyed and created once more. The
deploy waits for each, stops on a non-zero exit, and destroys it after.

Fly schedules are fuzzy hourly ticks, not cron, so `run.sh` gates each tick
(`mdp_functions.core_gate`). A tick skips at once only while this period's scheduled run holds the
runner lock; behind an operator's Retry, Replay or restore, or the deploy's rebuild, it waits (30 min
for hourly, 3 h otherwise) and then asks the gate. It runs only when its period has no closed scheduled
cycle: for hourly the last 45 minutes (ticks drift, so two in one clock hour never cost the next hour);
for daily the local day from `due_hour` in the row's `timezone`; for weekly the local ISO week from
(`due_weekday`, `due_hour`), so a missed Monday catches up later that week. A failed scheduled cycle
(left open or superseded) gets one retry, the next tick; after a second failure the period stops, the
runner asks the service to open a critical `cadence_failed` alert (one per period), and the next period
runs again, so a failing cadence never re-collects its paid sources every hour. A tenant daily
tick also waits until the global daily cycle of the current UTC day has closed, and an inactive
tenant's tick is never due. Every other tick prints `GATE ...: NOT DUE` and exits 0 before binding. Registration fills the schedule only for a new row (global daily 02:00 UTC, weekly
Monday 03:00 UTC; tenant daily 05:00 and weekly Monday 05:00 in the tenant's timezone); the
control plane owns it afterwards, and a row with null schedule fields runs on
those defaults. Local runs without `MDP_CORE_GATE=1` are never gated.

Core Retry and Replay (`/ops` Recovery, `pnpm --dir control mdp retry|replay <cycle_id>`) start a one-off
machine through the Fly Machines API with control-api's `MDP_FLY_MACHINES_TOKEN`, copying the
image of the cadence's global machine: `run.sh <cadence> --reason-category other` for Retry,
`run.sh <cadence> --cycle-id <id>` for a global Replay. Tenant Replay is not implemented;
`dbt.replay` and `run.sh --cycle-id` answer `replay_unavailable`.

Quiesce scheduled machines before running the lifecycle harness or flipping the
clock. After verifying the dbt Cloud connection, the clock
switch is `UPDATE control.runner_mode SET runner='cloud' WHERE id=true;` through
control_rt. Existing bound runs finish; new Core bindings refuse `runner_inactive`.
Changing the row back to `core` restores Core admission.

`ops/run.sh` holds the session advisory lock `core:<cadence>:<scope>` (through
control_rt, `ops/runner_lock.py`) for the whole run, so scheduled runs, Retries
and Replays of one cadence and scope serialize. A second run prints
`RUNNER LOCK WAIT` and waits up to `MDP_RUNNER_LOCK_WAIT_S` seconds (default
3600), then exits 75 with `runner_busy`. `run.sh --cycle-id <id>` takes the
scope and tenant slug from that closed cycle, forces reason `other`, runs the
Replay's three commands, and runs a normal full run with a new run id from an
EXIT trap, which restores the current build even after a failed Replay, before
it releases the lock. The lock holder forwards SIGINT and SIGTERM to `run.sh`
and waits for it. `run.sh` passes either signal to the running dbt command as
SIGINT, so the Replay ends and its trap runs the restore. Before the Replay,
`run.sh` writes a `control.runner_restore` row for the lock key, and it deletes
the row after the restore passes. A machine stop sends SIGINT and kills after
300 s. A kill, or a restore longer than that, leaves the row, and the next
`run.sh` under that lock runs that restore first. Run Now under Core
takes the same lock with `pg_try_advisory_xact_lock` and refuses 409
`core_run_in_progress` while a run holds it.

The image includes both locked Python projects because `ops/run.sh` also uses
the functions registry and its PostgreSQL driver. `UV_NO_DEV=1` and
`UV_FROZEN=1` prevent runtime installation of developer dependencies.

The deployed acceptance pauses schedules with
`fly machine update <id> --machine-config '{"schedule":""}' --skip-start --yes`,
checks that they were removed, and restores each original schedule in `finally`.
An empty `--schedule` flag is ignored by this flyctl version; it is not a pause.
