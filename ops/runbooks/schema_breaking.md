# schema_breaking


## Symptom

A dump cannot land because its schema is incompatible.
The load stops and releases its claim. Retrying the same schema cannot fix it.
For example, PostgreSQL refuses to change a column's type when a view depends on it.
This refusal has SQLSTATE `0A000`. A declared text version column must publish as text.
Compare the source declaration with the warehouse column before using Repair landing.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'schema_breaking' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Inspect the fingerprint and rejected sample. Pause the function; Repair landing after a reviewed fix. Use `/ops` and follow the function or run trace link.


## When to escalate

A migration or contract change is necessary. Preserve run and dump identifiers with the trace.
