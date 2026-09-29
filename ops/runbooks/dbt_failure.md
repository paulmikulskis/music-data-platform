# dbt_failure


## Symptom

A dbt Cloud webhook reports a failed job or node.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'dbt_failure' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Open the run events and trigger a full job after correcting the model. Use `/ops` and follow the function or run trace link.


## When to escalate

The failure requires a model or contract PR. Preserve run and dump identifiers with the trace.
