# two tenant-bound jobs, same cadence

Detector: scope.

Expected: separate cycles and work keys.

Rule: `scope-in-cycle-and-work-key`.

Runbook: [scope-mismatch](../../../runbooks/scope_mismatch.md).

Fixture: `fixture.json`; executes lifecycle `f` through the shared runner.
