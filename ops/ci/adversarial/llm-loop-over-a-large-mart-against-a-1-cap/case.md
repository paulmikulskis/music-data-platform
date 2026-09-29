# LLM loop over a large mart against a fixed budget cap

Detector: reservation.

Expected: run `partial` at or under the cap.

Rule: `reservation-before-llm-dispatch`.

Runbook: [cost-cap-hit](../../../runbooks/cost_cap_hit.md).

Fixture: `fixture.json`; executes pytest `test_enrichment_runtime.py::test_gold_cap_snapshot_and_costsync` through the shared runner.
