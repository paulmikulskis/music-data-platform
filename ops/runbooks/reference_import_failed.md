# reference_import_failed


## Symptom

A MusicBrainz import did not produce a validated generation: the importer exited nonzero,
`verify.sql` rejected the new database, the importer machine stopped without recording an
outcome, the run exceeded its time limit, or the promotion did not commit. The newest
`mdp_meta.import_run` row has `state='failed'` and a message that starts with
`reference_import_failed:` and names the phase. `refresh.py` exits 1.

What keeps serving: the last applied state. `musicbrainz_db` keeps the last validated generation;
promotion refuses any run that is not `validated` or a database whose `mdp.generation` lacks
`validated_at`, and an interrupted swap rolls back and reopens `musicbrainz_db`.


## First query

```sql
SELECT a.id, a.opened_at, a.subject_type, a.subject_id,
       s.import_generation, s.import_state, s.import_phase, s.import_started_at,
       s.import_finished_at, s.import_message, s.imported_generation
FROM control.alert a
LEFT JOIN control.reference_source s ON s.source = 'musicbrainz'
WHERE a.class = 'reference_import_failed' AND a.resolved_at IS NULL
ORDER BY a.opened_at DESC LIMIT 20;
```

Run that on the control database, then read the mirror and the importer from the repository root:

```sh
python3 ops/fly/mb-import/refresh.py --status
python3 ops/fly/mb-db/fly.py machine list --app mdp-mb-import --org "$FLY_ORG"
python3 ops/fly/mb-db/fly.py logs --app mdp-mb-import --org "$FLY_ORG" --no-tail
```


## Operator action

1. Read the phase in the message. `preflight` means the target database already existed or
   `MAINTENANCE` did not resolve to a new database; `fetch` is a download or checksum error;
   `import` is upstream `InitDb.pl`; `finalize` is grants or the generation row; `verify` names
   the failed check in `mdp-import.log`; a timeout or promotion failure says so.
2. The failed run's machine (`mdp-refresh-<run>`) and its temporary `mbrefresh` volume stay for
   inspection. The volume holds `mdp-import.log`, `mdp-verification.txt` and
   `finalize.private.log`; the private log can carry the reader password on an SQL error, so
   redact it before sharing. To read files from a stopped importer, mount its volume on a
   maintenance machine with an explicit sleep command; never restart the import entrypoint.
3. A schema change upstream shows as a `replication_control` or schema-sequence error: move the
   image tag in `ops/fly/mb-import/Dockerfile` to the upstream release for that schema (verify
   follows the image's `MUSICBRAINZ_DB_SCHEMA_SEQUENCE`), and update `ops/fly/mb-db/fly.toml`
   to the matching database image in a maintenance window if upstream requires it.
4. Retry in a clean temporary volume, after the cause is fixed:
   ```sh
   python3 ops/fly/mb-import/refresh.py --discard-prior
   python3 ops/fly/mb-import/refresh.py --force
   ```
   `--discard-prior` fails an abandoned run, drops `musicbrainz_next` and `musicbrainz_prev`,
   reopens `musicbrainz_db` if a swap was interrupted, and destroys refresh machines and
   `mbrefresh` volumes. It refuses while an importer is still running. Nothing retries
   automatically.
5. A run left `validated` because `refresh.py` itself stopped (the importer finished) is promoted
   with `python3 ops/fly/mb-import/refresh.py --resume`.

It clears when a later import validates and its landed generation reconciles.


## When to escalate

The same export fails twice, `verify.sql` finds empty identity tables or counts that do not
reconcile, `musicbrainz_db` does not accept connections after `--discard-prior`, or a promotion
reports that it did not commit. Include the run id, export stamp, phase, machine and volume ids,
and the sanitized error.
