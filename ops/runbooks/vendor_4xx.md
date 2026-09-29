# vendor_4xx


## Symptom

A provider refuses one target. Its batch may be partial.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'vendor_4xx' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Review stale target, correct its identity, then Run now. Use `/ops` and follow the function or run trace link.


## When to escalate

The identity is verified and the provider still refuses it. Preserve run and dump identifiers with the trace.
