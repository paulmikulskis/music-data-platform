# service restart with pending loads and queued repairs

Detector: recovery.

Expected: drained within a minute; one row set each.

Rule: `recovery-drains-receipt-fenced-loads`.

Runbook: [warehouse-unavailable](../../../runbooks/warehouse_unavailable.md).

Fixture: `fixture.json`; executes pytest `adversarial_runtime.py::test_restart_pending_loads_and_repairs` through the shared runner.
