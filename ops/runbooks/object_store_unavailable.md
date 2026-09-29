# object_store_unavailable


## Symptom

Published object storage is unreachable; preserve committed dumps.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'object_store_unavailable' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Check the heartbeat and storage configuration; Repair landing after recovery. Use `/ops` and follow the function or run trace link.


## When to escalate

Storage access or durable bytes cannot be restored. Preserve run and dump identifiers with the trace.
