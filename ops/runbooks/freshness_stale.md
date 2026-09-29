# freshness_stale


## Symptom

A source is older than its cadence freshness threshold.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'freshness_stale' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Use Run now on the upstream function, then trigger the full dbt job. Use `/ops` and follow the function or run trace link.


## When to escalate

Source delivery remains absent after a successful invocation. Preserve run and dump identifiers with the trace.
