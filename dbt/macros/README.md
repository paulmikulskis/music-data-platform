# dbt macros

| Macro | Current behavior |
|---|---|
| `bootstrap_raw()` | Generated empty raw tables for dev/ci; runtime owns production raw DDL |
| `mdp_context()` | Cycle, cadence, scope, revision lookup and manifest filter; exact production DBT_CLOUD_RUN_ID binding or no_cycle_binding; cycle_not_closed for a `stamp` cycle without `close_no` unless called by `mdp_invoke` |
| `mdp_manifest_filter(column, table)` | Semi-join to the bound cycle's `mdp_manifest_sql`; raises `manifest_table_required` on every target when `table` is missing; Workbench Preview reads served copies and admin Backtest reads inputs already filtered by cycle; unbound local/guarded builds return true |
| `mdp_manifest_sql(cycle, table, derived_rows=true)` | `list`: the cycle's `raw.cycle_inputs` rows. `stamp`: the table's stamps of the cycle's scope through `close_no`, declared global stamps through `global_close_no`, plus derived rows unless `derived_rows=false` (rows landed before the close only) |
| `mdp_global_inputs(cadence)` / `mdp_global_tables()` | Generated: global raw tables tenant models read per cadence; raw tables global functions write |
| `mdp_revision_filter(column)` / `mdp_revision_sql(cycle, column)` | Target revisions whose own cycle closed at or before the bound close (tenant: global through `global_close_no`) |
| `mdp_revision(target_set_id)` | Frozen target revision for the context's cycle and target set |
| `mdp_invoke(source_key, target_set=none, input_relation=none, target_kinds=none)` | Nine-column UDF receipts; fixture receipts or typed empty output under local/empty/dry-run guards; export stubs pass `target_kinds` |
| `mdp_export_kinds(cadence, scope)` | Generated: the target kinds a cadence and scope export freezes, from function `Targets` and model `meta.target_kinds` |
| `mdp_bind_cycle()` | Returns binding SQL after bootstrap for run/build/seed/snapshot/test; skips read-only commands and refuses production dbt retry |
| `mdp_timeout(source_key)` / `mdp_timeout_seconds(source_key)` | Timeout from raw.streamlines, default 900 seconds; Workbench skips the raw lookup; must exceed the 30-second UDF margin |
| `mdp_statement_timeout(source_key)` | Transactional Postgres SET LOCAL before CTAS; empty on DuckDB and Workbench |
| `mdp_literal(value)` / `mdp_is_local()` / `mdp_cadence()` | SQL quoting, invocation guards and model cadence resolution |
| `generate_schema_name(custom_schema_name, node)` | Local prefix, required workbench schema, production global or tenant routing |
| `learning_gate(relation)` | Derivative-only filter; unknown eligibility is false |
| `mdp_annotate(rows, source_keys, learning_inputs, resale_inputs)` | Served-mart select of the contract columns plus `learning_eligible`/`resale_permitted` (registry AND over every contributing key, unknown key false, AND input flags) and `source_keys` |
| `mdp_record_build()` / `mdp_build_table()` | Served-mart post-hook upserting `marts._build(relation, cycle_id, close_no, built_at)` inside the model transaction; on-run-start creates the table; guarded like the bind hook |
| `mdp_playlist_*()`, `mdp_membership_current()` | Shared playlist bodies (item/snapshot staging, track page artist ids, snapshots, observations, membership, events, source keys, current membership) for global models and tenant copies |
| `mdp_json_text/value/elements()` | Portable JSON field access and array rows for Postgres, DuckDB and Snowflake |
| `mdp_list_elements(list, alias)` | Rows of a `;`-joined text list (a seed's key list) for Postgres, DuckDB and Snowflake; callers drop empty items |
| `fold_by_priority(relation, entity, field)` | Field winner ordered by source priority, source key and value |
| `mdp_input_identity(key_cols, version_cols)` | input_ref/input_version hashes via mdp_identity_hash |
| `logical_unique(columns)` (test) | One staged row per logical key after the manifest filter and dedupe |
| `mdp_reference_generations()` | mb_spine generations whose completion reconciles in the cycle manifest, newest ranked 1 |
| `mdp_reference_rows(mb_table, generations)` / `mdp_reference_current(generations, tables)` | Current MusicBrainz rows of the newest reconciled generation, then the rows mb_resolve answers touched in it, tombstones of the previous one, content md5 |
| `mdp_resolve_closure(generations)` | The spine rows manifest-visible mb_resolve answers landed in `raw.mb_resolve_closure`, for the newest reconciled generation |
| `mdp_reference_indexes()` | Partial indexes for `int_reference__current`'s keyed reads |
| `mdp_hash_join_plan()` | Pre-hook: hash joins and no JIT for a table built from CTEs Postgres guesses at a few rows (track inputs, inlined reference rows) |
| `mdp_track_inputs(touched)` | One row per observed track with 22.3 surface enrichment and hydration; latest value per field; `fields_hash` |
| `mdp_track_exact(inputs, reference)` | SQL resolution by platform ISRC and platform URL; `reference_version` |
| `mdp_track_identity(inputs, exact, reference, resolutions, crosswalk, audit)` | The identity pick and method precedence; `audit=true` returns overruled disagreements |
| `mdp_retry_week(input_ref, closed_at)` | ISO week of the bound close minus hash(input_ref) mod 168 hours |

Input identity uses typed JSON arrays, preserving nulls and boundaries. Its encoding is
part of enrichment identity: rebuild inputs and rerun enrichment together after changing it.
Staging applies manifest filters before deduplication; target history retains all revisions.
Keep pg/pg_local autocommit false. The retry guard checks the original invocation command
because dbt rewrites the current command. See [conventions](../CLAUDE.md) for selector counts
and lint evidence for graph/binding checks.
