# cycle_not_found


## Symptom

Closing a cycle returns HTTP 404 with `error_class=cycle_not_found` and
`message=Unknown cycle`. The supplied identifier is absent from the control database.
The service maps this class to the `cycle-not-found` runbook slug.


## First query

Use the control database's configured connection and substitute the requested UUID:

```sql
SELECT c.id, c.status, c.cadence, c.scope, a.dbt_run_id, a.runner, a.job_id
FROM control.cycle c
LEFT JOIN control.cycle_attempt a ON a.cycle_id = c.id
WHERE c.id = '<cycle-id>'::uuid;
```

Check that the request and database refer to the same environment. Recover the
cycle identifier from the original successful `POST /v1/bind_cycle` response or
its `control.cycle_attempt` row; do not create a substitute cycle just to close it.


## The button

Retry close using the verified identifier: `POST /v1/cycles/{cycle_id}/close`.
There is no separate operator UI button yet. If no binding exists, bind the
authorized dbt run through `POST /v1/bind_cycle` before invoking `cycle_close`.


## When to escalate

Escalate to the platform operator if a previously acknowledged cycle is absent
from the correct database, or the binding points to a missing cycle. Preserve
the request identifier and binding response; do not repair control rows manually.
