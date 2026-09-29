# cycle_not_closed


## Symptom

A dbt model after `bronze_close`, a manifest filter, or a workbench preview fails
compilation with `cycle_not_closed: cycle <id> has no close_no`. The model read a
`stamp`-mode cycle that has no close number in `raw.cycles`, so any filter it
compiled would admit no rows. Phase-1 export, invoke and close models never raise
it. The service maps this class to the `cycle-not-closed` runbook slug.


## First query

Query the control database with the cycle identifier from the message:

```sql
SELECT c.id, c.status, c.manifest_mode, c.close_no, s.last_close_no, s.mirrored_close_no
FROM control.cycle c
LEFT JOIN control.scope_close s ON s.scope = c.scope
WHERE c.id = '<cycle-id>'::uuid;
```

A null `close_no` in control means `bronze_close` never ran for this cycle: the
job ran transform models alone, or phase 1 failed before its close model. A
`close_no` above `mirrored_close_no` means the close committed but the mirror
catch-up has not written its `raw.cycles` row and stamps yet; the close model then
answered `warehouse_unavailable` and did not return.


## The button

Rerun the full cadence job with its original binding. Its close model returns the
existing close stamp once the catch-up has mirrored it. If the warehouse is down,
follow [warehouse_unavailable](warehouse_unavailable.md) first; the recovery loop
also runs the catch-up. Never run transform selectors without phase 1.


## When to escalate

Escalate to the platform operator if `mirrored_close_no` stays below `close_no`
with a healthy warehouse, or a cycle with a `close_no` in control shows null in
`raw.cycles` after a catch-up. Include the cycle identifier and both queries' rows.
