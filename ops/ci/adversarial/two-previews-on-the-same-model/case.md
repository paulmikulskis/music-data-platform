# two previews on the same model

Detector: workbench.

Expected: both succeed in separate `wb_*` schemas.

Rule: `preview-schema-isolation`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes live `previews` through the shared runner.
