# all-null column in the first batch

Detector: inference.

Expected: typed `unknown`, later typed as additive drift.

Rule: `unknown-to-additive-inference`.

Runbook: [schema-drift](../../../runbooks/schema_drift.md).

Fixture: `fixture.json`; executes pytest `test_runtime.py::test_all_null_unknown_then_additive_drift` through the shared runner.
