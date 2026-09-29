# invoke model as `view`

Detector: CI lint.

Expected: fails: "invoke models are tables".

Rule: `invoke-table-materialization`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes lint `view` through the shared runner.
