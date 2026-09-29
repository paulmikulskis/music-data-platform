# retry after phase 3 failure

Detector: cycle attach.

Expected: zero vendor calls; same watermark.

Rule: `retry-attaches-original-cycle`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes lifecycle `b` through the shared runner.
