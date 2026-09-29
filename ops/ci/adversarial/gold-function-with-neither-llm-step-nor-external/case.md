# gold function with none of `llm_step`, `model_steps`, or `external`

Detector: registration.

Expected: refused at `mdp sources export`.

Rule: `gold-declares-egress`.

Runbook: [dbt-failure](../../../runbooks/dbt_failure.md).

Fixture: `fixture.json`; executes registration `gold` through the shared runner.
