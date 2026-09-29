# invoke_timeout


## Symptom

The synchronous invocation deadline expired.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'invoke_timeout' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Open the run and inspect its attempt deadline. Retry only after the active attempt stops. Use `/ops` and follow the function or run trace link.


## When to escalate

The batch cannot finish within its bounded deadline. Preserve run and dump identifiers with the trace.
