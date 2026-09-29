# function writing a table outside `writes`

Detector: runtime routing.

Expected: run `failed`, `undeclared_write`.

Rule: `declared-writes-only`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes pytest `test_runtime.py::test_undeclared_write` through the shared runner.
