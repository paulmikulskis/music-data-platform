# Service unreachable


## Symptom

The function service cannot be reached: it refused the connection, or three run polls in a row
got no answer. A slow service that answers polls fails its invoke with `invoke_timeout` instead.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'service_unreachable' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Check the external heartbeat and health endpoint; retry Run now after recovery. Use `/ops` and follow the function or run trace link.


## When to escalate

Connectivity or the service does not recover. Preserve run and dump identifiers with the trace.


## External heartbeat

Create a check with an external heartbeat service and choose its notification destination.
Store its secret ping URL as `MDP_HEARTBEAT_URL` in secret store for `mdp-core-runner`.
The next authorized runner deployment forwards it.
After a successful scheduled build, the runner sends one GET once the cycle is closed and mirrored.
No secret means no request and one `heartbeat.skipped` audit row.
`/ops` shows `not configured` with the setup link.
Each successful scheduled build records `heartbeat.sent`, or `heartbeat.failed` if delivery fails,
with its cadence and timestamp. The row never stores the ping URL.
Open `/audit` for older results and `/ops` for the latest result's age.
Check the monitor for that first ping before relying on it.

Set the deadline to allow the hourly schedule and its retry.
A shared URL detects loss of runner activity; it does not replace each cadence's status at `/ops`.
The monitor and notification destination must work when the control database is down.

Arrange an authorized outage drill with the operator.
Record the last ping, missed deadline, notification receipt and recovery ping times.
Keep the URL and recipient addresses out of evidence.
After recovery, check `/v1/health` and retry the failed cycle from `/ops`.

The [outside cadence watcher](runners_held.md#outside-watcher) checks GitHub's clock even when
the platform database is unavailable. Follow its owner setup steps before relying on notifications.


## Alert email

Set `RESEND_API_KEY` or `SMTP_URL`, plus `MDP_EMAIL_FROM` and `MDP_EMAIL_TO`, for control-api.
Use the secret map and an authorized `ops/deploy.sh --app mdp-control-api` deployment to apply them.
Until all settings exist, no delivery is attempted.
Each selected open alert gets one `email.skipped` audit row with setup guidance.
Worker restarts and repeated checks keep that same state. Existing audit rows stay unchanged.
Ops shows the setup link beside alert email counts. Open `/ops` to review alerts while email is off.

Once configured, the next worker check sends open, unacknowledged alerts that are still eligible.
A skipped row does not block delivery. Resolved and acknowledged alerts are not sent.
A delivery failure retries after 30 seconds, doubling up to one hour.
The tenth failure records `email.gave_up` and ends retries for that alert.
Check `/audit` for `email.sent`, `email.failed` or `email.gave_up`, then follow the original alert's runbook.
