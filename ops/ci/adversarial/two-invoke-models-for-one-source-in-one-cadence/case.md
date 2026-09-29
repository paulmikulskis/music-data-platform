# two invoke models for one source in one cadence

Detector: CI disjointness check.

Expected: fails, names both.

Rule: `one-invoke-per-source-cadence`.

Runbook: [duplicate-invocation](../../../runbooks/duplicate_invocation.md).

Fixture: `fixture.json`; executes lint `duplicate` through the shared runner.
