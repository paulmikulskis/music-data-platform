# Private MusicBrainz mirror

One `shared-cpu-4x` / 8 GB machine in `your configured Fly organization and region`, 250 GB encrypted data volume
mounted at `/var/lib/postgresql`. PostgreSQL 18 data lives in `18/docker`. No services,
public IPs, autosuspend or HA spare. `shared_buffers=2048MB` matches upstream compose;
remaining tuning is bounded for the 8 GB machine. Fly's private network encrypts traffic;
the supplied private `.internal` URLs use sslmode=disable (Postgres TLS is not configured).


## Databases

Each MusicBrainz generation lives in its own database, loaded from one CC0 full export.

| Database | Holds |
|---|---|
| `musicbrainz_db` | the serving generation; the reader connection points here, so readers never change URL |
| `musicbrainz_next` | a refresh's new generation while it imports and validates |
| `musicbrainz_prev` | the old generation for the moment between the swap and its drop |
| `mdp_meta` | `import_run` (one row per attempt: running, validated, promoted or failed) and `volume` (usable capacity from `df`) |

Every generation database carries the `mdp` schema from `mdp-schema.sql`: `mdp.generation` (the
export stamp, export date, replication and schema sequence, exact counts of ten identity tables,
`validated_at`), `mdp.url_tail_id()` with its index (the url table is analyzed after it, so the
planner has its statistics), `mdp.tracked_url` (the URLs of tracked platform domains and of the
Wikidata, Discogs, Instagram, TikTok and YouTube hosts, matched once per generation so the spine
queries never rescan all URLs; a changed pattern takes effect at the next import), and the `pg_trgm` GIN index on
`artist_credit.name`. The first import also built one on `recording.name`; no query reads it, later
imports do not build it, and dropping it from `musicbrainz_db` is an operator step. `mb_reader` has
SELECT on `musicbrainz`, `mdp.generation` and `mdp.tracked_url`, CONNECT and SELECT on `mdp_meta`,
and `pg_read_all_stats` for `pg_database_size()`; its transactions default to read-only and it cannot write anywhere.

The refresh path (import, validate, promote) is in [../mb-import/README.md](../mb-import/README.md).


## Handoff of the first import

The first import loaded export `20260923-002121` into `musicbrainz_db` before generations
existed. Run once, from the repository root:

```sh
python3 ops/fly/mb-db/apply-mdp-schema.py --dry-run
python3 ops/fly/mb-db/apply-mdp-schema.py
```

It creates `mdp_meta`, records the volume capacity, runs `finalize.sql` (reader grants,
`mdp-schema.sql`, the generation row), `verify.sql`, sets `validated_at`, and records the
serving generation as a promoted `import_run`. It never renames or reloads a database and is
safe to repeat, though each run rebuilds `mdp.tracked_url` and re-validates the generation row. The trigram index build takes a while on the full mirror; readers keep working
because `CREATE INDEX` blocks only writes, and the mirror has none.

The first import's `musicbrainz_db` was analyzed before its tail index existed. Give it the
tail-index statistics once, after the mdp-functions deploy:

```sh
python3 ops/fly/mb-db/apply-mdp-schema.py --analyze-url
```

It runs only `ANALYZE musicbrainz.url` as `musicbrainz`, which blocks no reader; the full apply is
not needed for it. Imports get the statistics from `mdp-schema.sql`.


## Access

Credentials: Fly holds the database administrator and reader passwords. The production
secret store holds their connection URLs. Passwords enter commands
through stdin or the environment, never as command arguments or output.

`python3 ops/fly/mb-db/fly.py <command> --org "$FLY_ORG"` from repo root is the only Fly path.
It reads the local Fly CLI login from `~/.fly/config.yml`, invokes
`~/.fly/bin/flyctl`, verifies app ownership, and accepts only `mdp-mb-db` and
`mdp-mb-import`. Unsupported app-scoped --org flags are removed only after ownership checking.
`mirror.py` runs psql scripts on the database machine over `fly.py ssh console`, as `musicbrainz`
on the local socket, with the script on stdin (the machine override selects a different database machine).

Deploy: `python3 ops/fly/mb-db/fly.py deploy ops/fly/mb-db --app mdp-mb-db --org "$FLY_ORG" --ha=false --no-public-ips --yes`.
Do not redeploy or resize during an import. Preserve the data volume and its scheduled snapshots.
