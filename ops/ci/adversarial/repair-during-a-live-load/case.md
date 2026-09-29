# repair during a live load

Detector: Appendix C step 2.

Expected: accepted as queued; executes after the load; one row set.

Rule: `appendix-c-step-2-repair-fence`.

Runbook: [warehouse-unavailable](../../../runbooks/warehouse_unavailable.md).

Fixture: `fixture.json`; executes pytest `test_landing.py::test_repair_during_claimed_load[postgres]` through the shared runner.
