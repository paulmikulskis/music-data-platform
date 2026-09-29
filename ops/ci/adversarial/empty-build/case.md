# `--empty` build

Detector: macro short-circuit.

Expected: no vendor calls, empty receipts.

Rule: `empty-build-no-side-effects`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes live `empty` through the shared runner.
