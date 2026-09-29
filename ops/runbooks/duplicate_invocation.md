# duplicate_invocation


## Symptom

The service returns an existing run for the same work identity.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'duplicate_invocation' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Open the existing run and receipts. Reuse the manual key to poll safely. Use `/ops` and follow the function or run trace link.


## When to escalate

The returned identity differs from the intended source or scope. Preserve run and dump identifiers with the trace.
