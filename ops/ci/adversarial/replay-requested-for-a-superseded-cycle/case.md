# replay requested for a superseded cycle

Detector: control plane.

Expected: refused; only closed cycles replay.

Rule: `replay-closed-only`.

Runbook: [cycle-not-found](../../../runbooks/cycle_not_found.md).

Fixture: `fixture.json`; executes lifecycle `g` through the shared runner.
