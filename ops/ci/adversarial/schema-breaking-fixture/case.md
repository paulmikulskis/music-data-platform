# schema-breaking fixture

Detector: landing.

Expected: dump dead-lettered, model fails, alert.

Rule: `schema-breaking-dead-letter`.

Runbook: [schema-breaking](../../../runbooks/schema_breaking.md).

Fixture: `fixture.json`; executes pytest `adversarial_runtime.py::test_schema_breaking` through the shared runner.
