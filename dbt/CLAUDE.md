# dbt conventions

Start with the [glossary](../docs/glossary.md) and [architecture](../docs/architecture.md).

## Concepts, in order

1. **Source** is a declared raw table that SQL reads through `source()`.
2. **Model** is a SQL query that dbt builds and other models read through `ref()`.
3. **Layer** says whether a model cleans raw rows, joins them or presents an enriched result.
4. **Cadence** is the model's hourly, daily or weekly schedule.
5. **Scope** says whether a model is global or belongs to one tenant.
6. **Cycle manifest** is the set of dumps that a build may read before it deduplicates rows.
7. **Mart contract** declares output columns and types, with a grain that identifies each served row.
8. **Rights annotation** attaches source and eligibility flags while keeping the serving rows.

## A small real mart

The complete `mart_chart_history` entry in [the mart contracts](models/marts/_marts__models.yml)
comes first; other models in that file follow it.

```yaml
version: 2
models:

- name: mart_chart_history
  config:
    contract:
      enforced: true
    meta:
      grain:
      - chart_name
      - chart_week
      - chart_position
  columns:
  - name: chart_name
    data_type: varchar
    constraints:
    - type: not_null
  - name: chart_week
    data_type: date
    constraints:
    - type: not_null
  - name: chart_position
    data_type: integer
    constraints:
    - type: not_null
  - name: track_title
    data_type: varchar
  - name: artist_name
    data_type: varchar
  - name: song_key
    data_type: text
  - name: billboard_match_method
    data_type: text
  - name: confidence
    data_type: double precision
  - name: matched_by_group
    data_type: boolean
  - name: weeks_on_chart
    data_type: integer
  - name: is_debut
    data_type: boolean
  - name: source_key
    data_type: varchar
  - name: learning_eligible
    data_type: boolean
    data_tests:
    - not_null
  - name: _cycle_id
    data_type: text
  - name: _built_by
    data_type: text
  - name: resale_permitted
    data_type: boolean
    data_tests:
    - not_null
  - name: source_keys
    data_type: text
    data_tests:
    - not_null
```

Its [SQL model](models/marts/global/mart_chart_history.sql) reads staged rows and annotates them:

```sql
{{ config(tags=['cadence:daily']) }}
with chart as (
    select
        cast('hot-100' as varchar) as chart_name,
        chart_week, chart_position, track_title, artist_name,
        song_key, billboard_match_method, confidence, matched_by_group, weeks_on_chart, is_debut,
        cast('billboard_hot100' as varchar) as source_key,
        source_keys as _source_keys,
        cast({{ mdp_literal(mdp_context().cycle_id) }} as text) as _cycle_id,
        cast({{ mdp_literal(invocation_id) }} as text) as _built_by
    from {{ ref('int_billboard_song__daily') }}
)
{{ mdp_annotate('chart') }}
```

1. The contract enforces column types and declares one row per chart, week and position.
2. SQL reads `int_billboard_song__daily`, whose daily staging applies the cycle manifest before matching.
3. `mdp_annotate` selects the contract columns and attaches the three rights columns from `_source_keys`.

The [project config](dbt_project.yml) supplies table materialization and global/silver tags;
the SQL declares its daily cadence. Collection stays weekly.

## Contracts and layers

### Contracts and raw ownership

[Declarations drive everything](../docs/architecture.md#declarations-drive-everything)

- Write enforced mart contracts with portable types before SQL.

- Match numeric precision in SQL casts and YAML; use text for public UUIDs.

- Regenerate the catalog and data SDK.

- Before a PR, run `bash ops/ready.sh` to check generated health policy, source readers and lineage before the selected tests.

- The command creates a missing local profile from the example and installs locked control dependencies before SDK generation. It preserves an existing profile.

- For a SQL edit, `bash ops/ready.sh` also builds changed models, downstream models and their inputs in a fresh DuckDB file; macro, contract, seed and deletion changes build the full project.

- Before a PR, a macro, model, seed, function declaration or peek review change also needs
  `control/apps/showcase/lib/lineage.generated.json` regenerated from a fresh dbt parse.
  From the repository root, run `uv run --project functions python ops/showcase/lineage/generate.py`,
  then repeat with `--check` and commit the result. Follow
  [Before a PR](../docs/DEVELOPING.md#before-a-pr) for the profile and parse commands.
  Review changed SQL fields before updating a peek review hash in `ops/showcase/lineage/peek.json`.

- Function declarations own raw DDL; `bootstrap_raw` creates empty dev/ci tables, and the deployed bootstrap
  creates the same declared tables and columns (`ensure_raw`, additive), plus the cost-ledger mirror and every
  control mirror.

- A raw table that no function writes (an operator load) declares its schema on the manifest that consumes it
  (`schema={table: Model}`), so both create it empty.

- The runtime mirror `raw.cost_ledger` takes its source columns and local bootstrap shape from
  `costsync.COST_LEDGER_COLUMNS`; it has no function writer or dump lineage.

- Models read warehouse relations through `source()` or `ref()`; `lint-dbt.sh` rejects literal
  schema-qualified reads.

### Observed owners and privacy

[Rights annotation and learning_gate](../docs/architecture.md#rights-annotation-and-learning_gate)

- Personal identifiers go through `mdp_pseudonym` (a keyed hash; the key is `mdp.pseudonym_key`, readable only
  by dbt_transform) before any shared relation.

- Playlist staging decides every owner question on `owner_class_observed`, the class the collector saw (for
  rows landed before it, the same payload evidence), never on the landed `owner_class`, which a target's
  frozen label sets.

- The rules come from `mdp_functions/owners.py`, rendered by `mdp sources export` into the generated
  `mdp_owner_rules.sql`, which the raw owner-name repair also applies.

- Only a row whose observed owner is the platform's own account keeps `description`, `owner_id` and
  `owner_name` (a platform-owned list serves the platform name, never a byline); for any other owner staging
  nulls all three, so no mart can serve them, and keeps the pseudonym in `owner_key` for joins, which no
  served mart selects.

- A candidate's owner gets a pseudonymous `hint_owner_id` and no name.

- The served `owner_class` is the observed class: a label fills only an observed `unknown` and may refine
  `editorial` to `chart`, so it never replaces an observed `dsp_algorithmic` or `user`.

### Rights and tenant learning

[Rights annotation and learning_gate](../docs/architecture.md#rights-annotation-and-learning_gate)

- Rights annotate serving rows.

- Registry relationships warn; only derivative consumers use `learning_gate`, which treats unknown eligibility
  as false.

- Every served mart ends in `mdp_annotate()` over its own `_source_keys` (JSON array text) and input flags;
  ineligible rows stay served.

- Derive `_source_keys` from upstream row keys (`mdp_source_keys_agg`) or carried arrays
  (`mdp_source_keys`); explain a retained literal where no per-row key exists. The dbt-ci review gate
  checks served marts against raw `meta.writers` lineage and rejects missing keys. Final annotation
  unions every reachable raw writer with the carried keys: joins, branches, gaps and display metadata
  cannot make rights more permissive. Historical comparisons also carry their input arrays; the writer
  floor is a conservative bound, not proof that each source supplied a row.

- Tenant material never trains: `mdp_annotate()` marks every `scope:tenant` relation learn false
  whatever its sources, and `learning_gate` refuses at compile time a tenant schema, a `scope:tenant` model,
  and a raw table a tenant-bound key writes (declared `tenant_id`); a fit reads the tenant-bound CC0 sources
  only through a global projection with no tenant key.

### Staging keys and lineage

[Cycles, cadence and close numbers](../docs/architecture.md#cycles-cadence-and-close-numbers)

- Staging casts, renames and deduplicates declared keys without cross-source joins.

- Models joined by many others (`stg_playlist__*`, the `int_playlist__*` hubs) are tables with an
  `mdp_analyze()` post-hook, so Postgres plans them from real statistics, never a one-row guess.

- Apply `mdp_context().manifest_filter('_dump_id', '<raw table>')` first, then keep one row per logical key in
  the declaring function's order and declare `logical_unique` on that key: the latest `_landed_seq` (then
  `_dump_id`), or the earliest for a function declaring `dedupe='earliest'` (a write-once record such as
  `raw.weekly_calls`).

- `mdp sources export` writes the order into the sources YAML (`meta.dedupe`), and new staging orders by
  `mdp_dedupe_order('<raw table>')`.

- Generated raw tests check physical uniqueness per dump over `(_dump_id, key)` for the declaring
  `_source_key`; separate dumps may hold copies of one logical row after a replay.

- Preserve `_run_id`, `_dump_id`, `_landed_seq`, `_cycle_id`, `_revision_id`, `_target_id`, `_request_id`,
  `_source_key`, `_ingested_at`, `_extra`, and applicable `tenant_id`/`_source_keys`.

- Public mart columns follow their individual contracts.

### Layers and cadence

[Layers and builder classes](../docs/architecture.md#layers-and-builder-classes)

- Staging is bronze; intermediate and global/tenant marts are silver; gold functions write their own tables.

- Each model has exactly one `cadence:hourly|daily|weekly` in its own config and one `scope:global|tenant`.

- Do not put cadence tags on folders or expand scheduled selectors across cadences with `+`.

- Cross-cadence refs read an already-built table; `lint-dbt` rejects a cross-cadence ref to a view or
  ephemeral model, which the other cadence would re-evaluate or drop.

## Selectors and invocation

### Selector inventory

[Cycles, cadence and close numbers](../docs/architecture.md#cycles-cadence-and-close-numbers)

- Selectors are `<cadence>_<global|tenant>_<bronze|transform>`; unscoped aliases select global.

- Bronze intersects cadence, scope, bronze and export/invoke/close tags; transform excludes that bronze
  selection.

- Read the generated `dbt/target/manifest.json` and [selectors](selectors.yml) for the current graph.

- `bash ops/ci/lint-dbt.sh -v` reports selector membership; materialized counts exclude ephemeral models.

### Tenant cadence exports

[Declarations drive everything](../docs/architecture.md#declarations-drive-everything)

- Playlist `_weekly` sources run in the daily job (one weekday bucket a day).

- The exporter writes a tenant export and close only for a cadence with tenant work (a tenant-bound function
  or a tenant model): the daily tenant job, and the weekly one, which holds the weekly call record, so each
  tenant gets a daily and a weekly machine.

- Evidence: free-sources gates.

- Remeasure with `bash ops/ci/lint-dbt.sh -v` from the repository root when the graph changes.

### Invocation ordering

[Cycles, cadence and close numbers](../docs/architecture.md#cycles-cadence-and-close-numbers)

- Invoke models are tables with explicit `depends_on`, one literal source invocation, one cadence and a
  same-cadence dependent.

- Export freezes membership before invocation; close depends on export and every bronze invoke in its cadence
  and scope.

- Only function invokes carry `invoke`; export and close use their own tags.

- Tenant stubs end in `_tenant`.

- Data staging depends on close.

- Generated stubs are editable; other generated files are regenerated.

- Keep Postgres autocommit false so `mdp_statement_timeout` applies to the invoke CTAS.

- `mdp_invoke` returns nine receipt columns; dev/ci/workbench, `--empty`, and `dry_run` read fixture receipts
  or a typed empty result without invoking HTTP.

## Cycles, freshness and schemas

### Bindings and freshness

[Cycles, cadence and close numbers](../docs/architecture.md#cycles-cadence-and-close-numbers)

- `mdp_bind_cycle` runs after bootstrap for run/build/seed/snapshot/test.

- Parsing, compilation, docs, listing and freshness do not bind.

- The hook returns SQL to the executor; it rejects production `dbt retry` using the original command because
  dbt rewrites the current command.

- Production context resolves exactly `DBT_CLOUD_RUN_ID` through raw cycle mirrors and fails with
  `no_cycle_binding` if absent.

- Core supplies that same variable for its own run identity.

- Current target views select the bound cycle; target history retains frozen revisions.

- Freshness runs between bronze and transform for declared sources read but not landed by that cadence/scope.

- Use the full Core job for retry, or `--cycle-id` for closed-cycle replay.

### Schema routing

[Tenant scoping and serving](../docs/architecture.md#tenant-scoping-and-serving)

- Schema routing checks target first: dev/ci retain the target prefix; workbench requires `wb_schema`;
  production global uses custom schemas; tenant jobs require `tenant_slug` and use `tenant_<slug>_<layer>`.

- The startup hook checks selected models before any DDL. `run` and `build` refuse a
  `scope:tenant` model unless `DBT_MDP_SCOPE=tenant:<id>`, including local dry runs.
  Full DuckDB fixture builds use `DBT_MDP_SCOPE=tenant:fixture`; schema prefixes still isolate them.

- Global refs remain global within tenant jobs.

- Tenant marts carry `tenant_scoped`; a shared tenant mart requires an enforced `tenant_id` contract.

- Tenant models sit in `staging/tenant`, `intermediate/tenant` and `marts/tenant`; root intermediates declare
  `scope:global` in their own config.

- Seeds are global and never rebuilt by tenant jobs.

### List coverage and tenant reads

[Playlist membership](../docs/architecture.md#playlist-membership)

- A list is `algotorial` when any snapshot of its stream, at any coverage, or its entry event has the observed
  algorithmic owner: an algotorial list larger than its embed is never complete.

- `list_type` follows, else the latest snapshot with a known owner.

- A tenant-bound function declares tenant relations without the slug (`tenant_marts.<model>`); its job
  compiles them to `tenant_<slug>_marts`, quoted, since a slug may hold a hyphen.

- Current membership takes each stream's window from the frozen cadence carried on its observations, so global
  and tenant copies select the same stream.

- Replay rebuilds in place against the replayed cycle.

### Calls and grades

[Weekly evidence and calls](../docs/architecture.md#showcase)

- A frozen call of a rule the rules seed no longer lists (one retired later) is graded too and counted as a
  warning, never an error, so the weekly job keeps building: `rule` is `accepted_values` over the seed's rules,
  which `lint-dbt.sh` holds to the seed. A `relationships` test to the seed would not do: a test whose parents
  span both scopes is selected by the global selectors too (dbt's eager indirect selection runs per intersection
  component), and `lint-dbt.sh` refuses one.

### Serving and workbench builds

[Tenant scoping and serving](../docs/architecture.md#tenant-scoping-and-serving)

- Build JSON with `mdp_json_agg`, `mdp_json_object` and `mdp_source_keys_agg`, never adapter SQL.

- Served marts are tables that declare `meta.grain` (not-null constraints); SDK generation writes
  `tests/grain/` uniqueness tests, and the data API pages in grain order under `marts._build`.

- `mdp_record_build()` is the one writer of `marts._build`. It stamps served marts with
  `meta.grain` and stored models with `meta.record_build: true`. The row names the physical
  relation, bound cycle, close number and build time, and commits with the table swap.
  Intermediate and mart folders install the post-hook. Staging tables install it explicitly. Adding `record_build` adds no API route.
  Stamping is skipped on dev, ci and workbench targets. Other targets stamp even empty or
  dry-run builds, so existing cursors become stale.
  Calls use stamped `int_song_key__daily`, `int_song_cluster__daily`,
  `int_cluster_entries__daily` and `int_playlist__snapshots`; refresh them through their daily build; see
  [the stamp macro](macros/mdp_annotate.sql).
  Reviewed counts also stamp `int_artist_identity`, `int_cluster_shazam__daily`,
  `int_song_windows__daily`, `stg_playlist__snapshots` and `stg_shazam__chart_entries`.
  Shazam staging is a daily table so its count stays tied to one build.
  Open [relation counts](../control/apps/showcase/server/relation-counts.ts) for the capture rules.

- Workbench Preview builds only the draft and reads existing permitted inputs through safe copies.
  Admin Backtest compiles supported upstream models into CTEs over frozen safe inputs.
  Membership comes from generated `explore_raw` copies. Raw access stays revoked.
  Unsupported historical inputs refuse with a Preview next step. Invokes and build stamps stay inert.
  Open `/explorer` to choose an input, then use its `ref()` in Preview.

- Scratch relations belong to the session's `wb_*` schema.

## Free-source rails

### Chart isolation

[Imports and source evidence](../docs/architecture.md#declarations-drive-everything)

- `stg_shazam__chart_entries` reads `raw.shazam_chart_entries` alone and feeds the served daily
  `mart_shazam_chart_daily` (chart × `chart_date` × position); Billboard keeps `raw.chart_entries`,
  `stg_billboard__chart_entries` and the daily `mart_chart_history`. Daily matching reads its own manifest through
  `stg_billboard__chart_entries_daily`; the weekly collection stays weekly.

- `int_billboard_song__daily` matches folded title and first credit to exactly one movement group.
  Ambiguous entries retain their text with no key. Open `billboard_match_rules` for switches.
  A debut needs provider weeks equal to one, a first group week, and complete current and preceding charts.

- A Shazam chart row reaches a recording only by its Apple song id through `int_track_identity__daily`, never by
  `artist_text`. `mart_shazam_chart_daily` maps country slugs to ISO codes through
  `seeds/shazam_markets.csv`; worldwide charts have no market. Read the mapping before
  adding a chart so a Shazam place and a playlist market count consistently.

### Wikipedia cycle isolation

[Imports and source evidence](../docs/architecture.md#declarations-drive-everything)

- The wiki staging reads the rows landed before the cycle closed (`manifest_filter(..., derived_rows=false)`)
  and has no edge to a wiki invoke, whose dependent is the operator table `int_wiki__receipts`.

- Inputs versioned per cycle read `mdp_cycle_day()` (the UTC day of the bound cycle's clock,
  `mdp_cycle_clock()`) or `mdp_cycle_week()` (its ISO Monday: a lookup made once a week inside the daily job);
  local builds take `var('cycle_opened_at')`.

## Enrichment and reconciliation

### Input identity

[Enrichment inputs and budgets](../docs/architecture.md#enrichment-inputs-and-budgets)

- `mdp_input_identity(key_cols, version_cols)` hashes typed JSON arrays into `input_ref` and `input_version`.

- Nulls and embedded separators remain distinct; rebuild inputs and rerun enrichment together after changing
  the encoding.

### Manifest and revision filters

[Cycles, cadence and close numbers](../docs/architecture.md#cycles-cadence-and-close-numbers)

- The filter reads the table's `raw.dump_stamps` rows.

- Any model after `bronze_close`, and any manifest or revision filter, fails with `cycle_not_closed` when its
  `stamp`-mode cycle has no `close_no`; invoke stubs (export, invoke, close), which never read the manifest in
  dbt, are exempt.

- A tenant model may read a global raw table only when the generated `mdp_global_inputs()` declares it for its
  cadence; `mdp sources export` derives that list from tenant models and lint fails an undeclared read.

- A tenant cycle's manifest reads the list frozen at its close (`raw.cycles.global_inputs`), so a Replay reads
  what it built.

- Target views expose frozen `resource_kind`, `canonical_key`, `params_json` and `taken_at`; history counts
  only revisions whose own cycle closed at or before the bound close (`mdp_revision_filter`).

- Export stubs pass the generated `mdp_export_kinds(cadence, scope)`, so an export freezes only the kinds its
  functions' `Targets` and its models' `meta.target_kinds` read.

### Enrichment staging

[Enrichment inputs and budgets](../docs/architecture.md#enrichment-inputs-and-budgets)

- Bootstrap also adds the gold runtime columns (`scope`, `step`, `run_admitted_at` and the `input_version`
  components), so staging never waits on a first landing.

- Enrichment staging dedupes on `(_source_key, scope, input_ref, input_version, step, config_version,
  output_key...)`.

## Reference seeds


## Song movement

### Song keys and daily facts

[Song movement](../docs/architecture.md#song-movement)

- `int_song_key__daily` gives each platform track (Apple, Spotify, and a Shazam-only Apple song) one strict
  song key from the daily identity inputs; `mart_song_aliases` keeps fallback links from an older key to the
  current one and survives a full refresh.

- `mart_song_day` rebuilds every key's facts for the bound UTC day from the manifest: list entries, Shazam
  charts and stream rates; an unobserved fact stays null, never zero.

- `int_song_entries__daily` proves a full-list add, a half-confidence head entry, or a Shazam entry after an
  observed absence (a first chart observation is a baseline); `int_song_windows__daily` sets each family's
  window from observed history; `int_song_movement__daily` scores the three families.

- Served: `mart_top_movers` and `mart_top_movers_current` (two positive families), `mart_early_signals_current`
  (one), `mart_arrivals_current` (entries, ranked, no score), `mart_readiness` (when each comparison can
  start, and stage coverage per list). Each row carries its `movement_list` and the evidence that built it.

- [Movement](../docs/movement.md) is the reader's guide to the lists, age proxies, stage and scoring; keep it
  true when a rule changes.

### Movement rules have one home

[Song movement](../docs/architecture.md#song-movement)

- Every number a movement model reads comes from a seed by name: `seeds/movement_parameters.csv` through
  `mdp_movement_parameter()`, `seeds/song_age_parameters.csv` through `mdp_song_age_parameter()`, the artist
  stage rules from `seeds/artist_stage_thresholds.csv`, list tiers and markets from `seeds/playlist_reach_tiers.csv`.
  A literal in a model is a bug; `tests/movement_parameters_complete.sql` fails a missing or non-positive row.

- `mdp_song_list_kind()` is the one reading of `list_kind`: it refines only a list the platform labels
  `editorial` or `chart`; a user or algorithmic list keeps its platform label, and `owner_class` is never
  rewritten.

- `song_age_parameters` rows carry `measured_on`; `tests/song_age_parameters_current` fails once the cycle year
  passes it, so the Apple id bands are remeasured before a new calendar year.

- Seeds that hold values an agent chose (`artist_stage_thresholds`, `movement_parameters`)
  carry a `reviewed_on` column, empty until a person reviews the row. Set the date when reviewing; clear it
  when the value changes.

### Movement groups are not identity

[Song movement](../docs/architecture.md#song-movement)

- `int_song_cluster__daily` joins strict keys into a movement group for scoring only: keys with the same
  observed ISRC always; `isrc_crosswalk`, `title_artist_duration`, `apple_variant` and `apple_id_successor` only while
  `seeds/song_cluster_rules.csv` switches them on, each with its measured precision (`cluster_confidence` is
  the lowest rule in the group). `apple_id_successor` and `apple_variant` are off in the seed.
  The representative is a resolved key first, then the key with the most facts.

- The `int_cluster_*__daily` models recount facts per group: a chart counts once, stream rates sum disjoint
  keys, a Shazam copy switch is not an arrival, age is the oldest known member, stage the most established.

- `mart_song_cluster_members` maps every strict key to its current representative, so a reader finds a song's
  score after copies are matched. `int_song_key__daily`, `int_track_identity__daily` and the aliases never
  change because of a group: a wrong merge costs a score, never a link.

- Change a switch only with the cluster audit evidence beside it; a rule needs a complete census and a Wilson
  lower bound at or above 95% precision to turn on.

### Artist catalog depth

[Song movement](../docs/architecture.md#song-movement)

- `int_song_artists__daily` lists each song's credited MusicBrainz artists (its recording credit, and each track's
  first platform artist through `int_artist_identity`); `int_artist_catalog__inputs` gives `mb_artist_catalog`
  each artist once per ISO week.

- `stg_mb__artist_catalog` reads readings landed before the cycle closed (`derived_rows=false`) and has no edge to
  the invoke, whose dependent is the operator table `int_artist_catalog__receipts`, so a failed lookup never
  skips movement.

- `int_artist_stage__daily` applies `seeds/artist_stage_thresholds.csv`, the one home of the rules (numbers and
  the release group kinds the established count reads), to each artist's newest lookup and only that lookup's
  release groups (`stg_mb__artist_release_groups`, joined on `input_version` and `config_version`).

- `int_song_age__daily` takes the song's most established credited artist and stays `unknown` while any credited
  artist has no lookup; `new_entries` ranks emerging artists first among equal scores (`mdp_song_stage_order`).

## Identity spine

### Reference generations

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- Reference rows come from `mb_spine` dump generations of the spine scoped to the tracked catalog, and from
  the rows `mb_resolve` answers touched in the newest generation (`raw.mb_resolve_closure`, read by
  `mdp_resolve_closure()`).

- The `mb_spine` invoke is universal and sits in the weekly transform; it reads the hourly
  `int_identity__track_inputs` off-job.

- A generation is current for a cycle only when its run completion reconciles in that cycle's manifest: the
  completion row and every dump it lists are visible, and each table's listed rows equal the rows read from
  the mirror (`mdp_reference_generations()`).

- Current rows are the newest reconciled generation's, then the answers' rows, plus tombstones for the
  previous reconciled generation's keys neither carries (`mdp_reference_rows()`).

### Reference query plans

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- The hourly chain computes the four tables it joins (ISRCs, URL links, recordings, redirects) in
  `int_reference__current`, a table rebuilt every hour, and reads it by key through its partial indexes, never
  through an ephemeral wrapper (Postgres materializes a CTE referenced more than once).

- Daily, weekly and tenant readers inline the macros over their own manifests.

- A table that inlines reference rows or builds track inputs takes `mdp_hash_join_plan()` as its pre-hook:
  Postgres guesses those CTEs at a few rows and would scan one once per row of another in nested loops.

### Reference extensions

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- The spine also holds the label extension (`mdp_reference_extension_tables()`: `l_artist_label`, `label`,
  `l_label_label`, `artist_ipi`, `artist_isni`), which lands with a generation only, so its reference rows
  never read `raw.mb_resolve_closure`.

- `int_label__ownership_closure` walks each label's current parents (`label ownership` and `imprint`, parent
  entity0) and renames (successor entity1) to a root owner, and leaves a step with two current parents
  unresolved; `int_artist__contract_edges` dates each artist-label affiliation at the precision MusicBrainz
  records, with the root owner and a sole current distributor, labelled "open data, not contract truth".

- `int_artist_identity.wikidata_qid` reads the artist's Wikidata URL relationship.

### Retention and manifest guards

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- `mdp_spine_visible()`, `mdp_resolve_closure()` and `mdp_enrichment_rows()` spell each manifest filter with
  its literal raw table.

- Raw keeps the newest two reconciled generations (`mb_spine`'s prepare step), so a Replay of a cycle whose
  generations were pruned reads the ones that remain.

### Incremental identity inputs

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- `int_identity__track_inputs` is incremental by track key: a build recomputes the keys touched by a dump
  stamped above its high-water mark in any joined input, from every manifest-visible row of those keys.

- It reads the new dumps, then only the touched keys' items and the page rows and snapshots of their
  observation groups, through the raw indexes the warehouse declares (`RAW_INDEXES`), before any window, so an
  hour costs what its new dumps touch; only a full build takes `mdp_hash_join_plan()`.

- A build whose manifest reads a generation retention deleted (one of the newest two it reconciles) fails at
  compile with `reference_generation_incomplete` (`mdp_reference_guard()`), and the runner asks the service to
  open that alert for the run.

### Priority and resolution ordering

[Identity spine and resolution methods](../docs/architecture.md#identity-spine-and-resolution-methods)

- Priority (`int_identity__priority_tracks`, ephemeral) is computed on every build from curated owner classes
  and the tenant `artist_page` revisions `cycle.tenant_close_nos` counts; it selects inputs and never enters a
  version.

- `reference_version` hashes the content of every reference row a track joins, tombstones included, so a
  reload of unchanged rows keeps it.

- The identity pick orders manifest-visible resolutions by current fields, equal reference, newest
  `retry_week`, producing `close_no`, then landing order; it is the dedupe for the two enrichment tables, so a
  replay's late copy never displaces a newer cycle's.

- The daily copy (`int_track_identity__daily`) feeds events, editorial entries and playlist followers.

- Cycle-bound behaviour is proven on Postgres by `functions/tests/test_identity_spine.py`.

- See [macro reference](macros/README.md) and [acceptance](../ops/ci/README.md).
