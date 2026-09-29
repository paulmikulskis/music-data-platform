# spine_narrowing_due


## Symptom

A weekly `mb_spine` run measured the closure trigger of design and found it crossed: one
generation's projected write (a raw copy and a reference copy, each the size of the newest landed
generation) passed 30% of the pgdata volume, or the tracked recordings in the newest generation passed
100,000. The landing goes on; the 60% ceiling (`warehouse_disk_high`) still stops a landing that would
pass it. The Reference page shows both figures on the MusicBrainz card.

The closure follows every release of a tracked recording, about 115 rows per recording on the live
seed, so its size grows with the tracked catalog. Past this line the next generations approach the
ceiling.


## First query

```sql
SELECT a.opened_at, a.run_id, e.attrs->>'projected_write_share' AS share,
       e.attrs->>'tracked_recordings' AS recordings, e.attrs->>'generation_bytes' AS generation_bytes,
       e.attrs->>'projected_bytes' AS projected, e.attrs->>'limit_bytes' AS limit_bytes
FROM control.alert a
JOIN control.run_event e ON e.run_id = a.run_id AND e.event_type = 'mb_spine_prepared'
WHERE a.class = 'spine_narrowing_due' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 5;
```

Run it on the control database. The newest generation's rows per table sit in
`control.reference_source.landed_counts`.


## Operator action

1. Ship the narrowed release step before the next monthly import: the closure keeps only the releases
   a tracked track, album or ISRC names, not every release of each recording.
2. Once a generation lands under the narrowed step and both figures read below their lines, resolve
   the alert.


## When to escalate

A `warehouse_disk_high` alert opens, or the share passes 45% before the narrowed step ships. Contact the
data engineer with the first query's output.
