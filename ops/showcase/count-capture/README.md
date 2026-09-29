# Count capture check

Run this check on a private local Postgres initialized by `ops/local/init.sh`.
The fixture uses synthetic rows and the timestamp shapes returned by production.
It holds the control worker's `target-probes` lock while the standalone image starts.
The image must store an exact count without a signed-in viewer.
Its next pass must keep that count and log `already_captured`.

```sh
uv run --project functions python ops/showcase/count-capture/check-image.py \
  --image h-count-capture:fixed --pg-port 5521 --http-port 5523
```

Use free ports and a private database container. The check replaces only its local count fixtures.
It removes its app container on exit. Remove the database container and its volumes afterward.
See [image builds](../README.md#deploy-overlay) to prepare the image.

Each capture pass writes one JSON line with event `showcase_relation_count_capture`.
`captured` is the number of counts stored, not a source's row count.
`skipped` names reviewed relations and reasons such as `already_captured` or `cycle_not_closed`.
A deferred pass reports its reason and stage, such as `runner_unknown` at `runner`.
No row values, build stamps, credentials or exception text enter this log.
Open `/ops` to check the runner, then open source details for missing counts.
