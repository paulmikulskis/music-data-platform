# bronze function yielding fewer rows than observed without `reject`

Detector: runtime accounting.

Expected: run `partial`, `accounting_mismatch`, receipt names the counts.

Rule: `observed-equals-yielded-plus-rejected`.

Runbook: [accounting-mismatch](../../../runbooks/accounting_mismatch.md).

Fixture: `fixture.json`; executes pytest `test_runtime.py::test_record_accounting_mismatch` through the shared runner.
