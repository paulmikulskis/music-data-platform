# accounting_mismatch


## Symptom

Observed records do not equal yielded plus rejected records.


## First query

```sql
SELECT a.id, a.run_id, a.subject_type, a.subject_id, a.opened_at, r.status, r.error_message
FROM control.alert a LEFT JOIN control.run r ON r.id = a.run_id
WHERE a.class = 'accounting_mismatch' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```


## The button

Inspect the receipt counts and rejected sample. Pause the function. Use `/ops` and follow the function or run trace link.


## When to escalate

Always escalate for a reviewed function fix before retrying. Preserve run and dump identifiers with the trace.
