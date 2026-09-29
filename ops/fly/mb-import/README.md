# Import and monthly refresh

The mirror loads the newest MusicBrainz CC0 full export into a database of its own, validates it,
and only then makes it the serving `musicbrainz_db`. Nothing replicates between
imports: each month's generation is a fresh load.


## The importer (`import.sh` in the image)

The image is the pinned upstream `musicbrainz-docker-musicbrainz` with three patches:
`fetch-dump.sh` and `createdb.sh` fetch only mbdump, derived, cdstubs, cover-art-archive and stats
(no edit/editor bundle; upstream verifies MD5 checksums); upstream cleanup keeps `mdp-*` files;
and `DBDefs.pm` reads the database name from `MDP_TARGET_DB` instead of its hard-coded
`musicbrainz_db`, so `InitDb.pl` creates and loads the target database. The main identity dump is
CC0; keep the archive LICENSE files for supplementary bundles rather than assuming every field is.

| `MDP_IMPORT_MODE` | Target | Ends as |
|---|---|---|
| `initial` | `musicbrainz_db` on an empty server | `promoted` (it serves once validated) |
| `refresh` | `musicbrainz_next` beside the serving database | `validated`, waiting for promotion |

In order it: refuses a volume with a prior attempt marker; creates `mdp_meta` if needed; opens or
continues the `import_run` row (`MDP_IMPORT_RUN_ID` from refresh.py, otherwise its own row from
upstream `LATEST`); refuses when the target database exists or upstream `database_exists
MAINTENANCE` finds one; fetches (`fetch`), runs `createdb.sh` without `-fetch` (`import`), runs
`finalize.sql` (`finalize`: reader grants, `../mb-db/mdp-schema.sql`, the `mdp.generation` row with
the export stamp, sequences and ten exact counts) into a private log, runs `verify.sql` (`verify`:
nonzero and reconciled counts, one `replication_control` row at the image's schema sequence, one
generation row, valid indexes, reader privileges read-only), checks the reader login, and only
then sets `validated_at` and the run state. Any failure records `state='failed'` with
`reference_import_failed: <phase> exited <code>` and exits nonzero. Nothing retries itself.


## refresh.py

`refresh.py` is the only way importer machines are created. From the repository root, with Fly
credentials in `~/.fly/config.yml`:

```sh
python3 ops/fly/mb-import/refresh.py --dry-run          # the plan, with the exact Fly commands
python3 ops/fly/mb-import/refresh.py --status           # databases, bytes, serving generation, newest run
secret_store run -- \
  uv run --project functions python ops/fly/mb-import/refresh.py --if-requested
```

A run reads upstream `LATEST` and the serving generation, and exits 0 when upstream is not newer,
unless `--force` or (`--if-requested`) a pending `control.reference_source.reimport_requested_at`
asks for a re-import. It records the volume capacity, then refuses with `reference_disk_high`
(exit 3) when databases use 80% of it, or would with a second generation sized as the serving one
plus 10% (59 GB against about 233 GiB usable today projects to about 50%). It then inserts the
`import_run` row, creates a temporary `mbrefresh` volume (60 GB), runs one `performance-4x` /
16 GB machine `mdp-refresh-<run>` built from this Dockerfile with `ops/fly` as the build context
and the `[env]`/`[[vm]]` of `fly.toml`, polls every 5 minutes for up to 24 hours, promotes a
validated run with `promote.sql`, clears the served request, and destroys the machine and volume.

`promote.sql` checks that the run is `validated` for `musicbrainz_next` and that database holds
that validated generation, refuses new connections to `musicbrainz_db`, and in one transaction
terminates open reader sessions and renames `musicbrainz_db` to `musicbrainz_prev` and
`musicbrainz_next` to `musicbrainz_db`. If that transaction fails, it reopens `musicbrainz_db`
unchanged. `musicbrainz_prev` is dropped only after the swap commits. `mb_spine` sessions cut by
the swap retry and then land the new generation.

On failure the machine and volume stay for inspection and the next run refuses until an operator
runs `--discard-prior` (see `ops/runbooks/reference_import_failed.md`). `--resume` promotes a
run left `validated` when `refresh.py` itself stopped. `--initial` loads an empty server.
Exit codes: 0 done or nothing to do, 1 refused or failed, 2 promoted with the temporary machine
or volume left behind, 3 `reference_disk_high`.


### Schedule

Monthly, on the first Thursday at 12:00 UTC (after the Wednesday export), an operator or a
scheduler that holds the Fly API token and `MDP_CONTROL_RT_URL` runs the `--if-requested` command
above in a tmux session; it waits for the import, so the session must outlive it. It is not a Fly
scheduled machine: creating and destroying volumes and machines needs the Fly API, and no MDP
machine holds that token. A Reference-page re-import request waits for the next run, or an
operator runs the same command sooner. If `refresh.py` stops while an import runs, the import
continues; the next run reports the run and `--resume` finishes it.


## Tests

`test_refresh.py` runs `import.sh` for real with stub `fetch-dump.sh`, `createdb.sh`, `carton`
and `wget` that load `functions/tests/fixtures/mb_mirror.sql`, plus `refresh.py` with a stand-in
for Fly, against a disposable PostgreSQL (127.0.0.1:5444 by default; it creates only `mbt_*`
databases):

```sh
uv run --project functions pytest ops/fly/mb-import/test_refresh.py
```
