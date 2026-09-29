# export and invoke with a target activated in between

Detector: frozen revision.

Expected: invoke uses the export's revision.

Rule: `export-before-invoke-frozen-revision`.

Runbook: [stale-target](../../../runbooks/stale_target.md).

Fixture: `fixture.json`; executes lifecycle `d` through the shared runner.
