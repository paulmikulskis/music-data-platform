# fan-out above `max_concurrency`

Detector: permit.

Expected: never more than the cap running.

Rule: `durable-concurrency-permit`.

Runbook: [invoke-timeout](../../../runbooks/invoke_timeout.md).

Fixture: `fixture.json`; executes pytest `test_hardening.py::test_durable_permit_across_workers` through the shared runner.
