# reference_disk_high


## Symptom

The MusicBrainz mirror's databases use 80% or more of its volume: the sum of
`pg_database_size()` over the non-template databases on `mdp-mb-db` against
`mdp_meta.volume.total_bytes` (the capacity PostgreSQL can use, measured with `df`). A refresh
also refuses to start with this class, and exit code 3, when a second generation would take use
to 80% (the serving database plus 10%), because a refresh briefly holds two generations.

What keeps serving: the last applied state. `musicbrainz_db` and every landed generation stay as
they are; nothing is dropped. Imports pause until use falls below 80%.


## First query

```sql
SELECT a.id, a.opened_at, a.subject_type, a.subject_id,
       s.disk_used_bytes, s.disk_total_bytes,
       round(100.0 * s.disk_used_bytes / nullif(s.disk_total_bytes, 0), 1) AS pct_used, s.probed_at
FROM control.alert a
LEFT JOIN control.reference_source s ON s.source = 'musicbrainz'
WHERE a.class = 'reference_disk_high' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```

Run that on the control database. Then read the mirror itself, from the repository root:

```sh
python3 ops/fly/mb-import/refresh.py --status
```

It lists every database on the mirror server, their total bytes, the volume capacity and the
newest `import_run`.


## Operator action

1. If `--status` lists `musicbrainz_next` or `musicbrainz_prev`, a failed or interrupted refresh
   left a whole generation behind. Read its run first (see
   the `reference_import_failed` runbook), then clear it with
   `python3 ops/fly/mb-import/refresh.py --discard-prior`. That usually halves use.
2. If only `musicbrainz_db` and `mdp_meta` remain, expand the mirror volume through the guarded
   wrapper, then confirm the filesystem grew:
   ```sh
   python3 ops/fly/mb-db/fly.py volumes extend "$MDP_MB_DB_VOLUME" --size <GB> --app mdp-mb-db --org "$FLY_ORG"
   python3 ops/fly/mb-db/fly.py ssh console --app mdp-mb-db --org "$FLY_ORG" --machine "$MDP_MB_DB_MACHINE" -C 'df -h /var/lib/postgresql'
   ```
3. Run `python3 ops/fly/mb-import/refresh.py`. Every run records the new capacity in
   `mdp_meta.volume` before it decides anything, and starts the refresh if one is due.

Never delete PostgreSQL relation files or WAL by hand, and never drop `musicbrainz_db`.


## When to escalate

Use reaches 90%, PostgreSQL reports no space left, or growth would fill the volume before the next
monthly refresh. Contact the data engineer with the `--status` output.
