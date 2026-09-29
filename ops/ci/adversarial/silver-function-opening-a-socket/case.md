# silver function opening a socket

Detector: runtime egress block.

Expected: run `failed`, `egress_blocked`.

Rule: `silver-no-egress`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes pytest `test_enrichment_runtime.py::test_silver_socket_is_failed` through the shared runner.
