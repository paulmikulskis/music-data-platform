# lost POST response

Detector: UDF idempotency key.

Expected: one run, one set of receipts.

Rule: `udf-transport-idempotency`.

Runbook: [duplicate-invocation](../../../runbooks/duplicate_invocation.md).

Fixture: `fixture.json`; executes pytest `test_runtime.py::test_lost_post_response_idempotency` through the shared runner.
