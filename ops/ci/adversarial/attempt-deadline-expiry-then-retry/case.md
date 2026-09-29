# attempt deadline expiry then retry

Detector: attempts.

Expected: new attempt, fresh deadline, resumes from last uploaded page.

Rule: `attempt-deadline-fresh-retry`.

Runbook: [invoke-timeout](../../../runbooks/invoke_timeout.md).

Fixture: `fixture.json`; executes lifecycle `kill` through the shared runner.
