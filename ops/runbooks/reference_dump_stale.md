# reference_dump_stale


## Symptom

No newer reference dump arrived within the source's expected cadence. MusicBrainz publishes a
CC0 full export twice a week (`data/fullexport/LATEST`), and the mirror imports the newest one
monthly, so the serving generation is normally under five weeks old. `refresh.py` also prints
`reference_dump_stale` when upstream `LATEST` itself is more than eight days old.

What keeps serving: the last applied state. The serving generation and every landed generation
stay in use; a stale dump never removes data.


## First query

```sql
SELECT a.id, a.opened_at, a.subject_type, a.subject_id,
       s.imported_generation, s.export_date, s.landed_generation,
       s.import_generation, s.import_state, s.import_phase, s.import_finished_at, s.import_message
FROM control.alert a
LEFT JOIN control.reference_source s ON s.source = 'musicbrainz'
WHERE a.class = 'reference_dump_stale' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```

Run that on the control database, then compare with upstream and the mirror:

```sh
curl -fsS https://data.metabrainz.org/pub/musicbrainz/data/fullexport/LATEST
python3 ops/fly/mb-import/refresh.py --status
```


## Operator action

1. Upstream `LATEST` is newer than the serving generation: the monthly refresh was missed or
   failed. If the newest `import_run` failed, follow
   the `reference_import_failed` runbook. Otherwise run
   `python3 ops/fly/mb-import/refresh.py` now and check the schedule that runs it monthly.
2. Upstream `LATEST` is old too: MetaBrainz has not published. Keep polling with backoff; rerun
   `refresh.py` daily until a newer export appears. It exits 0 with "Nothing to do" meanwhile.
3. Compare export stamps (`imported_generation`, `LATEST`), never download or import times.
   Never rewrite a source date to make a dump look current.


## When to escalate

Upstream has published nothing for two weeks, a scheduled refresh was missed twice, or a newer
export exists and `refresh.py` refuses to start for a reason that is not covered by
the `reference_import_failed` runbook or the `reference_disk_high` runbook.
