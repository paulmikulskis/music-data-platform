# surface_drift


## Symptom

A collector misses a required response envelope on three distinct targets in a run, or a gold
function misses on three consecutive inputs not rejected in the recent window. The critical
alert pauses the streamline; gold stops reading and leaves the remaining inputs unrejected.


## First query

```sql
SELECT e.run_id, e.at, e.event_type, e.attrs
FROM control.run_event e
JOIN control.alert a ON a.run_id = e.run_id
WHERE a.class = 'surface_drift' AND a.resolved_at IS NULL
  AND e.event_type IN ('envelope_mismatch', 'inputs_rejected')
ORDER BY e.at DESC LIMIT 30;
```


## The button

Open the alert's run trace from `/ops`. Compare the recorded surface/path with the parser and
reconnaissance fixtures. Repair and test the parser against the permitted public surface.
Enable the paused streamline only after that check, then request a fresh attempt. An isolated
gold input miss belongs to that input; see [inputs_parked](inputs_parked.md).


## When to escalate

The public surface changes its access requirements, or the repaired parser still cannot account
for observed records. Give the source maintainer the run id, surface, path and sanitized fixture;
keep the streamline paused.
