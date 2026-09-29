# Read holdings and events

Open `/ops/platform` to read the past week.
For a CLI read, pass a UTC time within the last 90 days as `--since`:
`pnpm --dir control mdp platform holdings --since 2026-09-20T00:00:00Z`.
Choose a recent date when using this example. Older or future starts are refused with a recovery link.
Both reads require an admin identity.

Holdings keeps collected rows and saved inventory separate:

- `ingestion.rows` counts committed output rows by source and UTC load day.
  It counts only scheduled runs loaded to the production warehouse.
  Manual, backfill, test and fixture runs are excluded. Metadata outputs are excluded.
- `ingestion.summary` returns SQL totals, positive source counts and daily totals from the same statement.
  `today_measured` distinguishes a recorded zero from a day with no receipts. Open `/runs` for receipts.
- `inventory_history.days` reads saved snapshots. Missing days are `unavailable`.
  `first_snapshot_day` and `unavailable_before` are null until the first snapshot.
  Snapshot writers use the production warehouse UUID in the `warehouse` column.

Current inventory belongs to the showcase app. Its live view and 00:30 UTC snapshot call
`readInventory(warehouse())` from `control/apps/showcase/server/inventory.ts`.
The caller supplies its `showcase_wh` connection. Control-api has no warehouse credential.
The shared query reads table counts and storage sizes from allowed warehouse and tenant layers.
Views, partition parents, scratch tables, metadata and dbt swap tables are excluded.
Catalog row estimates cannot exclude synthetic identities or prove downstream filtering.
All layers return `rows_est: null` and `complete: false`, including saved historical snapshots.
Stored snapshots and raw rows remain intact. Holdings suppresses old API estimates too.
Use a reviewed, filtered relation for a genuine row count.
A failed read throws, so the snapshot caller can raise its inventory alert.
Open `/runbooks/showcase-inventory-failed` to check missing snapshots.

All platform and lineage reads use read-only transactions.
Each statement has a two-second deadline and a 500 ms lock wait limit.
If a read reaches its limit, open `/ops` and retry when the runner is idle.

`vendor_cost` reads current cost ledger rows in the requested time window.
Each positive microcent value takes precedence over cents. Otherwise cents are converted
into microcents. The sum is rounded once to cents. Daily totals use the same rule separately.
The total and daily breakdown use one SQL statement, so a reconciliation cannot split their view.
Daily rounding can make the sum of displayed daily amounts differ from the rounded total.
The label is `estimate`, `reconciled` or `partly reconciled`, based on the contributing origins.
An empty window returns zero, `estimate` and `has_current_rows: false`. The showcase hides cost per
thousand rows until current cost rows exist. Infrastructure cost is excluded.
Reconciliation keeps the original usage time. Open `/runs` to inspect the load receipts.

Run `pnpm --dir control mdp platform events --limit 100`.
Pass `next_cursor` as `after` on the next API call, or as `--after` in the CLI.
Treat the cursor as an opaque string. Do not build or edit it.
The read uses the cycle, run and alert tables directly. It writes nothing.
It reads cycle opens and closes, run admissions and settlements, and alert opens and resolutions.
Settlements use the run's `updated_at`, even when the run started long ago.
Each event has a stable `key`, such as `run_settled:<run UUID>`, and an `occurred_at` time.
Events sort by time, then key. Each event family uses a time/key index and reads at most `limit + 1`
candidates before the merge. Cycle flags use the shared scheduled-cycle rule.

The cursor keeps the newest event time returned so far.
When `has_more` is true, pass the cursor to finish the remaining pages in order.
That continuation also keeps the last time and key, so equal timestamps do not skip events.
Once `has_more` is false, the next poll starts 120 seconds before the newest event time.
For example, a poll ending at 12:02 reads again from 12:00.
A settlement stamped 12:01 that commits after the first poll appears in that overlap.
Commits older than the overlap can be missed. This feed shows recent activity; it is not an audit log.
An empty window keeps the newest time. An empty first read keeps an unset time and reads all rows next time.
Run the same command with `--after '<next_cursor>'` to continue.

Readers filter repeated keys before showing or sending events.
The showcase helper `createPlatformEventReader` in `control/apps/showcase/server/platform-events.ts`
retains up to 10,000 keys that can still appear in the overlap. Create one reader per poller with
`createPlatformEventReader(client.platform.events)`, then await each page before reading another.
Drain pages while `has_more` is true, even when filtering leaves a page empty.
The API returns `overlap_start`, the earliest time the next polling window can replay.
The reader releases a key only after that bound passes its event time.
If the retained set would exceed 10,000 keys, it raises `platform_events_capacity`.
That page advances neither its cursor nor its key set. Stop polling and follow the error's next step.
A restart clears memory, so already shown events can appear again after a restart.
Pass the returned cursor unchanged when implementing another reader; open `/ops/platform` to inspect a page.

The `runner` field reads Core lock-holder sessions through `pg_stat_activity`.
A holder stays busy through transforms, even after the cycle closes.
An unreadable or malformed state returns `state: unknown` and `busy: true`.
`next_scheduled_at` is the earliest eligible global Core run from `control.dbt_job` and scheduled cycle
credit. It uses the declared timezone, due hour and weekday, the hourly 45-minute credit window,
and the two-failure limit. It is null when no eligible schedule is registered or runner state is unknown.
Host ticks can start later. Use cached heavy reads while `busy` is true. Open `/ops` to follow the runner.

For `lineage.chain`, pass a dump, request, cycle or run ID and a `limit` up to 100.
The response returns `next` with separate dump, receipt and request cursors.
Pass that object as `after` with the same identity. A null cursor marks a finished list;
an omitted cursor starts that list. Stop when all three are null.
Cycle lineage keeps explicit inputs, derived outputs and the dumps stamped by that close.
Open `/runs` to inspect the producing runs.


## Sources

Run `pnpm --dir control mdp platform sources`, read `GET /api/platform/sources`,
or open the Sources card on `/ops/platform`. These reads require the admin role.
Each row describes an enabled source or a source with a production load in the last 14 UTC days.
`source_key` identifies the source. `display_name`, `brand`, `family` and `description` use the
shared source wording. Unknown sources use the registry provider or source key, no brand,
and the description “Collected by MDP.” Their family is `derived` for silver, gold and
universal sources, or `other` for bronze. `cadence` and `enabled` read the source settings.

`entries_today` counts output rows loaded since 00:00 UTC today.
`days` contains today and the previous 13 UTC days, oldest first, with zero for days without loads.
Both counts are decimal strings. They use the same scheduled production rules as holdings:
test, fixture, manual, backfill and canary work, fixture settings, other warehouses,
input dumps, unfinished loads and metadata tables do not count.
`first_collected` walks the source's runs from oldest to newest by creation time.
`last_read` walks them from newest to oldest. Each walk uses `run_streamline_created_idx`
and stops at the first scheduled production run with a matching loaded output dump.
The fields return that run's earliest and latest matching load times, respectively, in UTC.
Loads after `queried_at` do not count. A source with no matching load returns null.
Each candidate run reads only its own dumps and loads; neither walk aggregates the full history.
`targets` is the frozen member count referenced by the latest scheduled production run that
has a target revision. It walks the same run index backward and stops at the first match.
It is null when no such run exists; it does not count live targets.
Rows sort by today's count, highest first, then display name. `queried_at` marks the read time.
If the sources read fails, the Sources card says it is unavailable and links to `/functions`.
The other cards still render. Open `/functions` to see each source's runs.
