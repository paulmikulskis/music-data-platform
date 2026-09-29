# dump_late


## Symptom

A dump was registered after its publication lease expired.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'dump_late' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Open its run and trace; inspect quarantine before Run now. Use `/ops` and follow the function or run trace link.


## When to escalate

A valid paid-for dump cannot be safely recovered. Preserve run and dump identifiers with the trace.
