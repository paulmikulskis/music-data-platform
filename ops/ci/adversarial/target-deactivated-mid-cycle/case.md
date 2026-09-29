# target deactivated mid-cycle

Detector: frozen revision.

Expected: the cycle uses its revision; the next cycle excludes it.

Rule: `frozen-target-membership`.

Runbook: [stale-target](../../../runbooks/stale_target.md).

Fixture: `fixture.json`; runs the frozen-revision deactivation sequence in `functions/tests/adversarial_runtime.py`.
