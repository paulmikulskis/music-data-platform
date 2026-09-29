# dbt_api_unavailable


## Symptom

The dbt Admin API token or account is missing, or the API is unreachable.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'dbt_api_unavailable' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Keep Core selected while configuring the Cloud integration; retry Trigger job after recovery. Use `/ops` and follow the function or run trace link.


## When to escalate

A configured token lacks the required job permissions. Preserve run and dump identifiers with the trace.
