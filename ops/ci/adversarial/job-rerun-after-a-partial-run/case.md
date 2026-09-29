# job rerun after a partial run

Detector: work key.

Expected: resumes completed batches; at most one repeated page per in-flight batch.

Rule: `canonical-work-key-batch-resume`.

Runbook: [partial-coverage](../../../runbooks/partial_coverage.md).

Fixture: `fixture.json`; executes lifecycle `c` through the shared runner.
