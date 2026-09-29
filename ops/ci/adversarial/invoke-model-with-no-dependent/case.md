# invoke model with no dependent

Detector: CI dependent check.

Expected: fails, names the model.

Rule: `invoke-has-dependent`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes lint `dependent` through the shared runner.
