# runner_outdated


## Symptom

A cycle bind omits the close-stamp protocol (`global_inputs`) without `lock_runner`, or a close
uses an attempt whose `stamp_protocol` is false. The service returns 409 and does not close the cycle.
A refused bind may have no run or alert row.


## First query

```sql
SELECT ca.dbt_run_id, ca.stamp_protocol, c.id AS cycle_id,
       c.cadence, c.scope, c.status, c.opened_by_dbt_run_id
FROM control.cycle_attempt ca JOIN control.cycle c ON c.id = ca.cycle_id
WHERE NOT ca.stamp_protocol
ORDER BY c.opened_at DESC LIMIT 20;
```


## The button

Compare the request error, runner image revision and deployed dbt hooks with the functions release.
The deployment owner deploys the matching runner and control-api before starting runs, following
[the deployment order](../fly/README.md). Rerun the full cadence job on that image.
Do not force a close, set protocol fields by hand, or use `dbt retry`.


## When to escalate

A matching image still sends a pre-stamp bind, or the deployment cannot complete its rebuild.
Give the deployment owner the image revisions, dbt run id and cycle id; leave incompatible
runners stopped.
