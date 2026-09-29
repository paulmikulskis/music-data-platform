# history-dependent mart (growth) after a retry

Detector: watermark.

Expected: prior snapshots present; delta correct.

Rule: `history-through-bound-watermark`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes lifecycle `b` through the shared runner.
