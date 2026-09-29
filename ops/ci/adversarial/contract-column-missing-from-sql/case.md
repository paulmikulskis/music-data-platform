# contract column missing from SQL

Detector: dbt contract.

Expected: build fails on the model.

Rule: `enforced-mart-contract`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes dbt `contract` through the shared runner.
