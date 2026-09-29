# vendor returns HTML instead of JSON

Detector: function parse.

Expected: rejected records with reason; accounting balances.

Rule: `rejected-payload-accounting`.

Runbook: [partial-coverage](../../../runbooks/partial_coverage.md).

Fixture: `fixture.json`; executes pytest `test_runtime.py::test_html_json_response_rejected_accounting_balances` through the shared runner.
