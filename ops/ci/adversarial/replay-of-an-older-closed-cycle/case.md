# replay of an older closed cycle

Detector: `steps_override` with `var('cycle_id')`.

Expected: binds to that cycle; zero vendor calls; same manifest.

Rule: `closed-cycle-replay-manifest`.

Runbook: [cycle-not-found](../../../runbooks/cycle_not_found.md).

Fixture: `fixture.json`; executes lifecycle `g` through the shared runner.
