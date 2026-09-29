# tenant-bound mart in a shared schema without a tenant column

Detector: CI tenant lint.

Expected: fails; with the column it passes.

Rule: `shared-tenant-mart-exposes-tenant-id`.

Runbook: [scope-mismatch](../../../runbooks/scope_mismatch.md).

Fixture: `fixture.json`; executes lint `tenant` through the shared runner.
