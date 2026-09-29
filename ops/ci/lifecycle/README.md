# Lifecycle harness

harness.py owns psql assertions, fixture HTTP plans, Core commands, race synchronization and
scoped resets. accept.py aggregates local acceptance. psycopg parses DSNs; psql performs
warehouse observations with the dbt role and writes with the matched administrator URL.
Assertions do not depend on Docker exec. [Operations](../../CLAUDE.md#acceptance) lists inputs.


## Reset authority

`lifecycle.sh --target pg_local --reset --claim` creates the disposable-stack audit marker
only after endpoint, database, runner and fixture identity checks. Further resets require the
same host:port:database fingerprint; --claim cannot replace another stack's marker.
All effective admin/control/warehouse/dbt endpoints must match, with no alternate libpq authority.
Loopback is allowed; .internal/.flycast requires MDP_LIFECYCLE_ALLOW_REMOTE=1. Public .example.invalid
hosts are refused. Cloud mode, active workers and mismatched identities also prevent reset.

Reset locks lifecycle-owned runs, removes their manifest references in control/warehouse,
then deletes owned outputs/state. Other cycles' dump identities remain unchanged. It uses
scoped DELETE, never global TRUNCATE. Deadlocked reset transactions receive at most two retries.
The DuckDB loop always refuses --target pg; pg_local overrides must address the same claimed
cluster. Its initializer receives sanitized, explicit connection settings.


## Detectors and recovery

Fixture routes require MDP_FIXTURE_MODE=1 and bearer authentication. Vendor response plans
exercise normal tracing, retries and accounting without modifying leases, landing or cycles.
The daily commit race holds a real table lock, observes registration before close and releases
it to prove that only committed receipts enter the appropriate manifest. Tenant probes and
models live in temporary projects generated through the normal exporter.
The two-tenant fixture uses `tenant-selectors.yml` to run its export, probe, close,
staging and mart with the real runner. Production tenant enrichment needs separate
inputs and prompt configuration. Run case `f` to check tenant isolation.
The interruption detector uses both running batch state and durable checkpoints; the fixture
transport's cumulative started counter is not an active-request count.

Bounded polling retries recovery 503 responses; authentication and assertions fail immediately.
Read/bind/idempotent request transport interruptions receive bounded retries. A synchronous dbt
service_unreachable failure gets one retry of the same identity after health recovers.
Runtime deadlines, leases and recovery perform state transitions; the harness does not forge them.
MDP_LIFECYCLE_SERVICE_SESSION selects the stop/restart tmux session (default mdp-test-svc);
MDP_SERVICE_COMMAND must launch that stack's service with consistent URLs, token and dump root.

The loop builds a synthetic accounting mart in a temporary project, using fixture-only collection.
No production adapter or business mart is installed in the main dbt project.
`accept-platform.sh --local --only-lifecycle` labels evidence lifecycle-only and excludes other legs.
Each suite writes immutable lifecycle-run-<UTC> evidence; top-level case files are convenience
copies. Full aggregate evidence links the suite and records failures. Verify harness behavior
with `uv run --project functions pytest ops/ci/lifecycle/test_harness.py -q`.
