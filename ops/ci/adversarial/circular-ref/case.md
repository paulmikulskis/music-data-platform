# circular ref

Detector: CI dbt lint (`dbt ls` graph construction).

Expected: dbt-failure naming both models in the cycle; repaired acyclic graph passes.

Rule: `acyclic-dbt-graph`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes dbt `circular` through the shared runner.
