# Fly deployment

Deployment templates describe services in your configured Fly organization and region: mdp-postgres, mdp-pg-frontend, mdp-functions,
mdp-control-api, mdp-data-api, mdp-core-runner, mdp-mb-db (the MusicBrainz mirror) and
mdp-mb-import. Alloy has configuration but lacks OTLP inputs.
[Status](../../docs/DEVELOPING.md#status) links dated app/data proofs and outstanding acceptance.

Postgres is private on 5432 with retained pgdata and native pg_lake installed; heap is active.
The public frontend passes PostgreSQL TLS through HAProxy, decoding Fly PROXY v2 before
applying allow.lst in tcp-request session. PostgreSQL terminates TLS and rejects plaintext.
Functions exposes private api/workbench processes; only api mounts the retained dumps volume.
Control/data APIs stay private; control uses production auth. Core supplies scheduled cadence
machines. App configuration/Dockerfiles live here and in control/apps for the two Node services.

```sh
secret_store run -- bash ops/deploy.sh --dry-run
secret_store run -- bash ops/deploy.sh --app mdp-functions
```

A full deploy runs bootstrap once. It then updates each mdp-core-runner machine with
`--skip-start`, only while that machine is stopped, and deploys mdp-functions. When every api
machine runs the new image and passes its `/v1/health` check, it deploys control-api, then starts
the runner machines and rebuilds each global cadence's marts once on the new image (a restore run
per cadence; a failure stops the deploy), then deploys data-api and alloy. So a promoter a cycle or
the rebuild runs meets the control API of its own release. If a deploy fails before control-api is
replaced, the runners stay stopped on the new image, and the failure line names the rerun. A runner on the previous image sends a pre-stamp bind,
which the new service refuses with `runner_outdated` without closing the cycle. A new runner
against the previous service fails where a declared read changed .
So no runner runs until both sides match. Until control-api is replaced, Run Now gets 409
`runner_outdated`. After bootstrap migrates control, the running previous service fails its
prepared statements on the changed tables until it restarts, so a previous-image run in the wait
fails without building. If a runner wait times out, the deploy stops before mdp-functions; the failure
line names the machines left stopped and the rerun (`--app` is repeatable). Runner machines have
a 300 s stop timeout (`stop_config`, Fly's maximum; flyctl has no `--kill-timeout` for machines),
so `run.sh`'s trap can restore after an interrupted Replay. After a rollback of mdp-functions to
an image without close stamps and a redeploy, the new service's catch-up makes the cycles the old
image closed list/0 and rewrites the `raw.cycles` rows its mirror stripped.

mdp-postgres and mdp-functions build and push first, then restart from the pushed image only once
no core-runner machine runs or is due within `MDP_QUIET_MARGIN_MIN` minutes (default 15), because a
restart drops a running cycle's connections. The due time comes from the runners' scheduler starts; right
after a runner update none is on record, so the restart waits for the next tick. The wait lasts at most `MDP_QUIET_LIMIT` seconds
(default 3 h); on timeout the failure line names the pushed image, and a rerun deploys between runs.
mdp-postgres builds the pg_lake image unless `MDP_PG_WITH_PG_LAKE=0` asks for the heap fallback. To
roll the database image back, deploy the previous image with `fly deploy --image
registry.fly.io/mdp-postgres:<previous label>` in a quiet window; `pg_stat_statements` then stays
installed but unloaded, and the staff query review's reads of `catalog.pg_stat_statements` fail
until the image comes back.

Deploy uses a guarded org/app wrapper and remote linux/amd64 builders. The build context is the
archived paths at the run's one pinned revision (`MDP_DEPLOY_REVISION`, HEAD at start), with nothing
from the worktree; a modified tracked file under an archived path stops the deploy before it starts. [SECRETS.md](SECRETS.md) maps
inputs to apps. Keep pgdata and API dumps volumes when replacing machines.
R2 provisioning and Iceberg restore acceptance are separate from extension installation.

Run deployed acceptance against your own configured stack.
`ops/ci/accept-platform.sh --deployed` runs that wrapper; private API access defaults to an
operator tunnel on 18080. Any reset requires a matching disposable-stack claim and safe
endpoints; remote opt-in never authorizes a production reset. See [operations](../CLAUDE.md).

Configure `MDP_WEBSHARE_MICROCENTS_PER_BYTE` in secret store for billing, and set each
provider budget's `cap_bytes` to its bandwidth allocation (provider scope id is
UUIDv5 of `mdp:proxy:webshare`). Byte exhaustion stops residential requests even
with a zero tariff or a monetary warn-only budget; direct requests continue.
Residential requests fail closed with `proxy_quota_exhausted` if no provider
byte quota is configured. This check is independent of the tariff and monetary
budget action. Usage is metered per completed request; a request already in
flight can cross the remaining byte allocation, and subsequent requests stop.
