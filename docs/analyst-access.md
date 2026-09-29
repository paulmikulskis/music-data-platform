# Analyst access

Use your individual `analyst_<handle>` login to query the global warehouse. The `analyst_ro`
group reads reviewed global marts and generated `explore_staging`, `explore_intermediate` and
`explore_marts` copies. New columns are private until declared.
Start with the contracted marts below. Raw, control, reference, tenant and workbench schemas
are outside this access path. Writes belong to your personal sandbox. The login cannot create
other schemas or execute `mdp.*` functions.
Internal pg_lake metadata is also closed to human roles. Choose a global relation in the catalog.


## Console access

A staff identity opens `/workbench`, `/sandbox` (also `/sandboxes`), `/queries`,
`/explorer`, `/reference`, status and function pages. Other GET pages are read-only.
Run, Preview, Backtest and Save as PR work with staff access.
Preview builds the draft against already-built global inputs. Backtest uses those current inputs for
both cycle contexts; it does not reconstruct past raw data. If a model needs a restricted input,
use the permitted relation named in the error in a SELECT draft, or ask an operator to run the full build.
Warehouse previews use the same global data boundary as `analyst_ro`: marts,
intermediate and staging, including their privacy-safe copies, plus labels and catalog.
Checked-in marts such as `mart_chart_history` read their rights flags through
`catalog.learning_rights`: `source_key`, `learning_eligible` and `resale_permitted`.
Individual analyst logins can read the same view. It follows the current registry and contains
no licence text, prices or contacts. Production builds read `reference.rights_registry`.
Open Workbench and choose Preview to check the unchanged mart.
Raw and tenant rows need admin. Use a global relation from the catalog in Workbench.
Queries shows your own query review records. Sandbox shows only your linked analyst sandbox.

Ask an operator to issue a key:

```sh
pnpm --dir control mdp keys create --role staff --label analyst-console --warehouse-role analyst_<handle>
```

`--warehouse-role` is optional. It links an existing individual analyst login for Sandbox status;
it does not create a login or grant SQL access. Without it, ask the operator to issue a linked key.
The operator sends the key privately. Follow [console connection](operating.md#reach-control-api)
and use this key in the browser's `x-api-key` header.
The operator lists keys with `pnpm --dir control mdp keys list` and revokes one with
`pnpm --dir control mdp keys revoke <id>`.

An existing Clerk session also works with public metadata `mdp_staff: true`.
The operator can add `mdp_warehouse_role: "analyst_<handle>"` for Sandbox status.
`mdp_admin: true` takes precedence. Reader keys cannot open the console.
Operator forms are disabled for staff. Tenant, key, target, budget, knob, recovery,
runner, alert and sandbox operator changes need admin; ask an operator and open the
[access runbook](../ops/runbooks/forbidden.md).


## Keep your analysis

<!-- sandbox-policy:start -->
Each login has 4 connections, a 30s statement timeout, a 60s idle transaction timeout, and a 256MB temporary-file limit.
A sandbox has a 1 GiB creation quota. The checker runs every 60 seconds and alerts at 80 percent.
At the quota it freezes new objects; existing tables can still be read or reduced. Delete unused tables and check status to resume creation.
Archives live under `inputs/sandbox-archives/<schema>/<timestamp>/`. Open the manifest before removing a local archive.
<!-- sandbox-policy:end -->

Your login owns `sandbox_<handle>`. It can create tables and views there. Analyst sandboxes are readable by analysts and explorers. Explorer sandboxes are readable only by explorers. Production readers and dbt cannot. On the local stack:

```sql
CREATE TABLE sandbox_local.chart_copy AS
SELECT * FROM marts.mart_shazam_chart_daily;
CREATE VIEW sandbox_local.chart_draft AS
SELECT * FROM marts.mart_shazam_chart_daily;
```

Keep rights and label columns when copying rows. Pseudonymous keys stay pseudonymous.
Use the catalog's tenant labels before combining inputs. Sandbox work does not start a cycle.
A sandbox shares reads within its access group. Use a separate local file for unfinished private notes.
Persistent views and SQL routines over raw, exploration, staging or intermediate relations are refused because they can block loading.
Save those queries as tables instead. A view over a served mart can still be lifted.
The owner can drop sandbox objects. Account removal refuses a nonempty sandbox and leaves
login disabled until the operator explicitly keeps or removes its contents.


### Work offline

Set `MDP_ANALYST_URL` to your individual connection string. `source ops/local/env.sh` supplies
it locally. For production, use the same Fly proxy and login described above.
These commands preserve every selected relation's columns, including rights and labels:

```sh
pnpm --dir control mdp warehouse snapshot --schemas marts,staging --out ./warehouse.duckdb
pnpm --dir control mdp warehouse snapshot --schemas marts --parquet ./warehouse-parquet
```

Omit `--schemas` to copy every relation your login can read. Existing destinations are refused.
DuckDB holds a `_mdp_snapshot` manifest table. Parquet has `_mdp_snapshot.json` beside its schema
folders. Both record labels, capture time, row counts and each relation's build and close stamp.
The summary flags multiple tenants. Missing scope labels print `cross-tenant=unknown`.
Postgres arrays become JSON. Types without a DuckDB equivalent keep their text representation.
An unbuilt sandbox or unstamped local relation has a null build. Each relation is consistent on
its own; different relations can have different builds. Reads time out after 30 seconds and stop
waiting for a lock after 250 milliseconds. A failed export publishes no partial output.

Open the [Python notebook](starters/notebook.ipynb) to list the catalog, load a snapshot and draw
a chart. The [R guide](r.md) has an R starter.


### Turn a view into a model

```sh
pnpm --dir control mdp new mart mart_chart_draft --from sandbox_local.chart_draft
bash ops/ready.sh
```

The first command writes the enforced YAML contract, then the SQL. Fill its descriptions and
choose `meta.grain` before adding an API route. It copies the view definition and resolves
known inputs to `ref()` or `source()`. It carries source keys and conservative rights flags into
`mdp_annotate()`. Missing input flags stay false. Inline other sandbox views first. Use declared
staging inputs when a masked raw view cannot be lifted safely. Tenant inputs need a tenant model.
The new model never reads your sandbox. Open a PR after the checks pass.


### Run plain SQL in the workbench

Start from a checked-in model, for example `SELECT * FROM marts.mart_chart_history LIMIT 100`.

The `explore_` copy hides private columns and keeps readable fields.
If a query names a restricted table, **Use safe copy and rerun** uses a readable copy when one exists.
Otherwise, open Explorer and choose a readable table.

Run checks your current global analyst permissions. Console staff cannot query raw, tenant or sandbox
relations; open Explorer and choose a permitted global input. Use your individual SQL login to query a sandbox.
The database role is the privacy boundary: a staff session reads what an individual analyst login reads
(the global layers, the generated labels, and analysts' team-shared sandboxes), never raw, control,
tenant schemas or explorer sandboxes. The console's input check keeps drafts on global inputs so they
can become models; it is not the boundary.
Run accepts plain SELECTs with schema names, such as `SELECT * FROM marts.mart_chart_history`.
It uses your session's read role with a timeout and row cap. Labels and cross-tenant warnings
remain visible. Use Preview for dbt SQL and Backtest for two cycles. Save as PR still needs a
successful Preview or Backtest of the exact draft.


### Connect a BI tool

Any tool with a PostgreSQL connector works with an individual analyst or explorer login.
Use the same host, port, database and TLS settings as DBeaver. This includes dashboard tools
and spreadsheet connectors. The login's grants apply in every client. No hosted BI service is required.

### R

From the clone, run `pak::local_install("r/mdpr")`, then `mdpr::mdp_setup("<handle>")`.
Connect with `con <- mdpr::mdp_connect()`.
Read `mdpr::mdp_tbl(con, "mart_chart_history") |> head(10) |> dplyr::collect()`.
The [R guide](r.md) covers IDEs, offline work, rights and model proposals.


## Query everything

Staff use an individual `explorer_<handle>` login for the full warehouse.
Start with the catalog:

```sql
SELECT schema, name, layer, category, tenant, learning, resale, licence_status, readable
FROM catalog.relations ORDER BY schema, name;
SELECT * FROM catalog.denials;
SELECT * FROM explore_raw.playlist_snapshots LIMIT 5;
```

Every relation and column carries labels in its Postgres comment. The same generated labels
appear in the catalog, explorer, workbench and served API response metadata:

| Label | Read it as |
|---|---|
| Layer | Bronze lands and cleans data. Silver joins it. Gold enriches it. |
| Category | Public platform data, tenant monitoring, vendor-licensed, tenant-private or personal. |
| Tenant | Global, a tenant slug, or `tenant` when rows carry tenant ids. |
| Rights | Learning and resale need explicit permission. Unknown means no. Licence status names the registry evidence. |

A workbench result carries the most restrictive input labels. Personal outranks tenant-private,
which outranks vendor-licensed, tenant monitoring and public platform data.
Red means **review this query**. A result that uses two tenants still runs for staff and leaves
an audit record. An unresolved input also appears in red. Labels describe every tenant present
in the inputs; a filter or empty result can narrow the rows without removing that warning.
Operators open Queries in the console to review cross-tenant records first; staff sees only its own records.

Direct SQL has relation comments and the catalog, rather than a result banner supplied by Postgres.
A local worker reads normalized query counters from `pg_stat_statements`. The record keeps the
login, observation time, call count, query hash, tenants and labels. It keeps no SQL text. SQL
logging is disabled and password DDL is not tracked. Counters can miss failed statements or
queries evicted before a poll; the time is when the worker observes them. Unqualified names,
parameters and shared relations with fewer than two observed tenants need review because
statistics cannot prove the session's search path, bound values or earlier row values. Named tenant schemas give a stable scope even for an empty result.

Tenant data remains under its operator’s control. Staff access does not permit an export or redistribution.
A tenant API key still reads only its own tenant. `analyst_ro` still reads only global data.
Some dbt models are CTEs (`ephemeral`), so they have no physical table; read a dependent mart.


## Local

Install the tools in [Quickstart](DEVELOPING.md#quickstart), then run:

```sh
bash ops/local/up.sh
source ops/local/env.sh
psql -X "$MDP_ANALYST_URL"
```

The login is `analyst_local`, with password `analyst_local`. It belongs to `analyst_ro`.
TablePlus and DBeaver use PostgreSQL, host `127.0.0.1`, port `56432`, database `warehouse`,
and SSL mode `require`. Startup prints the actual port and a ready connection string.
These fixed credentials belong only to this disposable local database.

Startup loads a synthetic week, September 14–20, 2026. The checked inventory below lists its marts. `--demo-week` also selects this default. The same week loads every time:
playlist positions move, tracks enter and leave, chart ranks change, and followers grow.
`marts.mart_chart_history` has chart positions by week. The new release appears in
`explore_staging.stg_lb__fresh_releases`. No collector makes a live request.

Some intermediate models are temporary SQL expressions, so they have no table to query.
Use `marts.mart_playlist_membership_current` for current membership instead of
`intermediate.int_playlist__membership`. Use the served event and profile tables below
for history. Run `bash ops/local/down.sh` to remove the stack and its data.


### Demo tenant

Tenant models require a tenant scope. Source the local environment first, then set the
scope on the command itself. To build the empty demo tenant tables:

This writes tenant models under `tenant_demo_*`; `analyst_local` cannot read them.
With the default global scope, dbt refuses tenant models before creating any tables.


### R and psql files

Use the actual port printed by startup. For example, when `MDP_LOCAL_PG_PORT=56540`:

```r
mdpr::mdp_setup(target = "local", port = 56540)
con <- mdpr::mdp_connect("mdp_local")
mdpr::mdp_tbl(con, "mart_song_day")
DBI::dbDisconnect(con)
```

`mdp_setup()` writes service `mdp_local` to `~/.pg_service.conf` and its password to
`~/.pgpass`, both with mode `0600`. Then run `psql service=mdp_local`.
For separate files, set `PGSERVICEFILE` and `PGPASSFILE` before setup in R:

```r
Sys.setenv(PGSERVICEFILE = "/tmp/mdp-local.service", PGPASSFILE = "/tmp/mdp-local.pgpass")
mdpr::mdp_setup(target = "local", port = 56540)
```

Use those same paths in the shell:

```sh
PGSERVICEFILE=/tmp/mdp-local.service PGPASSFILE=/tmp/mdp-local.pgpass psql service=mdp_local
```

A container uses its own files and network. In the database container's network namespace,
use `port = 5432`. See [R setup](r.md#set-up) to install `mdpr`.


### Local marts

<!-- local-marts:start -->
<!-- Generated by: uv run --project functions python ops/analyst-doc.py -->
The local week builds these marts and checks analyst access before startup finishes:

- `marts.mart_arrivals_current`
- `marts.mart_chart_history`
- `marts.mart_early_signals_current`
- `marts.mart_editorial_entries`
- `marts.mart_editorial_presence`
- `marts.mart_playlist_coverage`
- `marts.mart_playlist_events`
- `marts.mart_playlist_membership_current`
- `marts.mart_playlist_profile`
- `marts.mart_readiness`
- `marts.mart_shazam_chart_daily`
- `marts.mart_song_aliases`
- `marts.mart_song_cluster_members`
- `marts.mart_song_day`
- `marts.mart_top_movers`
- `marts.mart_top_movers_current`
- `marts.mart_track_daily_streams`

The list comes from [models.txt](../ops/local/models.txt). Run `bash ops/local/up.sh` to build it.
<!-- local-marts:end -->


## Fly account and connection

An operator provisions your login once and delivers its generated password through an approved
private channel. Keep it in a password manager, never in a notebook, source file or query history.
You also need a Fly identity authorized to reach `mdp-postgres` on the organization's private
network. A database password alone does not grant that network access.

With flyctl installed and signed in, keep this terminal open (choose another free local port
if 15472 is occupied):

```sh
fly proxy 15472:5432 -a mdp-postgres --bind-addr 127.0.0.1
```

The [fly proxy command](https://fly.io/docs/flyctl/proxy/) connects directly to the private
Postgres app. It bypasses `mdp-pg-frontend`, so `ops/fly/haproxy/allow.lst` does **not** apply.
The public frontend at `mdp-pg-frontend.example.invalid:5432` uses that source-IP allowlist and requires
an operator-managed entry for the client's public egress IP. No allowlist change is needed for
the private proxy recipe. Postgres requires TLS; `sslmode=require` encrypts the database
connection. `verify-full` additionally needs the operator's trusted CA and matching hostname.


### psql

Replace `analyst_demo` with your issued username. `-W` prompts for the password without
putting it on the command line. Session defaults use UTC and a 30-second statement timeout.

```sh
psql -X -W 'host=127.0.0.1 port=15472 dbname=warehouse user=analyst_demo sslmode=require'
```

Run `SELECT current_user;` to confirm the individual login. Use fully qualified table names.
For repeated notebook connections, libpq's `~/.pgpass` can hold the password locally:
`127.0.0.1:15472:warehouse:analyst_demo:<password>`, with file permissions `chmod 600 ~/.pgpass`.
Do not commit that file or paste its contents into evidence.


### DBeaver

Create a PostgreSQL connection: host `127.0.0.1`, port `15472`, database `warehouse`, your
individual username and password. In the SSL settings select SSL mode `require`. Test the
connection while the proxy runs, then open a SQL editor. Browse `marts`; disable auto-commit
only for a short multi-query snapshot, and commit or roll back promptly.


### Python / pandas

Install `pandas`, `sqlalchemy` and `psycopg[binary]` in your notebook environment. This uses a
password prompt and closes the connection after the read:

```python
from getpass import getpass
import pandas as pd
from sqlalchemy import URL, create_engine, text

engine = create_engine(URL.create(
    'postgresql+psycopg', username='analyst_demo', password=getpass('Database password: '),
    host='127.0.0.1', port=15472, database='warehouse', query={'sslmode': 'require'},
))
with engine.connect() as connection:
    df = pd.read_sql_query(text('SELECT * FROM marts.mart_shazam_chart_daily LIMIT 100'), connection)
engine.dispose()
```


### DuckDB

The [Postgres extension](https://duckdb.org/docs/current/core_extensions/postgres.html) reads
through the same login. Set up the private `~/.pgpass` entry above first; no password appears
in the ATTACH statement. Install/load the extension in the local DuckDB client:

```sql
INSTALL postgres;
LOAD postgres;
ATTACH 'host=127.0.0.1 port=15472 dbname=warehouse user=analyst_demo sslmode=require'
  AS warehouse (TYPE postgres, READ_ONLY, SCHEMA 'marts');
SELECT * FROM warehouse.marts.mart_shazam_chart_daily LIMIT 100;
```

The starter queries below use PostgreSQL syntax. In DuckDB, submit them unchanged with
`postgres_query('warehouse', '<escaped PostgreSQL SQL>')`, or adapt table references to
`warehouse.marts` and date expressions to DuckDB syntax. READ_ONLY also prevents accidental
remote writes from this client; warehouse privileges enforce the boundary in every client.


## Readable copies

For Billboard chart history, query
`marts.mart_chart_history`. For playlist changes, query `marts.mart_playlist_events`.
Raw rows stay restricted. A staff explorer can read reviewed `explore_raw` projections;
`analyst_ro` reads the global marts and reviewed staging and intermediate copies.
Run `SELECT * FROM catalog.relations` to find the copies your login can read.
Workbench and `mdpr::mdp_tbl()` add these next steps to database refusals.
Plain psql keeps PostgreSQL's own error; use the replacements above.


## Served marts

<!-- analyst-contracts:start -->
<!-- Generated by: uv run --project functions python ops/analyst-doc.py -->
18 served contracts: 18 global mart contracts permit analyst reads when built; 0 tenant marts excluded.

Grain is the composite row key; retain every grain column when joining or deduplicating.

| Mart / contract | Access | Grain | Other key columns |
|---|---|---|---|
| [`marts.mart_arrivals_current`](../dbt/models/marts/global/signals.yml) | SELECT | `song_key` | `cluster_key` |
| [`marts.mart_chart_history`](../dbt/models/marts/_marts__models.yml) | SELECT | `chart_name`, `chart_week`, `chart_position` | `song_key`, `source_key` |
| [`marts.mart_early_signals_current`](../dbt/models/marts/global/signals.yml) | SELECT | `movement_list`, `family`, `rank` | `song_key`, `cluster_key` |
| [`marts.mart_editorial_entries`](../dbt/models/marts/global/playlist.yml) | SELECT | `platform`, `playlist_id`, `variant`, `stream`, `occurrence_key`, `interval_id` | `platform_track_id`, `snapshot_id`, `platform_album_id`, `isrc`, `mb_recording_gid`, `platform_item_id`, `featured_track_id` |
| [`marts.mart_editorial_presence`](../dbt/models/marts/global/playlist.yml) | SELECT | `platform`, `playlist_id`, `variant`, `stream`, `occurrence_key`, `interval_id`, `event_type` | `platform_track_id`, `snapshot_id`, `platform_album_id`, `isrc`, `mb_recording_gid`, `platform_item_id`, `featured_track_id` |
| [`marts.mart_playlist_coverage`](../dbt/models/marts/global/playlist.yml) | SELECT | `platform`, `playlist_id`, `variant`, `stream`, `observed_at`, `snapshot_id` | `cycle_id`, `source_key` |
| [`marts.mart_playlist_events`](../dbt/models/marts/global/playlist.yml) | SELECT | `platform`, `playlist_id`, `variant`, `stream`, `occurrence_key`, `interval_id`, `event_type`, `observed_at` | `platform_track_id`, `snapshot_id`, `platform_album_id`, `isrc`, `mb_recording_gid`, `platform_item_id`, `featured_track_id` |
| [`marts.mart_playlist_membership_current`](../dbt/models/marts/global/playlist.yml) | SELECT | `platform`, `playlist_id`, `variant`, `occurrence_key` | `interval_id`, `platform_track_id`, `platform_item_id`, `featured_track_id`, `snapshot_id` |
| [`marts.mart_playlist_profile`](../dbt/models/marts/global/playlist.yml) | SELECT | `platform`, `playlist_id`, `variant`, `stream`, `observed_at`, `snapshot_id` | `owner_id` |
| [`marts.mart_readiness`](../dbt/models/marts/global/signals.yml) | SELECT | `family` | — |
| [`marts.mart_search_index`](../dbt/models/marts/_marts__models.yml) | SELECT | `object_key` | — |
| [`marts.mart_shazam_chart_daily`](../dbt/models/marts/global/shazam.yml) | SELECT | `chart`, `chart_date`, `position` | `apple_song_id`, `apple_primary_artist_id`, `isrc`, `mb_recording_gid` |
| [`marts.mart_song_aliases`](../dbt/models/marts/global/song.yml) | SELECT | `alias_key` | `song_key`, `platform_track_id` |
| [`marts.mart_song_cluster_members`](../dbt/models/marts/global/cluster.yml) | SELECT | `song_key` | `cluster_key`, `representative_song_key` |
| [`marts.mart_song_day`](../dbt/models/marts/global/song.yml) | SELECT | `song_key`, `day` | — |
| [`marts.mart_top_movers`](../dbt/models/marts/global/song.yml) | SELECT | `day`, `movement_list`, `rank` | `song_key`, `cluster_key` |
| [`marts.mart_top_movers_current`](../dbt/models/marts/global/song.yml) | SELECT | `movement_list`, `rank` | `song_key`, `cluster_key` |
| [`marts.mart_track_daily_streams`](../dbt/models/marts/global/streams.yml) | SELECT | `platform`, `platform_track_id`, `day` | — |
<!-- analyst-contracts:end -->

This is a contract inventory. It does not prove that a table exists in this environment.
Explorer marks absent models **Not built here** and shows a local build command.
Inline SQL models have no table; open one of their downstream marts.
The inventory is generated from dbt contracts, not a manually maintained count. Regenerate it
with `uv run --project functions python ops/analyst-doc.py`; `--check` detects drift in CI.
Other global staging/intermediate tables are available for exploration but are internal model
interfaces. They may include pseudonymous keys and are not an alternative export surface.


## Ten starter queries

Each block is one PostgreSQL statement, tested as `analyst_local` against the local demo week.
Weeks below mean ISO weeks beginning Monday in UTC, using observation time.
They are not tenant-local call weeks. Limits bound previews; a missing row means unknown or
unobserved, never automatically zero. Historical observations reflect the current mart build.


### 1. Latest tracked chart positions

```sql
SELECT chart, chart_date, position, apple_song_id, title_text, artist_text
FROM marts.mart_shazam_chart_daily c
WHERE chart_date = (SELECT max(chart_date) FROM marts.mart_shazam_chart_daily WHERE chart = c.chart)
ORDER BY chart, position
LIMIT 100;
```


### 2. Chart movers between observed chart dates

Positive `places_up` means a better rank. A gap spans the two observed dates, not necessarily
one day; a new entry has no prior position. Songs are matched by Apple song ID, never by name.

```sql
WITH history AS (
  SELECT chart, chart_date, apple_song_id, title_text, position,
         lag(position) OVER (PARTITION BY chart, apple_song_id ORDER BY chart_date) AS previous_position,
         lag(chart_date) OVER (PARTITION BY chart, apple_song_id ORDER BY chart_date) AS previous_date
  FROM marts.mart_shazam_chart_daily
)
SELECT *, previous_position - position AS places_up
FROM history
WHERE previous_position IS NOT NULL
ORDER BY chart_date DESC, places_up DESC, chart, apple_song_id
LIMIT 100;
```


### 3. Confirmed editorial adds by observation week

Restrict to editorial owners and full streams to avoid counting a head and full observation twice.
Baseline and unknown entries are not confirmed adds. The actual add lies between `entered_after`
and `first_observed_at`; the week is when it is first observed, not an exact add timestamp.

```sql
SELECT date_trunc('week', first_observed_at)::date AS week_start, platform,
       count(*) AS confirmed_adds, count(DISTINCT platform_track_id) AS distinct_tracks
FROM marts.mart_editorial_entries
WHERE owner_class = 'editorial' AND stream = 'full' AND NOT is_baseline
GROUP BY 1, 2
ORDER BY 1 DESC, 2
LIMIT 100;
```


### 4. Confirmed editorial removals by observation week

A removal is bounded by `removed_after` and `removed_by`. A disappearance from a partial head
is not a removal; count only confirmed `remove` events on the full stream.

```sql
SELECT date_trunc('week', removed_by)::date AS week_start, platform,
       count(*) AS confirmed_removals
FROM marts.mart_playlist_events
WHERE owner_class = 'editorial' AND stream = 'full' AND event_type = 'remove'
  AND removed_after IS NOT NULL AND removed_by IS NOT NULL
GROUP BY 1, 2
ORDER BY 1 DESC, 2
LIMIT 100;
```


### 5. Latest collector coverage by source

Raw receipts and control run ledgers are not exposed. This is observation coverage, not a count
of failed requests: a collector that lands nothing has no new row here. Ask the operator for
receipt-level failures. Keep head and full streams separate.

```sql
WITH latest AS (
  SELECT *, row_number() OVER (
    PARTITION BY platform, playlist_id, variant, stream ORDER BY observed_at DESC, snapshot_id DESC
  ) AS n
  FROM marts.mart_playlist_coverage
)
SELECT source_key, stream, coverage, count(*) AS playlist_variants,
       max(observed_at) AS newest_observation
FROM latest WHERE n = 1
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
LIMIT 100;
```


### 6. Coverage gaps to investigate

`days_since_full` is measured at the cycle's `as_of`, not the wall clock. Keep the timestamps
beside it to recognize an old warehouse build. Unknown full coverage sorts first.

```sql
WITH latest AS (
  SELECT *, row_number() OVER (
    PARTITION BY platform, playlist_id, variant, stream ORDER BY observed_at DESC, snapshot_id DESC
  ) AS n
  FROM marts.mart_playlist_coverage
)
SELECT platform, playlist_id, variant, stream, coverage, last_full_at, as_of, days_since_full
FROM latest WHERE n = 1
ORDER BY days_since_full DESC NULLS FIRST, platform, playlist_id, variant, stream
LIMIT 100;
```


### 7. A track's current playlist reach

The CTE picks one observed track so this runs immediately. Replace its SELECT with
`SELECT 'spotify' AS platform, '<track-id>' AS platform_track_id` for your track.
This counts tracked playlists by variant and extent, not unique listeners or all playlists.
Do not sum variants as distinct audiences; stale or head-only membership is weaker evidence.

```sql
WITH chosen AS (
  SELECT platform, platform_track_id
  FROM marts.mart_playlist_membership_current
  WHERE platform_track_id IS NOT NULL
  ORDER BY platform, platform_track_id LIMIT 1
)
SELECT m.platform, m.platform_track_id, m.variant, m.extent,
       count(DISTINCT m.playlist_id) AS tracked_playlists, min(m.position) AS best_position
FROM marts.mart_playlist_membership_current m
JOIN chosen c USING (platform, platform_track_id)
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4;
```


### 8. Playlist follower changes

A change is between observations, not necessarily a daily change. These are playlist followers,
not a track's listeners. Choose one stream for each comparison. Null follower counts or changes
mean no comparable measurement, not zero growth.

```sql
SELECT platform, playlist_id, variant, title, observed_at, followers, follower_change
FROM marts.mart_playlist_profile
WHERE stream = 'full'
ORDER BY observed_at DESC, follower_change DESC NULLS LAST, platform, playlist_id, variant
LIMIT 100;
```


### 9. Inspect event uncertainty before charting it

Keep the event type, baseline flag and time bounds in your output. `entry_unknown`, `baseline`
and `entered_head` are presence evidence, not confirmed editorial adds.

```sql
SELECT platform, playlist_id, variant, stream, platform_track_id, event_type,
       observed_at, entered_after, first_observed_at, removed_after, removed_by,
       is_baseline, uncertainty_hours
FROM marts.mart_playlist_events
ORDER BY observed_at DESC, platform, playlist_id, variant, stream, occurrence_key, interval_id, event_type
LIMIT 100;
```


### 10. Check rights before an analysis export

```sql
SELECT learning_eligible, resale_permitted, source_keys, count(*) AS rows_in_chart
FROM marts.mart_shazam_chart_daily
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
```


## Recording identity

`mart_playlist_events` and its editorial projections resolve identity from the bound daily
cycle's evidence: `daily_manifest_close_no` identifies its committed global close number and
`as_of` gives its UTC close time, even for events observed earlier.
An hourly identity build can know more than this daily snapshot; query time does not advance it,
and replaying the same daily close keeps the same evidence boundary.
Local builds without a mirrored close leave the number null and use event observation time.

`mb_recording_gid` can combine observations from several platform tracks. Keep
`recording_method` and served `confidence` beside it; confidence is a method score,
not a calibrated correctness probability. The identity evidence
and URL/release audit distinguish strict metadata agreement
from a relaxed rule accepting title-version/guest suffixes, artist aliases or credit order,
and canonical-duration differences:

Identity measurements must be repeated against an independently reviewed synthetic or permitted dataset. The public snapshot contains no production precision report.


## Movement groups

Movement marts can group several strict song keys for scoring. Their `song_key` is the
representative; `cluster_key` names the same scoring group. Read `member_song_keys` for every
strict key in it. A resolved recording leads, then the key with the most list, chart and stream
facts. Equal counts use ascending song key. The representative can change when a member resolves.
Use `mart_song_aliases` and `mart_song_day` for strict identity and daily facts.

Each evidence locator keeps its original source row and adds the group methods and members.
For example, three member keys mean “matched 3 copies.” This does not claim three independent
signals. A playlist or chart counts once within a group. Stream rates sum distinct track keys.
`cluster_confidence` is measured precision: the lowest merge-rule Wilson 95% lower bound. Open the cluster audit for the measured pairs.
Open the movement audit for rule switches, sample limits
and a query that expands a group.


## Analysis and export rules

Rights annotate rows; they never hide rows from a served mart. `learning_eligible` is true only
when all contributing registered sources and input flags permit derivative learning; false or
unknown means no training or derivative learning. `resale_permitted` is true only when all
contributing rights permit resale. It is not a blanket grant for every use. Preserve both flags
and `source_keys` through extracts and joins; aggregate rights conservatively across every input.
Analysis exports respect these flags: use eligible inputs for learning and resale-permitted
inputs for resale, and check the source's terms for the intended use. Access alone is not permission
to redistribute. Internal descriptive analysis can inspect ineligible rows.

Use the served annotations for exports, not unannotated staging rows. Pseudonymous identifiers
remain sensitive: do not reverse them or attempt to reconstruct private owner names. For the global analyst login, tenant
schemas, tenant source data and tenant-specific joins are out of scope. Do not request another
service role to work around a denial. Database reads do not invoke collectors or create workbench
schemas. Ask the operator about stale data or missing access; do not retry a long query endlessly.


## Operator account lifecycle

These commands need `uv` and an explicitly supplied administrator `MDP_WAREHOUSE_ADMIN_URL`
for the `warehouse` database. They do not fetch service secrets or choose an environment.
The grants file runs on initial boot and local initialization. On an existing volume an operator
applies `ops/fly/postgres/boot/init/10-warehouse-grants.sql` as the warehouse administrator once
before adding people; deploying an image alone does not rerun init SQL. No control migration is needed.

```sh
ops/fly/postgres/analyst-add.sh demo
ops/fly/postgres/analyst-remove.sh demo
```

Add creates `analyst_demo` with an independent generated password, prints it once after commit,
and refuses an existing name without rotating it. Handles are lowercase letters, digits and
underscores, start with a letter, and have at most 48 characters; `ro` is reserved. Do not pipe
add output to a shared log. Save the password immediately and deliver it privately.

Remove disables login, terminates existing sessions and drops the role. It refuses elevated or
unrelated roles and never cascades away objects. If unexpected dependencies prevent DROP, the
login stays disabled; the operator resolves those dependencies before rerunning removal. To replace
a lost password, run `analyst-account.py rotate <handle>`, then privately deliver the new password. Network
access is a separate operator-managed lifecycle.

Local proof and recorded output: h-analyst evidence.


### Sandbox access

Run `pnpm --dir control mdp warehouse sandbox status` with your individual connection.
It lists storage, objects, dependents, quota, notices and last used time.
For the same facts in the console, open Sandbox with a linked staff identity to see your warehouse schema.

Operators set `MDP_WAREHOUSE_ADMIN_URL` and run status with `--all`. The operator page
can freeze new objects, unfreeze a manual hold, or disable a login. It provides commands
for password rotation and archives so credentials and files stay in the operator's checkout.

```sh
uv run --project functions python ops/fly/postgres/analyst-account.py rotate demo
uv run --project functions python ops/fly/postgres/analyst-account.py disable demo
uv run --project functions python ops/fly/postgres/analyst-account.py archive demo

# Keep the archived work online under another individual login:
uv run --project functions python ops/fly/postgres/analyst-account.py archive demo --transfer analyst_successor
```

Add `--explore` for an explorer login. An explorer archive can transfer only to another
explorer. Archive disables the departing login before copying and preserves rights and labels.
It refuses removal while external objects depend on the schema; notices name their owners.
Open `archive.json`, notify those owners, and rerun archive after they move their inputs.

A quota hold stops new objects; it does not delete data or impose a hard byte limit on
updates to existing tables. Remove unused tables so the periodic checker restores CREATE.
Manual holds require the operator's Unfreeze action. The checker also raises an operator
alert for unsafe ACLs. Open Sandboxes and inspect the named schema.


### Movement copies

Use `marts.mart_song_cluster_members` to find a song's current movement representative:

```sql
select representative_song_key, cluster_methods
from marts.mart_song_cluster_members
where song_key = 'apple:123';
```

Movement API filters on `song_key` accept every member of a group. The returned `song_key` is the
current representative. Strict song history still uses the requested identity key. Open the score's
`evidence` to inspect its member keys and matching rules.


## Hot 100 song matches

Read `marts.mart_chart_history` for every chart entry, including unmatched entries.
The `billboard_hot100` row in `marts.mart_readiness` shows `keyed_entries` out of 100
for `chart_week`, with `chart_entries` showing how many were read; open `marts.mart_chart_history`
to inspect the unkeyed entries.
`song_key` is the movement group representative. `billboard_match_method` explains the match.
A `group` match may connect several platform copies; it is not a new recording identity.
Expand it through `marts.mart_song_cluster_members`.

```sql
select chart_week, chart_position, song_key, billboard_match_method
from marts.mart_chart_history
order by chart_week desc, chart_position
limit 100;
```

`marts.mart_song_day` carries `billboard_position`, `billboard_weeks_on_chart`, and
`billboard_debut` for each member on the chart's published date. A null means no keyed
observation or an unknown debut. The first captured week cannot prove a debut.
A true debut needs a complete preceding chart and a provider week count of one.
An earlier ambiguous entry that could belong to the group leaves the debut unknown.
Read the matching evidence before using these labels.
