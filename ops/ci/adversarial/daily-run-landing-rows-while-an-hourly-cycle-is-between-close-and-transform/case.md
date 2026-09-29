# daily run landing rows while an hourly cycle is between close and transform

Detector: watermark.

Expected: hourly build excludes them.

Rule: `immutable-committed-watermark`.

Runbook: [dump-late](../../../runbooks/dump_late.md).

Fixture: `fixture.json`; executes lifecycle `d` through the shared runner.
