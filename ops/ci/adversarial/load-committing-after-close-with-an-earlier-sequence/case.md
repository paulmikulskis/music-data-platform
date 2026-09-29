# load committing after close with an earlier sequence

Detector: committed-dump manifest.

Expected: excluded from that cycle; present in the next.

Rule: `commit-receipt-before-manifest-close`.

Runbook: [dump-late](../../../runbooks/dump_late.md).

Fixture: `fixture.json`; executes lifecycle `d` through the shared runner.
