# Functions conventions

Start with the [glossary](../docs/glossary.md) and [architecture](../docs/architecture.md).

## Concepts, in order

1. **Source key** names one registered function, such as `kexp_plays`.
2. **Builder class** says whether the function fetches outside data, reshapes inputs or enriches them.
3. **Declaration** tells the runtime the function's inputs, outputs, schema and schedule.
4. **Cadence** is the hourly, daily or weekly schedule the function belongs to.
5. **Scope** is global unless the function belongs to one tenant.
6. **Cycle** freezes the targets and readable dumps for one cadence and scope.
7. **Context (`ctx`)** supplies traced HTTP, record accounting and saved cursors.
8. **Dump** is a stored batch of records whose load receipt proves it reached the warehouse.

## A small real function

Copy the declaration shape in [sources/kexp_plays/function.py](src/mdp_functions/sources/kexp_plays/function.py):

```python
"""kexp_plays: KEXP's track plays by airdate window, daily from a watermark, with a two-year backfill one
airdate year per `backfill:` window. Operator-only and learning false until the registry
records KEXP's written permission; the key ships disabled."""

from collections.abc import AsyncIterator
from typing import Any

from mdp_functions import kexp
from mdp_functions.kexp import HOSTS, RadioPlay
from mdp_functions.layers import Ctx, bronze

@bronze(
    source_key="kexp_plays",
    exclusion_reasons=("not_a_trackplay",),
    writes=["raw.radio_plays"],
    cadence="daily",
    key=["station", "play_id"],
    schema=RadioPlay,
    hosts=HOSTS,
    # A year's backfill window is about 1,500 pages at one request a second.
    knobs={"enabled": False, "timeout_s": 3600},
)
async def plays(ctx: Ctx) -> AsyncIterator[dict[str, Any]]:
    async for row in kexp.plays(ctx):
        yield row
```

1. `@bronze` declares the daily output table, its row schema, record key and allowed hosts.
2. `plays` yields the records from [kexp.plays](src/mdp_functions/kexp.py), which handles requests, accounting and the watermark.
3. The runtime adds lineage and lands the rows; this source stays disabled and operator-only pending written permission.

## Authoring and ownership

### Ownership

[Layers and builder classes](../docs/architecture.md#layers-and-builder-classes)

- Python fetches and shapes records; SQL owns joins and aggregates.

- Drizzle owns control DDL.

- Use `functions_rt` for runtime state, `loader_wh` for raw landing and `service_read` for warehouse
  inputs/previews.

- Registry sync writes only code-owned columns; control owns knobs, alert acknowledgement/resolution and
  cursor `reset_generation`.

| Class | Reads | Writes | Egress | Declarations | Runtime adds |

|---|---|---|---|---|---|

| `@bronze` | outside sources | declared `raw.*` | traced HTTP | source_key, writes, cadence | lineage, accounting, tenant identity when applicable |

| `@silver` | declared relations | declared `raw.*` | denied in subprocess | reads, writes, cadence | pinned input, source keys, lineage |

| `@gold` | declared relations | declared `raw.*` | budgeted | reads, writes, cadence, llm_step or external=True | identity, configuration, eligibility, lineage |

| `@universal` | declared relations | declared `raw.*` or none | external=True permits it | reads, writes, external | async execution, lineage |

### Deploy probes

`canary=True` opts a declaration into a small dry-run probe. The default is false.
Declare it only for free public requests without an API key, quota, vendor balance or request cost.
The runtime also refuses paid transport and provider accounting. It skips busy, blocked or
backed-off hosts through the same admission used by scheduled requests.
The probe validates a sample in memory and creates no run, cycle, cursor, dump or raw row.
Keep stateful sources excluded. Add a caller test proving no request for excluded sources and no
persistent writes for opted-in sources in [test_canary.py](tests/test_canary.py).

### Page accounting

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- Authors call `ctx.observed(n)`, yield records, or call `ctx.reject(record, reason=...)`.
  Use `ctx.exclude(record, reason=...)` for a known, enumerable non-error class, such as a
  playlist episode or radio air break. Declare the exact reasons once in `exclusion_reasons`
  on the source decorator; an undeclared reason fails the run. Unknown item kinds and missing
  fields use `ctx.reject()`. Inspect the receipts' `row_exclusions` before changing that list.

- Published-page accounting requires observed = yielded + rejected + excluded; a mismatch is partial with
  `accounting_mismatch`.

- Bare yield requires one output; `ctx.emit(table, row)` routes multiple declared outputs.

- Unknown fields survive in `_extra`; validation failures retain records/reasons in `raw._rejected`.

- Error rejections lower row coverage; expected exclusions do not. Row acceptance below the
  source's declared floor fails the run.

- A failed run or a target floor miss fails its invoke model and so holds the cycle's close, unless the
  bronze source declares `blocks_cycle=False`: then the invoke returns the failed receipt as rows, the
  cycle closes, and the run's own alerts keep the miss visible. Declare it only for a lookup that fills
  optional values.
  Receipts show each batch's rejected share separately from the run's target coverage.

- A target succeeds after schema validation. A write with expected records and zero delivery fails; an empty read with no work succeeds.

- A completed target with excluded or rejected rows and no validated output opens a warning.
  It does not fail the run or park the target. The target and function pages show it beside request
  health. Open the producing run and follow the [runbook](../ops/runbooks/target_zero_yield.md).

- Never silently drop records.

### Checkpoints

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- `ctx.cursor` reads checkpoints; `ctx.set_cursor` commits a compare-and-set with page dumps, checking cursor
  version and reset generation without changing the latter.

- `ctx.alert(kind, message, **attrs)` records an alert and run event with the page.
  Its class needs a runbook row; attrs carry counts and codes, never record values.

- Run alerts open once per `(run_id, attempt_no, class)`. The runtime takes the latest
  `run_attempt.attempt_no`, or zero before the first attempt. The partial unique index and
  `ON CONFLICT DO NOTHING` keep settlement from reopening a resolved alert in that attempt.
  A later attempt can open a new alert. Migrations 0032 and 0033 define the index, insert grant
  and baseline for alerts without an attempt key. Target alerts keep their separate
  `once=True` rule while an alert remains unresolved. See [control_db.py](src/mdp_functions/control_db.py).

### Playlist cadence

[Playlist membership](../docs/architecture.md#playlist-membership)

- Playlist collectors (`playlist.py`, `soundcloud.py`, `bandcamp.py`) parse through `off_loop`, validate
  through `assemble`, and land through `account`, which yields the loop between emitted chunks.

- Each playlist collector has one source key per cadence (`<key>` daily, `<key>_weekly`), both in the daily
  job, and fetches only exported members whose frozen `params_json.cadence` matches; a spec without one stays
  daily.

- The weekly key fetches weekly members whose frozen `weekday_bucket` (target id hash mod 7) is the UTC
  weekday its bound cycle opened, so each is fetched once a week, the load spreads, and a batch that crosses
  midnight or retries keeps its bucket.

### Playlist envelopes

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- Only known non-track kinds (`NON_TRACK_KINDS`: episodes, music videos, SoundCloud non-track entities) are
  `unsupported_item`; any other kind is rejected and counts toward drift.

- A full list with no supported row lands partial with an `empty` envelope miss, unless the platform counts it
  as empty (count 0, no rows): that list lands full, and its removals stand.

### Owner identity

[Rights annotation and learning_gate](../docs/architecture.md#rights-annotation-and-learning_gate)

- Personal identity never lands: only an owner observed to be the platform's own account (Spotify's `spotify`;
  on an Apple catalog list, never a `pl.u-` library list, with a JSON-LD date, an `appleCurator` link or the
  bare "Apple Music" link with no listener profile, a chart when its rows carry `rankingText`; SoundCloud's
  account or a system playlist; Bandcamp's own lists) keeps a name.

- `assemble` lands the parser's observed class as `owner_class_observed` and decides on it (`PUBLIC_OWNERS`)
  before a target's label replaces `owner_class`, so a label never keeps a name, makes a win, or hides an
  algotorial list.

- A listed candidate lands no owner name, a platform-owned list serves the platform name, never the
  writer's byline, and fan playlists keep no handle (URL by playlist id).

- `owners.py` holds the shared rules (public classes, platform ids, the older-row evidence, served names and
  classes): the parsers import its constants, `mdp sources export` renders it for staging, so staging and landed rows share one rule.

- Every other owner id goes through `mdp_pseudonym` and its name is nulled.

### Transport refusals

[Rights annotation and learning_gate](../docs/architecture.md#rights-annotation-and-learning_gate)

- The source network guard is a guardrail for honest authors, not a sandbox.

- The runtime client keeps no cookies, so a Set-Cookie never becomes a visitor session.

- Every HTTP client the runtime builds is a `RefusingClient` (`fetch/forbidden.py`): its transport and proxy
  mounts refuse the runtime-wide forbidden-path list (DSP web-player token endpoints, internal GraphQL,
  spclient hosts, the Apple web app catalog API family) with `forbidden_path`, on the percent-decoded,
  dot-resolved path without `;params`, and refuse a Host header (the HTTP/2 :authority) naming another
  authority, so redirect hops, auth follow-ups and event-hook rewrites are checked too, whatever a manifest's
  `hosts` allow.

- A request extension outside `timeout`, `mdp_accept` and `mdp_redirects` is refused (httpcore acts on
  `target`, `trace` and `sni_hostname`), and a response carries no `network_stream`.

- Live smokes use it.

- `tests/test_forbidden_paths.py` proves each entry and path.

### Defaults and retirement

[Declarations drive everything](../docs/architecture.md#declarations-drive-everything)

- Each manifest may declare the control-owned knobs its streamline starts with (`knobs=`); the bootstrap seed
  (`streamline_defaults`) applies them once, only to a streamline still on the table defaults, so an
  operator's later choice stands.

- Collectors whose live surface refuses the honest user agent declare `enabled=False`.

- A removed function's source key goes in `streamline_defaults.RETIRED`: its streamline row stays (runs
  reference it) and is disabled once with an audit row, its rights row stays for the rows it landed, and `mdp
  sources export` deletes its invoke stub and generated tests.

- A declared `*.example.com` host covers every subdomain and paces, permits, and pauses them all as
  `example.com`.

### Promoter scope and ownership

[Tenant scoping and serving](../docs/architecture.md#tenant-scoping-and-serving)

- Admission refuses a compiled tenant relation (`tenant_<slug>_<layer>.<model>`) outside that tenant's own
  scope.

- Promoters write targets only through the control API (`ControlTargets`: `upsert`, `promote`, `propose` for
  pending targets an operator confirms, `deactivate`, and `spec`, where keys a spec already holds win), at
  `MDP_CONTROL_API_URL` with `MDP_CONTROL_API_KEY`, a `promoter`-role key that reaches only those target
  commands.

- Each decides what a set holds by the keyed lookup, never a paged listing.

- `promote` gives a new target its spec and activation in one call, and completes a held target an earlier run
  left without a spec.

- Any control-api failure (an error status, a refused connection) is `control_api_unavailable`, and any other
  error a derived body raises is `function_failed`, so a promoter run ends at once, never left running.

### Spotify pages

[Tenant scoping and serving](../docs/architecture.md#tenant-scoping-and-serving)

- `sp_artist_daily` reads each Spotify artist page's `stats` (followers, monthly listeners, top cities when
  present) and the play counts of the top tracks its discography lists, two tables keyed with `position` (the
  snapshot is 0).

- `sp_track_plays` (gold, one input per Spotify release and cycle day) lands the album page's track list with
  each track's count where the page carries one. A missing count remains unknown.
  A 404 album rejects its input, which parks after 3.

- What a row needs (`stats.followers`, `stats.monthlyListeners`, a list at `discography.topTracks.items`,
  `tracksV2.items`) goes through `ctx.require` (`streams.required`): missing, null or of another kind, it is an
  envelope miss that rejects the target or input and counts toward `surface_drift`, never a null that lands
  with full coverage and never an exception past the target, so the tenant's daily close never waits on a
  Spotify page.

- Optional parts (`topCities`, a track's album) read through `mapping`/`listing`; `topCities` of another shape
  is a miss too.

## Free-source rails

### Public free-source inputs

[Imports and source evidence](../docs/architecture.md#declarations-drive-everything)

- The free-source collectors need no key, login, token or proxy, and none reads a private person.

- `sz_chart` (`shazam.py`) fetches each seeded Shazam chart's CSV, for the chart date, rank and printed
  credit, then its page, for each row's Apple song id and its one artist link (a collaboration row links only
  its primary artist); a page row lands only when the CSV row of its rank names the same title
  (`csv_page_mismatch` otherwise), so CSV rank equals page rank on every landed row.

- It writes `raw.shazam_chart_entries` alone; Billboard keeps `raw.chart_entries`.

- Song pages and `/shazam/v1`, `/shazam/v3` are never fetched.

- The global `chart` set is written only by the seed (`chart_targets.py`, `inputs/chart_seed.csv`), which
  refuses more members than the `sz_chart` row of `dbt/seeds/chart_caps.csv`.

### Apple song durations

`apple_song_duration` is a daily bronze collector, disabled by default.
It reads exact Apple song ids from the global `track` target set through public iTunes Lookup.
It writes `raw.apple_song_durations` with a positive duration or `not_found` / `no_duration`.
A mismatched id or malformed answer rejects that target. Other platforms make no request.
Local and deployed bootstrap seed an empty global track set before export freezes membership.
The set must exist even while the function is disabled: admission checks membership first.
SQL uses these durations only where the movement input has none; strict identity stays unchanged.
Import a small Apple track set, resolve and activate its members, then enable collection with
`pnpm --dir control mdp knobs set apple_song_duration --enable --batch-size 10 --max-concurrency 1`.
It declares `blocks_cycle=False`: a missed floor fails its run and opens its alerts, and the daily
cycle still closes.
See [the declaration](src/mdp_functions/sources/apple_song_duration/function.py) before adding targets.

### Wikipedia inputs

[Imports and source evidence](../docs/architecture.md#declarations-drive-everything)

- `wiki_sitelinks` and `wiki_pageviews` (`wikimedia.py`) are gold and tenant-bound in the daily job: sitelinks
  look each act's QID up once a week (its input version is the cycle's ISO week), land Wikipedia editions only
  and one `raw.wiki_lookups` row per completed lookup (its article count, 0 included; a missing item or failed
  request lands none); pageviews read human views for the three days before the cycle's day and land every day
  of that window through the newest day any response of the run carried (Wikimedia leaves out a day it has not
  loaded yet), 0 for a day it lists none and for each day of a 404.

- Pageviews read at most `PROJECTS_PER_ACT` articles an act, every act's first edition before any act's second
  (`input_order`); both keys carry a 600 s `time_budget_s` that ends the run partial well inside the invoke's
  timeout.

- Their inputs declare the spine's `_source_keys`, which `Ctx.emit` reads from JSON array text.

- A tenant-bound gold run snapshots its compiled input relation and resolves its other declared tenant reads
  to the same tenant.

### ListenBrainz and radio gates

[Imports and source evidence](../docs/architecture.md#declarations-drive-everything)

- The ListenBrainz keys (`listenbrainz.py`: `lb_popularity`, gold and tenant-bound, one POST per act;
  `lb_sitewide`, the weekly top lists; `lb_fresh_releases`; `lb_similar_artists`, Labs neighbours looked up
  once a week, learn false until Labs outputs are confirmed CC0) ship `enabled=False` until the MetaBrainz
  supporter tier is bought, call only paths on the runtime's ListenBrainz allowlist, land null totals as "no
  data" rather than zero, and drop every tag at the parser.

- `kexp_plays` (`kexp.py`) ships disabled: the daily run asks for oldest-first pages (`ordering=airdate`) and
  reads from an airdate watermark (two hours of overlap; staging keeps one row per play), which advances with
  each page only while plays arrive in airdate order, so a failed later page is reread; a backfill window is
  at most one airdate year within the last two years (`backfill_window_refused` otherwise) and projects its
  write against the headroom line first (`kexp_backfill_projected`, `warehouse_disk_high` past it); host, show
  and comment text never land, and a non-track play is rejected `not_a_trackplay`.

- Its rows stay operator-only and learning false until the registry row's `review_ref` names KEXP's written
  permission.

### Bootstrap and host rates

[Declarations drive everything](../docs/architecture.md#declarations-drive-everything)

- A seed CSV that gains a column is recreated by the deployed bootstrap (`exporter.widened_seeds`), since a
  plain dbt seed cannot widen a table.

- The bootstrap also mirrors `dbt/seeds/rights_registry.csv` into `control.rights_source` as `rights_sync`
  (`rights.sync_rights`), which a gold row's learning flag reads.

- A free-source host's starting rate sits in `streamline_defaults.HOST_RATES`.

## Runtime and landing

### Pages and receipts

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- The runtime owns HTTP retries/traces, budget reservations, durable concurrency permits, batch leases,
  attempt deadlines, schema inference/fingerprints, Parquet/payload/rejected dumps, manifest-last publication,
  ten lineage columns, raw DDL, load receipts and recovery.

- A page ends before the next request or at target/function completion; its outputs and cursor register
  atomically.

- Completed targets and uploaded parts survive attempts.

- Running/draining batches consume permits; heartbeats do not extend attempt deadlines.

- Publication rechecks the current attempt/token/deadline before registering a page.

- Warehouse DDL precedes row transactions under an advisory lock.

- The receipt fence prevents equal/older generations inserting again; repair advances generation and replaces
  rows.

### Target failures and recovery

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- A target's own failure rejects that target with a typed reason and the batch goes on: a non-429 4xx and a
  stale target (404/410) are terminal for the work key, and a 5xx after retries stays resumable.

- A 429 or a transport failure after retries stops the batch at that target, which stays resumable; a host
  block pauses the host, so later targets there fail fast without a request.

- Recovery reconciles warehouse mirrors, pending loads and expired leases.

- Terminal failed and partial runs settle while recently touched (seven days), never settled, or holding
  pending, claimed or newly acknowledged loads. `run.settled_at` records the start of the grade's read;
  a later load acknowledgement remains eligible. An unchanged grade does not refresh `updated_at`.
  Retry clears the marker. Queued and running runs have no age limit.
  A terminal backfill stays eligible while its cycle is open or its close is not mirrored,
  even if the run succeeded or its grade is older than seven days.
  Migration 0035 defines `settled_at`; inspect [recovery.py](src/mdp_functions/recovery.py) and `/runs` for receipts.

- It also drops the fetch validators (the cursor's ETag and content hash) that a batch with a rejected or
  quarantined dump wrote, so the next fetch publishes full content; a cursor a later batch advanced keeps its
  own.

- Polling counts committed receipts and reports repairs without replacing the original terminal run status.

### Bindings and retries

[Cycles, cadence and close numbers](../docs/architecture.md#cycles-cadence-and-close-numbers)

- Bindings validate runner, registered job, cadence and scope.

- Scheduled work keys include source, scope, target set/revision and cycle, never configuration; manual keys
  are separately namespaced and conflicting reuse fails.

- Existing bindings finish after a runner switch; new inactive-runner bindings fail.

- Full retries resume work with fresh attempts; a Retry or a Replay of a closed cycle attaches to the
  canonical work key and its frozen `run.resolved_config`, whatever the current code.

- A Retry or restore attaches to the newest non-superseded cycle that neither a `backfill:` nor a `manual:`
  run id opened.

- Run Now under Core binds with `lock_runner`: `pg_try_advisory_xact_lock` on `core:<cadence>:<scope>` refuses
  409 `core_run_in_progress` while a run holds it, and binds nothing.

- An export freezes exactly the kinds in its payload (`mdp_export_kinds()`), frozen in `run.resolved_config`;
  a manual export reads the same generated list.

### Close stamps and mirrors

[Cycles, cadence and close numbers](../docs/architecture.md#cycles-cadence-and-close-numbers)

- Close locks the scope's `control.scope_close` row FOR UPDATE, takes `last_close_no + 1`, stamps the scope's
  unstamped output dumps whose load to the run's pinned warehouse is `loaded` (one control-only UPDATE through
  a partial index, O(new dumps); migrate destinations never hold a stamp back), and commits under the lock, so
  close order is commit order.

- Every close call, open or already closed, then runs the mirror catch-up (`catch_up`) under the scope's
  advisory lock: one warehouse transaction writes every stamp and closed `raw.cycles` row with `close_no` in
  `(mirrored_close_no, last_close_no]`, and `mirrored_close_no` advances only after it commits.

- A close is terminal, and returns, once `mirrored_close_no >= close_no`; otherwise it answers
  `warehouse_unavailable`, and the next close or recovery completes the catch-up without skipping a close.

- A tenant close freezes the job's `global_inputs`, mirrored in `raw.cycles`, and takes `global_close_no` from
  the global row's `mirrored_close_no`.

- `control.cycle_manifest(cycle)` is the manifest: `list` cycles (closed before stamps, `close_no` 0) read
  their `cycle_input` rows; `stamp` cycles read their scope's stamps through `close_no`, declared global
  tables through `global_close_no`, and their derived rows.

- Dumps committed before stamps carry `close_no` 0.

- Bind upserts `raw.cycles` for the states it changes but leaves an unmirrored closed row to the catch-up, and
  a mirrored status never goes back to `open`.

- `cycle_input.mirrored_at` marks mirrored derived rows, and a run polls terminal only once its derived rows
  are mirrored.

- Recovery runs the catch-up for every lagging scope and mirrors derived rows.

### Run completion

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- A `completion=True` bronze run gets `raw._run_completion` as its last dump after every batch is terminal:
  its registered output dumps per table with row counts, plus `ctx.record_completion()`.

- Settle writes it too, fenced on the run's latest attempt, so a run that recovery settles after a crash or an
  expired deadline still gets it.

## Identity spine

### Reference declarations

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- `musicbrainz.py` holds the platform URL patterns (one set for the tracked-URL predicate and the parser, with
  the Wikidata, Discogs, Instagram, TikTok and YouTube hosts beside the DSPs; the mirror's `mdp.tracked_url`
  recomputes from them at each import), the scoped spine SQL and the lookups.

- The spine closure also reaches every label a closure artist has an affiliation with (`ARTIST_LABEL_TYPES`;
  employment types never land), then each label's parents, distributor and rename successor up to the root
  owner (`label_closure()`), and the artists' IPI and ISNI codes; `mb_resolve` answers never carry these
  (`EXTENSION`).

- The mirror is read as `mb_reader` through `MDP_MB_DB_URL`; each generation database carries one
  `mdp.generation` row (`ops/fly/mb-db/mdp-schema.sql`).

### Spine snapshots and retention

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- `mb_spine` (weekly universal, `external=True`, `completion=True`) lands a validated generation newer than
  its cursor's, scoped to the tracked catalog: it reads the hourly `int_identity__track_inputs` off-job (a
  completion universal run snapshots its reads into the run's input dump, and its retries reuse that dump),
  turns them into seeds (`tracked_seeds()`: ISRCs and platform track, album and artist ids), and lands their
  closure (`seed_ids()`, `closure()`, `SPINE`) with every table in one REPEATABLE READ snapshot.

- Each chunk is a page whose cursor commits with its dumps, a retried attempt resumes the run, and a new run
  restarts the generation so one completion lists all of it.

- `raw.mb_generation` (with the rows read per table) is its last output.

- Its `prepare` step (a universal manifest hook the runtime calls after the reads load) is the one writer that
  deletes from `raw.mb_*`: it keeps the newest two reconciled generations, then stops the landing with a
  `warehouse_disk_high` alert when raw plus the reference copy of one more generation would pass
  `MDP_PGDATA_CEILING` of `MDP_PGDATA_VOLUME_BYTES`.

- It measures the closure-size trigger (one generation's projected write past 30% of the volume, or
  more than 100k tracked recordings), opens `spine_narrowing_due` once either passes, and the reference probe
  copies both figures to the Reference page.

### Priority lookups and probes

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- `mb_resolve` looks priority tracks up on the mirror (album URL to release candidates, then trigram) and
  lands, beside each resolved answer, the spine rows it touched in `raw.mb_resolve_closure` (the `raw.mb_*`
  row shape with `mb_table`), keyed per row by `output_keys`; reference staging joins them for keys the landed
  generation lacks.

- Its statements never let the planner start from a title or a URL tail, whose row counts it cannot estimate
  on the 40M-recording mirror: album URL ids go through the tail index into an array before the release links,
  trigram candidates start from the artist credit with the title compared through `similarity()`, and
  `lookup_session()` sets the trigram threshold to `TRIGRAM_FLOOR`;
  `test_a_lookup_reads_a_bounded_part_of_the_mirror` bounds the pages a lookup reads.

- A title or first artist without an ASCII letter or digit has no trigrams under the mirror's C ctype and
  skips the trigram step.

- `connect()` tries three times (2 s, then 5 s apart) before an input counts against the run's circuit.

- Recall limits of the trigram step are in `ops/evidence/mb-resolve-perf/`.

- `mb_artist_catalog` (gold, daily, ships disabled) reads each credited artist's catalog depth on the mirror once
  a week (its input version is the cycle's ISO week): the release groups crediting the artist by primary and
  secondary type, each kind with the earliest dated release year from `release_country` and
  `release_unknown_country`. `release_group_meta` is supplementary and never read. SQL decides which kinds count.

- Every input lands one lookup row, `not_found` and `special_purpose` included; a merged gid reads its redirect
  target. Special purpose artists are MusicBrainz's documented list by MBID (`SPECIAL_PURPOSE_ARTISTS`), with any
  bracketed name as the backstop. Its input declares `_source_keys` `["mb_spine"]`, so the songs that select an
  artist never enter the reading's lineage.

- The catalog reads its artist, generation and release groups in one statement on a reused connection.
  Each query gets at most ten seconds and the remaining run budget. A budget stop leaves that artist
  incomplete for the next daily cycle. `catalog_week` is declared as text in both outputs, matching
  bootstrap and the staging views. Explicit output types also own input-version columns on publication;
  landing keeps declared text storage when reading a retained typed dump. Check the catalog receipts
  before enabling it with `pnpm --dir control mdp knobs set mb_artist_catalog --enable`.

- `track_isrc_crosswalk` searches Deezer.

- Both land negatives, page oldest first and end `partial` at their budgets.

- `reference.py` probes the mirror (`mdp_meta` import runs and volume) and the landed generation for the
  Reference page and the four reference alert classes, and holds retention and the volume guard; recovery runs
  the probe every 15 minutes when the mirror is configured, and `POST /v1/reference/probe` runs it now.

- It clears a class only through `control.resolve_reference_alert`, because resolution is otherwise
  control-owned.

## Fixture loop

### Local fixture commands

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- Configure the role URLs listed in [README](README.md) and a disposable local Postgres first.

- Commands run from the repository root; fixture and dbt processes open DuckDB sequentially.

```sh
# PostgreSQL fixture invocation:
uv run --project functions mdp run billboard_hot100 --fixture --target pg_local
```

### Reader counts

`Targets` declares the platforms a reader counts in its frozen membership.
For example, `Targets("playlist", platforms=("spotify", "sp"), member_cadence="weekly")`
counts weekly Spotify lists across every weekday bucket.
Bandcamp readers also declare their `id_prefix`, such as `radio:`.
Without declared platforms or a target set, a reader has no tracked count.
`mdp sources export` writes the per-reader units to the control contracts.
Regenerate with `uv run --project functions mdp sources export` after changing a selector.

## HTTP and traces

### Service HTTP contract

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- `POST /v1/invoke` durably admits work and returns 202; poll `GET /v1/runs/{id}` for receipts.

- Only `/v1/health` is unauthenticated: it checks control, warehouse and object storage and returns aggregate
  200/503 status.

- `/v1/health/detail`, docs, OpenAPI and all other routes require the bearer token. [Service
  OpenAPI](openapi/service.json) defines the HTTP contract.

### Trace correlation

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- Follow receipt `trace_url` and `run.trace_id`; without OTLP these are local correlation identifiers backed
  by JSON logs.

- Join `_request_id` to call_ledger, `_dump_id` to manifests and receipts, and `_cycle_id` to the cycle
  binding.

### SQL invocation

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- Postgres UDF installation uses `udf/postgres/install.sh` as administrator.

- The private configuration holds the token; only dbt_transform invokes.

- Connect/read timeouts and the poll deadline bound HTTP; cancellation takes effect at the next PostgreSQL
  boundary.

- Admission sends the seconds left as `deadline_s`; the service ends the attempt 20 s inside it
  (`admission.attempt_seconds`, at most `timeout_s`), so the UDF reads the run's terminal receipt before it
  gives up. A failed run raises its receipt's error class and message with the `/runs/<id>` link.

- A slow admission retries with the same Idempotency-Key until the deadline; a refused connection fails
  at once as `service_unreachable`.

- A poll that times out or loses its connection retries after 0.5 s, then 1 s; three misses in a row fail as
  `service_unreachable`. Reaching the deadline, in a backoff or a clipped poll, fails as `invoke_timeout`
  with the run id, its last status and target count.

- The Snowflake twin shares this rule.

- Collectors parse off the event loop, so polls keep answering during large parses.

- Run polls, admission and health checks use the API's own thread pool (`api.CONTROL_THREADS`); page
  landing and loads use asyncio's default executor, so a queue of slow landings never delays a poll.

## Derived work, backfill and migration

### Declared reads and tenant scope

[Tenant scoping and serving](../docs/architecture.md#tenant-scoping-and-serving)

- Silver subprocess hooks block sockets, DNS, subprocess and native-library entry points; Linux also uses a
  network namespace when available.

- Declared reads are pinned snapshots.

- Silver, gold and universal reads are dbt relations; registration refuses `raw.*` reads.

- A tenant-bound function declares tenant relations without the slug (`tenant_marts.<model>`), and its reads
  resolve to the run's `tenant_<slug>_marts` (a slug may hold a hyphen; reads quote every part).

### Cycle context and scheduled work

[Weekly evidence and calls](../docs/architecture.md#showcase)

- Derived functions see the bound cycle as `ctx.cycle` (with `opened_by_dbt_run_id`, the frozen
  `local_weekday`, and `global_closed_at`, the time of the global close its build read) and, in a tenant run,
  `ctx.tenant` with its status and timezone.

- Tenant timezones load with zoneinfo from the `tzdata` package, since slim images lack the backward-link
  names.

### Gold configuration and reruns

[Enrichment inputs and budgets](../docs/architecture.md#enrichment-inputs-and-budgets)

- Gold `config_version` is the declared decorator `version` plus the declared parameters (the LLM step's
  configuration, model steps and preprocessing); a PR bumps `version` when outputs should change.

- The image digest and module source never enter it, so a deploy keeps every completion valid.

- Admission freezes it, with its step ids, in `run.resolved_config`; step budgets resolve through those ids,
  and `llm_step.step_version` is the step's own version (the column beside it, `llm_step.config_version`, is
  the compatibility alias for pre-0011 images, synced by trigger and never read here).

- Gold rows carry `run_admitted_at`, and marts rank configurations by it, newest first.

- A rerun by `config_version` is a `manual:` work key with its own frozen configuration and its own input
  snapshot.

- It binds the newest closed scheduled cycle of the function's cadence and scope (whose build the input
  relation holds), and its dumps never become `derived` rows: the next close stamps them.

- Its read is off-job: `ACCESS SHARE` on the relation and every page in one REPEATABLE READ transaction before
  processing.

### Gold budgets and paging

[Enrichment inputs and budgets](../docs/architecture.md#enrichment-inputs-and-budgets)

- A declared `time_budget_s` (it requires `knobs={"allow_partial": True}`, or registration refuses it) starts
  after the paged read; after it no new input is admitted, the run ends `partial` (`time_budget`, no
  `partial_coverage` alert), and it is terminal for its work key: a Retry, Replay or restore returns its
  receipts.

- A gold `input_order` pages the relation in that order (the identity lookups: oldest `retry_week`, then
  lowest `first_landed_seq`), so a budget end leaves the newest inputs for the next run.

- Gold input reads page the declared relation with a server-side cursor and drop every input whose completion
  is valid for the cycle before snapshotting, so completed inputs are never snapshotted, observed or rejected.

- In the relation's own job a part enters the input dump when the run takes it up, so the dump holds only what
  the run processed; parts are read one at a time.

- The runtime appends each declared `input_version` component column beside `input_version`.

### Gold output identity and completion

[Enrichment inputs and budgets](../docs/architecture.md#enrichment-inputs-and-budgets)

- Enrichment keys are `(_source_key, scope, input_ref, input_version, step, config_version, output_key...)`,
  with `step` the LLM step id, `models:<bundle>` or `external:<source_key>`; a written table named in
  `output_keys` takes its own key in place of `output_key` (several rows per input beside a table with one).

- Each input lands its outputs, then a `raw._enrichment_completion` row listing the required dumps and counts
  per table.

- An input counts as done only when that row and every listed dump are in the run cycle's manifest and the
  counts reconcile; otherwise it is processed again, so an older-cycle replay may land a second physical copy.

### Gold failures and drift

[Enrichment inputs and budgets](../docs/architecture.md#enrichment-inputs-and-budgets)

- A `vendor_retryable` or `vendor_4xx` error fails only its input: a typed reject with no completion, looked
  up again by the next run, and the run ends `partial`; three in a row reject the rest of the run's inputs
  without a call.

- An input its function rejects (a page without its state) stays incomplete too, and the run ends `partial`
  (`input_rejected`).

- A reject whose reason is `envelope_mismatch` or `drift:…` is surface drift only in a streak: three in a row
  on inputs the streamline has not rejected in the window (`rejected_before`), with no parsed input between
  them.

- The streak ends the run's reads, the rest wait unrejected, and the run ends `partial` (`surface_drift`) with
  the critical `surface_drift` alert, which pauses the streamline; a gold input's envelope miss records its
  event and leaves that decision to the derived path.

- Any other miss is its input's own: one with a parsed input after it in the run, one on an input rejected
  before, or one after the run's last parsed input.

- So a page without one track's entity parks that track, and inputs that missed once never pause the rail
  again by heading the read.

### Gold input parking

[Enrichment inputs and budgets](../docs/architecture.md#enrichment-inputs-and-budgets)

- A gold function may declare `park_after`: an input (`input_ref`, `input_version`) that failed on its own (a
  `vendor_4xx`, a reject that is not drift, or such a miss) in that many runs within 28 days is parked, left
  out of the read (`sp_track_artists` parks after 3), so dead ids never head the oldest-first order or open
  the circuit on live ones.

- A run whose circuit opened, a `vendor_retryable` error, and a drift streak park nothing.

- The read counts the parked inputs on its `input_snapshot` and `inputs_parked` event and opens one
  `inputs_parked` alert per streamline (a warning from 10); resolving it (`streamlines.unpark`) releases them,
  and failures also age out of the window.

- Retries reuse retained results and land only missing outputs.

- `batch_size` sets how many inputs share one page.

### Backfill and migration

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- `POST /v1/backfill` accepts exactly one window, target_ids or closed cycle_id selector and creates a fresh
  isolated cycle/work key.

- Backfill cursors do not advance live cursors.

- `POST /v1/migrate` queues retained committed output for an explicit registered warehouse, then copies every
  control mirror from the warehouses runs were pinned to (mirrors are incremental, so the catch-up never
  re-sends history); repeat requests reuse loads and upsert the mirrors by key.

- It does not change the production warehouse pin.

- Workbench runs separately; see [control conventions](../control/CLAUDE.md#workbench).

## Verification

### Verification gates

[Runtime accounting and recovery](../docs/architecture.md#runtime-accounting-and-recovery)

- `bash functions/tests/checks.sh` runs lint and the suite with local configuration; extra arguments go to
  pytest.

- A test that needs PostgreSQL carries the `docker` marker (conftest adds it to every test on the disposable
  databases); `-m "not docker"` runs without one, as CI's offline job does.

- `test_reference_probe.py` reads what the `test_identity_*` files build, so it runs after them in one
  session.

- `bash functions/tests/run-postgres.sh -q` uses disposable databases for integration tests.

- `tests/test_enrichment_runtime.py` covers silver egress, LLM accounting, pinned configuration reruns and cost
  reconciliation; `tests/test_backfill_migrate.py` covers isolation and migration recovery.

- `ops/ci/accept-adversarial.sh` includes accounting, egress, identity and receipt adversarial cases.

- Offline skips do not establish integration acceptance.

- See evidence.

## Exploration labels

Raw fields default to hidden. A model lists reviewed fields in
`model_config = {"json_schema_extra": {"non_personal": ["field", ...]}}`.
New and unknown fields stay null until classified. Free-form JSON stays hidden.
A personal identifier declares `Field(..., json_schema_extra={"explore": "pseudonym"})`.
A private name or payload declares `"explore": "omit"` instead.
The [label generator](src/mdp_functions/relation_labels.py) reads these field declarations,
retention, dbt lineage and rights. Regenerate with `python ops/label-catalog.py` through the
functions environment after `dbt parse`. Staff read the generated `explore_raw` views.
Historical metadata projections include only global cycles and their dump membership.
The generator supplies each projection's row filter alongside its reviewed columns.
Backtest checks retained rows against the safe load receipts. Choose Preview when history is incomplete.

Workbench source references use those views, so `mdp_pseudonym` keeps their existing pseudonyms.
The local stack uses the same fixture key as `pg_local`.

Shared relation columns use `dbt/shared_columns.yml` for reviewed non-personal outputs and
column `meta.privacy` / `meta.representation` for pseudonyms, omissions and private retained inputs.
The label generator reads both. Missing fields are null in generated `explore_<schema>` copies.
A direct staff grant requires every physical column to be declared non-personal.
`ops/ci/privacy.py --database` scans every staff-readable relation and column.
Retained comment text has representation `private`; only dbt reads its original relation.
The comment enrichment input reader refuses it before fetching rows or calling a model.
