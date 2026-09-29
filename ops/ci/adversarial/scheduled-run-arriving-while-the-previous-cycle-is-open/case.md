# scheduled run arriving while the previous cycle is open

Detector: supersession.

Expected: previous cycle superseded; in-flight page lands; no new batches; new cycle opened.

Rule: `scheduled-open-cycle-supersession`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes lifecycle `e` through the shared runner.
