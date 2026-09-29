# warehouse_disk_high


## Symptom

A weekly `mb_spine` run stopped before it read the mirror: landing one more MusicBrainz generation
would take the warehouse server past its ceiling, 60% of the pgdata volume
(`MDP_PGDATA_VOLUME_BYTES`, `MDP_PGDATA_CEILING`). The projection is the bytes every database uses
plus two copies of one generation (raw and the reference models), each the size of the newest landed
generation, or the tracked seeds times `BYTES_PER_SEED` before the first. The run ends `succeeded`
with nothing landed and this critical alert on it.

What keeps serving: reference staging keeps the newest reconciled generation, and `mb_resolve` keeps
answering priority tracks from the mirror and landing the rows it touches. New tracked ids wait for a
generation.


## First query

```sql
SELECT a.opened_at, a.run_id, e.attrs->>'used_bytes' AS used, e.attrs->>'generation_bytes' AS generation,
       e.attrs->>'projected_bytes' AS projected, e.attrs->>'limit_bytes' AS limit_bytes,
       e.attrs->'retained' AS retained, e.attrs->>'seeds' AS seeds
FROM control.alert a
JOIN control.run_event e ON e.run_id = a.run_id AND e.event_type = 'mb_spine_prepared'
WHERE a.class = 'warehouse_disk_high' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 5;
```

Run that on the control database. Then, on the warehouse, find what holds the space:

```sql
SELECT n.nspname, c.relname, pg_size_pretty(pg_total_relation_size(c.oid)) AS size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'm', 'p') ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 20;
```


## Operator action

1. Check `retained` in the first query. Retention runs in the same step and keeps the newest two
   reconciled generations of every `raw.mb_*` table. A generation that never reconciles stays until
   two newer ones do; see the `reference_generation_incomplete` runbook to finish or rerun it.
2. If the spine is not what fills the volume, the largest relations above name the owner. Do not
   delete raw rows by hand.
3. If the warehouse needs room, extend the `mdp-postgres` volume through the guarded wrapper
   (`ops/fly/fly.sh volumes extend <volume id> --size <GB> --app mdp-postgres --org "$FLY_ORG"`),
   then set `MDP_PGDATA_VOLUME_BYTES` on `mdp-functions` to the new size.
4. Rerun the weekly job. The alert stays for the record; acknowledge it once a run lands.


## When to escalate

Use passes 80% of the volume, or two weekly runs in a row stop here. Contact the data engineer with
the first query's output.
