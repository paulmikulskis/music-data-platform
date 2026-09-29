# warehouse_unavailable


## Symptom

A close request returns HTTP 503 with `error_class=warehouse_unavailable`, or a
built-in `cycle_close` batch fails with that class. Receipt reads and warehouse
mirror publication use this class for PostgreSQL, pool, OS and DuckDB failures.
The service maps this class to the `warehouse-unavailable` runbook slug.


## First query

Query the control database with the affected cycle identifier:

```sql
SELECT c.id, c.status AS cycle_status, r.id AS run_id, r.status AS run_status,
       r.warehouse_id, r.error_class, r.error_message
FROM control.cycle c
LEFT JOIN control.run r ON r.cycle_id = c.id AND r.kind = 'close'
WHERE c.id = '<cycle-id>'::uuid;
```

Then compare the scope's close watermark with the mirror:

```sql
SELECT scope, last_close_no, mirrored_close_no FROM control.scope_close
WHERE scope = (SELECT scope FROM control.cycle WHERE id = '<cycle-id>'::uuid);
```

Check authenticated `GET /v1/health/detail` and the configured warehouse's
connectivity, permissions, pool capacity and storage. Do not print connection
secrets. A close reads only the control database, so a warehouse outage never
rolls it back: the cycle stays `closed` with its `close_no`, and
`mirrored_close_no` stays below it until the mirror catch-up writes the scope's
stamps and closed `raw.cycles` rows in one warehouse transaction. Until then the
close is not terminal, later closes in the scope wait behind it, and tenant cycles
keep reading the previous global close. A continuing receipt outage also prevents
settlement and polling; once receipts are readable, the failed close batch settles
the run as partial with its saved warehouse error. Preserve committed dumps and
receipt records.


## The button

After restoring warehouse health, retry `POST /v1/cycles/{cycle_id}/close` using
the same identifier, or wait for the recovery loop: both run the catch-up, which
mirrors every close through `last_close_no` and then advances `mirrored_close_no`.
An already closed cycle keeps its `close_no` and stamps. For an unsuccessful built-in run, repeat the
`cycle_close` invocation with its original validated dbt binding to request a
new attempt. There is no separate operator UI button yet; do not delete or reload
committed dumps to resolve a close failure.


## When to escalate

Escalate if health remains unavailable after restoring connectivity/capacity,
the pinned warehouse is misconfigured, or repeated close retries cannot publish
the mirror. Include the cycle/run identifiers, failing operation, and health
results without credential values.
