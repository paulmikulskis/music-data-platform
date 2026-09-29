# dump_unreadable


## Symptom

The loader cannot decode a registered dump.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'dump_unreadable' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Inspect its manifest and storage, then Repair landing with the original dump ID. Use `/ops` and follow the function or run trace link.


## When to escalate

The original bytes are absent or corrupt. Preserve run and dump identifiers with the trace.
