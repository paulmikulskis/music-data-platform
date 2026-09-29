# vendor_retryable


## Symptom

Provider retries are exhausted after throttling, server errors or timeouts.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'vendor_retryable' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Open the run trace and retry Run now after the provider recovers. Use `/ops` and follow the function or run trace link.


## When to escalate

Failures persist across backoff windows. Preserve run and dump identifiers with the trace.
