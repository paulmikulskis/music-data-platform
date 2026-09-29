# control_db_unavailable


## Symptom

The API returns HTTP 503 with `error_class=control_db_unavailable` when a control
query or pool acquisition fails. A control failure must retain this attribution
even during a cycle close. The runbook slug is `control-db-unavailable`.


## First query

Using the configured control connection and the affected cycle identifier:

```sql
SELECT id, status, cadence, scope, closed_at
FROM control.cycle
WHERE id = '<cycle-id>'::uuid;
```

If the query cannot connect, inspect service health, database availability and
pool capacity before investigating cycle state. Keep connection secrets out of
logs and evidence.


## The button

Restore database connectivity, then retry polling or the original close request
with the same identifier. There is no separate operator UI button yet. Do not
admit new work or reconstruct missing control records while the database is
unavailable; committed dumps remain available for recovery.


## When to escalate

Escalate if connectivity or pool capacity cannot be restored, the control schema
or grants have drifted, or the acknowledged cycle cannot be found after recovery.
Provide the operation, cycle identifier and health status without secrets.
