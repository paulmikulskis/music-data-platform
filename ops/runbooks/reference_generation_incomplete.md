# reference_generation_incomplete


## Symptom

A MusicBrainz generation landed in the warehouse but does not reconcile: the `mb_spine` run's
`raw._run_completion` row is missing, a dump it references is not in the consuming cycle's
manifest, or the landed row counts differ from the counts the mirror recorded in
`mdp.generation.counts`.

It also opens on a dbt run (`subject_type = 'dbt_run'`) whose build failed with
`reference_generation_incomplete: this cycle's manifest reads MusicBrainz generation ...`: the cycle it
built (usually a Replay of an older cycle) reads one of the newest two generations its manifest
reconciles, and `mb_spine`'s retention has deleted that generation's `raw.mb_*` rows. The build stops
rather than resolve identity on the generations that remain.

What keeps serving: reference staging keeps the last complete applied generation. The switch to
the new generation and every absence-based tombstone wait until it reconciles, so no key is
removed on the strength of a partial landing.


## First query

```sql
SELECT a.id, a.run_id, a.opened_at, r.status, r.error_message,
       s.imported_generation, s.landed_generation, s.landed_at, s.landed_reconciled
FROM control.alert a
LEFT JOIN control.run r ON r.id = a.run_id
LEFT JOIN control.reference_source s ON s.source = 'musicbrainz'
WHERE a.class = 'reference_generation_incomplete' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;

-- Per table: what the mirror recorded against what landed.
SELECT m.key AS mb_table, m.value::bigint AS mirror, (s.landed_counts ->> m.key)::bigint AS landed
FROM control.reference_source s, jsonb_each_text(s.mirror_counts) m
WHERE s.source = 'musicbrainz'
ORDER BY (s.landed_counts ->> m.key)::bigint IS DISTINCT FROM m.value::bigint DESC, m.key;
```


## Operator action

1. Open the alert's `mb_spine` run in `/ops` and follow its trace. A run without its completion
   row is unfinished; a completion row whose dumps are missing from the manifest is waiting on a
   later close.
2. Resume the missing landings: rerun or resume that `mb_spine` run for the same generation. Its
   completed outputs are reused and only the missing ones land, then the runtime writes the
   completion row last.
3. Leave the switch and the tombstones to reconciliation. Never mark a generation reconciled by
   hand, and never delete landed rows to make counts match.
4. If `imported_generation` moved on (a refresh promoted a newer generation before this one
   landed), the next weekly `mb_spine` lands the newer generation; the incomplete one never
   becomes current.

For a `dbt_run` alert: the generation's dumps stay in the object store. Re-land each of that
generation's `raw.mb_*` dumps (the run's `raw._run_completion` row lists them) with
`POST /v1/repair` and its `dump_id`, then rerun the Replay. Retention deletes it again at the next
weekly `mb_spine` run, so replay before then, and resolve the alert once the Replay passes.

The `reference_source` alert clears when the completion row and every required dump are
manifest-visible and the counts reconcile.


## When to escalate

Counts still differ after a complete resume (the landing is wrong, not late), the same table
fails to land twice, or reference staging falls back to a generation older than the last complete one.
Include the run id, both generations and the per-table comparison.
