# Functions service

The Python service admits durable work, executes declared functions, publishes manifest-last
Parquet and lands records through generation-fenced receipts. Bronze, silver, gold and
universal classes, backfill, retained-output migration, costsync and workbench are implemented.
Drizzle owns control DDL. Runtime uses functions_rt; landing uses loader_wh; reads/previews
use service_read. See [conventions](CLAUDE.md) for accounting and authoring rules.


## Local loop

Use Python 3.12/uv and a provisioned disposable Postgres. Supply MDP_CONTROL_URL,
MDP_WAREHOUSE_URL, MDP_SERVICE_READ_URL, MDP_SERVICE_TOKEN, MDP_CONTROL_ADMIN_URL and
MDP_CONTROL_RT_DATABASE_URL externally; [setup](../docs/DEVELOPING.md#quickstart)
describes role ownership and the matching dbt settings.

The runner binds a local cycle and executes export → function → close. Fixtures match exact
method/URL/query; misses fail. Each DuckDB path has its own control catalog/dump prefix.
Exit the fixture process before dbt opens DuckDB. The API fixture switch substitutes vendor
responses; the separate MDP_FIXTURE_MODE opt-in enables harness routes.


## Service interface

`POST /v1/invoke` returns 202 after admission; `GET /v1/runs/{id}` returns status/receipts.
The [OpenAPI contract](openapi/service.json) includes cycles, repair, backfill, migration,
registry sync, inspection and costsync. Only `/v1/health` is unauthenticated; it reports
aggregate control/warehouse/storage health. Detail, docs and other routes require bearer auth.
Workbench is a separate process: `uv run --project functions mdp workbench serve` on 8085.


## Verification

```sh
uv run --project functions ruff check functions/src functions/tests
bash functions/tests/run-postgres.sh -q
```

Integration tests copy the control schema into disposable databases and remove them afterward.
Missing database inputs cause explicit skips, which do not certify acceptance. Receipt-fence
proofs cover both adapters. Egress/accounting, backfill/migration and workbench tests use the
real runtime with controlled fixtures. Evidence records outcomes.
