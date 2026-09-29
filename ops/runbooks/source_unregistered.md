# Source unregistered

An enabled streamline names a function absent from the deployed functions registry.
The deploy advisory check opens one warning per source, including sources without a probe.

1. Open `/functions/<source_key>` using the key named in the warning.
2. Disable the streamline if the source is retired or belongs only to a fixture.
   Otherwise, deploy the function that declares that source key.
3. Rerun `uv run --project functions python ops/fly/resilience-checks.py`.
   Resolve the warning at `/ops` after the source no longer appears in the registry report.

The check changes no knobs and creates no collection run.
If the registry check is unavailable, inspect access to `mdp-functions` with
`bash ops/fly/fly.sh status --org "$FLY_ORG" --app mdp-functions`, then rerun the check.
