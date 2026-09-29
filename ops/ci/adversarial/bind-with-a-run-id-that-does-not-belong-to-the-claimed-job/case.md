# bind with a run id that does not belong to the claimed job

Detector: Admin API identity check.

Expected: `scope_mismatch`; model fails.

Rule: `verified-cloud-run-job-identity`.

Runbook: [scope-mismatch](../../../runbooks/scope_mismatch.md).

Fixture: `fixture.json`; executes pytest `adversarial_runtime.py::test_cloud_identity_model` through the shared runner.
