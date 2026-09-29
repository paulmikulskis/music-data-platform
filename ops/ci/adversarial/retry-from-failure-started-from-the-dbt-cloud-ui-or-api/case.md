# retry-from-failure started from the dbt Cloud UI or API

Detector: bind hook (`flags.WHICH == 'retry'`).

Expected: fails before any model with `partial_retry_refused`; Retry (full run) offered.

Rule: `full-job-retry-only`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes lifecycle `f` through the shared runner.
