# job id claiming another scope

Detector: `dbt_job` validation.

Expected: bind refused, model fails with `scope_mismatch`.

Rule: `registered-job-scope`.

Runbook: [scope-mismatch](../../../runbooks/scope_mismatch.md).

Fixture: `fixture.json`; executes live `scope` through the shared runner.
