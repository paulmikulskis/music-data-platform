# Architecture

Python functions collect records. dbt turns them into SQL tables. The control API manages
work, and the data API serves tables with declared columns and keys.
Start with the [glossary](glossary.md) for terms and [developer guide](DEVELOPING.md) for commands.


## Cycles, cadence and close numbers

A cadence says when work is due: hourly, daily or weekly. A scope says whose work it is:
global or one tenant. A cycle binds one cadence and scope to frozen inputs.
A retry uses the same inputs and target list.

The runner builds export and bronze invoke tables, checks source freshness, then builds
transforms. Export freezes target membership and specs before collection starts.
Close depends on export and every bronze invoke in that cadence and scope.
Staging reads after close. Selectors never expand across cadences with `+`;
a cross-cadence reference reads an already-built table.

Close locks the scope's `control.scope_close` row and allocates `last_close_no + 1`.
It stamps unstamped output dumps loaded to their runs' pinned warehouse and commits under
that lock. Numbers therefore follow commit order across all cadences of that scope.
The warehouse catch-up copies stamps and closed cycles in one transaction, then advances
`mirrored_close_no`. Close returns successfully only once its number is mirrored.

`mdp_context()` finds the bound cycle through `DBT_CLOUD_RUN_ID`; production fails when
that binding is absent. `manifest_filter` applies the cycle to each raw table before
deduplication. A stamp-mode cycle without a close cannot feed transforms.
Full retries reuse work keys with fresh attempts. Global Replay rebuilds a closed cycle,
then restores the current build. Tenant Replay is unavailable; tenant builds use Retry.
A source absent from an older frozen cycle completes as `not_in_cycle` on restore or retry.

code: [runner](../ops/run.sh), [cycles and mirror catch-up](../functions/src/mdp_functions/cycles.py),
[context](../dbt/macros/mdp_context.sql), [selectors](../dbt/selectors.yml).


## Layers and builder classes

A layer describes both the work a function does and the table it writes.
`@bronze` fetches outside data. `@silver` reshapes declared warehouse inputs without network
access. `@gold` enriches declared inputs with budgeted external services or model steps.
`@universal` declares its reads, writes and whether it needs external access.
Silver runs in a separate process whose hooks block network access.

Python shapes individual records. SQL owns joins and aggregates, so the dependency graph
shows where records meet. Derived functions read dbt relations from staging upward;
registration refuses direct `raw.*` reads.

In dbt, staging casts and deduplicates raw records without cross-source joins.
Intermediate models join them. Global marts serve public observations; gold functions enrich declared inputs.
A mart contract declares its columns and portable types before SQL is written.
An enforced contract makes a changed output shape fail the build.

code: [builders and context](../functions/src/mdp_functions/layers.py),
[declaration validation](../functions/src/mdp_functions/registry.py),
[silver worker](../functions/src/mdp_functions/silver_worker.py), [dbt layout](../dbt/dbt_project.yml).


## Declarations drive everything

Each function's decorator is its declaration: source key, output tables, schema, record key,
cadence, targets and any warehouse reads. The exporter discovers those declarations.
This keeps the collector, raw table and dbt entry point from describing different data.

`uv run --project functions mdp sources export` renders raw source YAML, per-dump uniqueness
tests, local bootstrap DDL, registry seed rows and export/invoke/close SQL stubs.
It also derives the target kinds each export freezes and the global tables tenant jobs read.
Owner classification rules come from the same Python rules used by collectors.
Export preserves existing source tables; retiring a writer does not erase its landed data.

Generated files are regenerated. Export/invoke/close stubs are the editable exceptions:
they live in `dbt/models/bronze` and make ordering visible through `depends_on`.
The deployed bootstrap creates declared raw tables and columns before transforms run.
Drizzle owns control DDL; dbt owns transformed relations; function declarations own raw DDL.
The source catalog combines declaration facts with the human notes in `annotations.csv`.

code: [exporter](../functions/src/mdp_functions/exporter.py), [templates](../functions/src/mdp_functions/templates),
[bootstrap](../ops/fly/bootstrap.py), [catalog notes](sources/annotations.csv).


## Runtime accounting and recovery

A function observes each input record, then yields it or calls `ctx.reject()` with a reason.
`ctx.exclude()` records known non-error classes, such as playlist episodes or radio air breaks.
The source declares exact `exclusion_reasons`; the runtime refuses every other exclusion reason.
Unknown kinds and missing fields are rejections. Inspect the receipts' `row_exclusions` to see
expected filters separately from errors.
For each published page, observed equals yielded plus rejected plus excluded.
Exclusions do not lower row coverage. A completed target with filtered rows but no validated
output opens a warning beside request health on the target and function pages. It does not
fail the run or park the target. Open its producing run and follow the
[runbook](../ops/runbooks/target_zero_yield.md).
Unknown fields survive in `_extra`; invalid records have a typed rejection.
This makes a changed provider response visible instead of silently losing rows.

The runtime supplies traced HTTP, budgets, retries, leases and lineage.
A page ends before the next request or when the target or function finishes.
Its outputs and cursor changes register together, after its dump manifest is published.
Warehouse load receipts fence duplicate inserts; recovery resumes pending loads and mirrors.
A completion row counts only when its listed dumps and row counts reconcile.
Recovery revisits unfinished work and terminal runs with outstanding receipts.
See [recovery rules](../functions/CLAUDE.md#target-failures-and-recovery) for eligibility and
[alert writes](../functions/CLAUDE.md#checkpoints) for the once-per-attempt boundary.

Provider failures can reject one target or stop a resumable batch, depending on the error.
The transport refuses forbidden endpoints even when a function declares their host.
It keeps no cookies. Requests, dumps and cycles have ids that connect logs to landed rows.
Local fixture runs use the same export → function → close order with recorded responses;
fixture evidence does not establish live provider or integration acceptance.

code: [context accounting](../functions/src/mdp_functions/layers.py),
[dump manifest](../functions/src/mdp_functions/manifest.py),
[transport refusals](../functions/src/mdp_functions/fetch/forbidden.py),
[service contract](../functions/openapi/service.json), [fixture commands](../functions/CLAUDE.md#fixture-loop).


## Rights annotation and learning_gate

Serving a row and using it for learning are separate decisions.
Every served mart passes its contributing `_source_keys` and input flags to `mdp_annotate()`.
The macro attaches `source_keys`, `learning_eligible` and `resale_permitted` without removing
rows. A missing registry key or false input flag cannot grant eligibility.
Source keys describe all contributing data, including evidence used to infer an event.

Derivative consumers use `learning_gate()` to keep only eligible rows; unknown means false.
Tenant material never trains: tenant marts annotate learning false, and the gate refuses
tenant relations at compile time. A permitted global projection carries no tenant key.
The static lineage check verifies that served marts name their upstream raw writers;
it does not approve collection or grant provider permission.

Observed owner evidence controls playlist privacy. Only platform-owned accounts keep
descriptions, owner ids and names in serving rows. Other owner identifiers are pseudonymous
join keys, and owner names are removed. A frozen target label cannot turn a private owner public.
Functions retaining authored text declare `retain_days`; retention removes raw rows and
stored copies, including derived input copies, while frozen counts can remain.

code: [annotation](../dbt/macros/mdp_annotate.sql), [learning gate](../dbt/macros/learning_gate.sql),
[owner rules](../functions/src/mdp_functions/owners.py), [retention](../functions/src/mdp_functions/retention.py),
[review checks](../ops/ci/review_gate.py).


## Playlist membership

A snapshot records what a collector sees at one time. Its stream distinguishes a full list
from a head window; its variant distinguishes a market or other version of the list.
An occurrence key distinguishes repeated copies of the same track.
Membership is computed separately for each platform, playlist, variant, stream and occurrence.

A partial observation proves presence only. A full observation can also prove absence.
An unchanged response can reuse matching earlier content; it does not invent missing items.
Observations tied at the same time cannot establish order and count as partial for membership.
An algorithmic list larger than its embed does not become a complete list.

An island is a run of observed presence with no full absence between its rows.
A full absence closes it; the next presence starts another island.
The opening snapshot identifies the island, so late data does not renumber other islands.
SQL builds islands from observed rows instead of a grid of every snapshot and possible item.

| Event | What the evidence proves |
|---|---|
| `baseline` | The occurrence is first seen in the stream's first full observation; its add time is unknown. |
| `add` | A full absence precedes presence; the add lies between `entered_after` and `first_observed_at`. |
| `entry_unknown` | Presence has no earlier full absence and does not qualify as a baseline. |
| `remove` | The island has full presence followed by full absence; removal lies between `removed_after` and `removed_by`. |
| `entered_head` / `left_head` | The occurrence enters or leaves the visible head, without proof about the whole list. |
| `move` | Complete, ordered observations show a position change and a changed snapshot hash. |

A head transition becomes an add or remove only when both bounds prove the full extent.
For example, full `[A]`, partial `[B]`, full `[B]` does not remove A at the partial read;
the final full read proves its absence. B's presence has an earlier full absence bound.
Current membership chooses a stream using the bound cycle's clock and the observations'
frozen cadence, so replay does not substitute wall-clock freshness.

code: [shared playlist SQL](../dbt/macros/mdp_playlist.sql),
[membership model](../dbt/models/intermediate/int_playlist__membership.sql),
[served events](../dbt/models/marts/global/mart_playlist_events.sql).


## Identity spine and resolution methods

A platform track id names a track on one platform. An ISRC names a recording identifier;
a MusicBrainz recording id lets the platform connect recordings across catalogs.
The spine contains only reference rows reachable from the tracked catalog, including artist,
release, URL and label relationships. dbt reads landed snapshots, never the moving mirror.

`mb_spine` reads one validated mirror generation in a consistent database snapshot.
A generation becomes usable only when its completion and every listed dump are visible
in the consuming cycle and their counts reconcile. Lookup closure rows fill keys the generation
lacks. Tombstones represent removed keys. Raw retention keeps two reconciled generations;
a build needing pruned data fails until the required generation is repaired.

SQL chooses an ISRC and a recording separately, in this precedence order:

1. Platform-reported ISRC, with a unique reference recording when one exists.
2. A landed MusicBrainz platform URL relationship.
3. `mb_resolve`'s album URL and release match (`mb_release`).
4. A crosswalk ISRC at its platform's confidence floor, with a unique recording when available.
5. A title, artist and duration match (`mb_trigram`) at its confidence floor.

Ambiguity stays unresolved. Higher-precedence answers win; disagreements remain in the audit.
Method, confidence and evidence travel with the result. Spotify's crosswalk floor of 1.01
deliberately withholds that method; lowering it requires new precision evidence.

Hourly inputs update touched track keys. Priority selects lookup work without changing its
version. Resolution selection prefers matching track fields, then reference content, retry
week, producing close number and landing order. Daily and tenant consumers resolve from
their own manifests, so later hourly answers do not rewrite a daily cycle's identity.

code: [identity SQL](../dbt/macros/mdp_identity.sql), [reference SQL](../dbt/macros/mdp_reference.sql),
[mirror lookups](../functions/src/mdp_functions/musicbrainz.py),
[confidence floors](../dbt/seeds/identity_confidence_floors.csv),
[daily identity](../dbt/models/intermediate/int_track_identity__daily.sql).


## Tenant scoping and serving

Global is the default scope. Tenant work binds one tenant's target revisions and builds
`tenant_<slug>_<layer>` schemas. Global references remain global in a tenant job.
Declared global inputs and their close number freeze at tenant close.
Tenant copies call shared playlist SQL, so both scopes use the same membership rules.

Served marts have enforced contracts and a `meta.grain`: the columns that identify one row.
SDK generation supplies typed reads and grain tests. `mdp_record_build()` is the one writer
of build stamps in `marts._build`. A stamp commits with the table swap and records its physical
relation, bound cycle, close number and build time. Stored models with `meta.record_build: true`
receive stamps too, without becoming served API routes. Dev, ci and workbench targets skip stamps.
The API orders pages by grain and rejects cursors from a different build.
See [build rules](../dbt/CLAUDE.md#serving-and-workbench-builds) before adding a stamped model.
The authenticated key chooses tenant identity, schema and tenant filters.
Request parameters cannot choose another tenant. `tenant_readable: false` refuses tenant keys.
An operator-only mart without a grain is outside the generated serving API.


## Enrichment inputs and budgets

Derived work uses `input_ref` for the logical input and `input_version` for the facts that
change its answer. Typed JSON hashing keeps nulls and embedded separators distinct.
Gold configuration is the declared version and parameters, frozen at admission;
a deployment image change alone does not invalidate completed work.

Completion requires the input's outputs to land and reconcile in the consuming manifest.
Reads skip valid completions before snapshotting. Budgeted work processes ordered inputs
and can end partial, leaving later work for another run. Failed inputs have no completion;
repeated individual failures may park an input until an operator releases it.
Provider request caps and LLM reservations stop new calls before dispatch.


## Song movement

A song key names one platform track in the daily identity. `mart_song_day` holds each key's
facts for one UTC day: list entries, Shazam charts and stream rates, null where nothing was
observed. Movement compares a window of observed days and scores three independent families:
playlist adds with follower reach, Shazam spread and stream growth. A mover is positive in two
families; an early signal in one; an arrival entered a list or chart and carries no score.
Each song sits in one movement list (`new_entries`, `established_entries`, `catalog_entries` or
`unplaced`) by release-age proxy and artist stage. People set every rule and weight in seeds;
nothing is learned.

A movement group joins copies of one song for scoring only: the same observed ISRC always,
and switched merge rules with measured precision. The group's representative carries the score,
and `mart_song_cluster_members` maps every strict key to it. Identity keys never change because
of a group, so a wrong merge costs a score, never a link.
`apple_id_successor` and `apple_variant` are off in the cluster seed.
Shazam country slugs map to ISO market codes for movement; worldwide charts have no market.
The disabled-by-default `apple_song_duration` collector can fill missing movement durations
from exact Apple ids. Bootstrap seeds its empty global track set before membership freezes.
See [duration collection](../functions/CLAUDE.md#apple-song-durations) for activation steps.
The song backtest replays these rules over closed daily cycles and scores later outcomes.
It measures fixed rules; searching weights for a better score would make the work a
derivative consumer.

code: [movement rules](movement.md), [song facts](../dbt/models/marts/global/mart_song_day.sql),
[movement groups](../dbt/models/intermediate/int_song_cluster__daily.sql),
[parameters](../dbt/seeds/movement_parameters.csv), [backtest](../ops/backtest/README.md).


## Showcase

The showcase is a separate Next.js app, deployed as `mdp-showcase`, for authenticated viewers.
Each person is a runtime entry with their own admin key. A signed link, minted through the
control API's `showcase.link`, opens a session that ends after idle and total day limits.
The app reads served marts through the data API with a reader key, reads the warehouse as
`showcase_wh` (the same reviewed projections as a staff explorer), and keeps links, sessions,
actors, shares, calls, drafts and rules in control tables through `control_rt`. It proxies the console with the
person's server-side key, so a viewer reaches the operator console without a database tunnel.

A call is a person's or approved rule's pick of a song. It freezes the song key, the places it was seen, and a
snapshot of the facts at that close number. Later chart days are compared against those frozen
places; a group's representative can change without moving a call. Each displayed metric identifies its inputs, and a source line says what was read and when.
Human submissions and rule picks use different validation paths.
Open [calls](../control/CLAUDE.md#calls) for those checks and
[drafts and rules](../control/CLAUDE.md#drafts-and-rules) for the weekly tray and closing behavior.

The app limits concurrent reads and delays heavy refreshes while runners work.
Check the [read budget](../control/CLAUDE.md#read-budget) and
[query inventory](../ops/showcase/queries.json) before adding a query. See [platform reads](platform-reads.md) for the public source boundaries.

code: [showcase guide](../control/apps/showcase/README.md),
[link and call tables](../control/packages/control-db/src/schema/showcase.ts),
[link RPC](../control/apps/control-api/src/router.showcase.ts),
[warehouse role](../control/packages/control-db/sql/showcase-role.sql).


## Deploy order

The full deploy coordinates runner, service, mart and API changes in one invocation.
App subsets do less work. Follow [deploy](operating.md#deploy) for app selection,
release checks, restore rebuilds, secret imports and recovery.
Scheduled builds also report heartbeat results. Open [daily status](operating.md#daily-status)
for the operator view and public health summary.

code: [deploy script](../ops/deploy.sh), [bootstrap](../ops/fly/bootstrap.py),
[release check](../ops/fly/check-release.py),
[runner guide](../ops/fly/core-runner/README.md), [operations guide](../ops/CLAUDE.md#fly-deployment-and-storage).
