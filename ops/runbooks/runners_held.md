# Runners held

A deploy removes runner schedules until compatible services are ready.
An overdue close can mean a deploy stopped before releasing them.
Open `/ops`, then inspect the machines:

```sh
fly machines list --app mdp-core-runner --json | jq '.[] | {id, name, state, schedule: .config.schedule, hold: .config.metadata.mdp_deploy_hold}'
```

A held machine is stopped or created, has no schedule, and carries `mdp_deploy_hold`.
The marker names the deploy that holds it.
A running machine is doing work. Wait for it to stop.
Check the deploy's terminal or log before taking its hold.
Run only one deploy at a time; the marker check is not an atomic lock.


## Rerun

If the original deploy is still running, let it finish.
If it stopped, use the same revision in a clean checkout and run:

```sh
MDP_DEPLOY_TAKEOVER=1 secret_store run -- \
  bash ops/deploy.sh --app mdp-core-runner --app mdp-functions --app mdp-control-api --app mdp-data-api
```

The flag permits replacing the old hold.
The script checks both service releases, restores schedules, and starts the runners.
Do not clear a marker or start a held machine by hand.
Check `/ops` for the next scheduled close.


## Owner help

Ask the owner when the holding deploy's state is unknown, credentials are refused, or the rerun fails.
Give the owner the machine id, hold id, revision and failed check.
Keep credentials out of the report. Open `/ops` after recovery.


## Outside watcher

The `cadence-watch` GitHub workflow runs every 30 minutes.
It reads `/health/status` through the public showcase app.
That route forwards only a check time, health flag, and overdue cadence names from control-api.
It includes active tenant schedules without exposing tenant ids or counts.
No API key, Fly token, database credential, source data or build id reaches GitHub.
A missing close, overdue close, stuck open cycle, stale response or unreachable API fails the check.
A database outage therefore fails the check on GitHub's clock.

The workflow stays disabled until an owner deploys control-api and showcase, then runs:

```sh
curl --fail https://mdp-showcase.example.invalid/health/status
gh variable set MDP_STATUS_URL --body 'https://mdp-showcase.example.invalid/health/status'
gh variable set MDP_STATUS_WATCH_ENABLED --body 'true'
gh workflow run cadence-watch.yml
```

GitHub schedules run from the default branch and can be delayed.
GitHub does not email every repository watcher for every failed scheduled run.
The owner must enable email under GitHub Settings → Notifications → Actions,
select failed workflows, and verify delivery with an authorized failure drill.
Check the workflow's Actions page and the recipient's inbox before relying on it.
Keep the independent heartbeat monitor as a second notification path.
See [heartbeat setup](service_unreachable.md#external-heartbeat).
