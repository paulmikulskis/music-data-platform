# Runbooks

Each guide lists the symptom, first query, recovery steps and when to escalate.

| Error class | Guide |
|---|---|
| `accounting_mismatch` | [accounting_mismatch](accounting_mismatch.md) |
| `cadence_failed` | [cadence_failed](cadence_failed.md) |
| `control_api_unavailable` | [control_api_unavailable](control_api_unavailable.md) |
| `control_db_unavailable` | [control_db_unavailable](control_db_unavailable.md) |
| `cost_cap_hit` | [cost_cap_hit](cost_cap_hit.md) |
| `cost_cap_soft` | [cost_cap_hit](cost_cap_hit.md) (the soft threshold of a provider request cap) |
| `cycle_not_closed` | [cycle_not_closed](cycle_not_closed.md) |
| `cycle_not_found` | [cycle_not_found](cycle_not_found.md) |
| `dbt_api_unavailable` | [dbt_api_unavailable](dbt_api_unavailable.md) |
| `dbt_failure` | [dbt_failure](dbt_failure.md) |
| `dump_late` | [dump_late](dump_late.md) |
| `dump_unreadable` | [dump_unreadable](dump_unreadable.md) |
| `duplicate_invocation` | [duplicate_invocation](duplicate_invocation.md) |
| `forbidden` | [Console access](forbidden.md) |
| `forbidden_path` | [forbidden_path](forbidden_path.md) |
| `freshness_stale` | [freshness_stale](freshness_stale.md) |
| `inputs_parked` | [inputs_parked](inputs_parked.md) |
| `invoke_timeout` | [invoke_timeout](invoke_timeout.md) |
| `litellm_unavailable` | [litellm_unavailable](litellm_unavailable.md) |
| `llm_budget_exceeded` | [llm_budget_exceeded](llm_budget_exceeded.md) |
| `object_store_unavailable` | [object_store_unavailable](object_store_unavailable.md) |
| `partial_coverage` | [partial_coverage](partial_coverage.md) |
| `reference_disk_high` | [reference_disk_high](reference_disk_high.md) |
| `reference_dump_stale` | [reference_dump_stale](reference_dump_stale.md) |
| `reference_generation_incomplete` | [reference_generation_incomplete](reference_generation_incomplete.md) |
| `reference_import_failed` | [reference_import_failed](reference_import_failed.md) |
| `runner_outdated` | [runner_outdated](runner_outdated.md) |
| `scheduled_only_refused` | [scheduled_only_refused](scheduled_only_refused.md) |
| `schema_breaking` | [schema_breaking](schema_breaking.md) |
| `schema_drift` | [schema_drift](schema_drift.md) |
| `scope_mismatch` | [scope_mismatch](scope_mismatch.md) |
| `scrape_blocked` | [scrape_blocked](scrape_blocked.md) |
| `service_unreachable` | [service_unreachable](service_unreachable.md) |
| `source_unregistered` | [source_unregistered](source_unregistered.md) |
| `showcase_inventory_failed` | [showcase_inventory_failed](showcase_inventory_failed.md) |
| `source_canary_failed` | [source_canary_failed](source_canary_failed.md) |
| `spine_narrowing_due` | [spine_narrowing_due](spine_narrowing_due.md) |
| `target_zero_yield` | [Target without output](target_zero_yield.md) |
| `undeclared_exclusion` | [Undeclared exclusion](undeclared_exclusion.md) |
| `stale_target` | [stale_target](stale_target.md) |
| `surface_drift` | [surface_drift](surface_drift.md) |
| `vendor_4xx` | [vendor_4xx](vendor_4xx.md) |
| `vendor_retryable` | [vendor_retryable](vendor_retryable.md) |
| `warehouse_disk_high` | [warehouse_disk_high](warehouse_disk_high.md) |
| `warehouse_unavailable` | [warehouse_unavailable](warehouse_unavailable.md) |

Seed all Markdown guides with `pnpm --dir control --filter @mdp/control-api seed-runbooks`
using MDP_CONTROL_RT_URL. `uv run --project functions mdp control runbooks` installs missing
runtime catalog entries without replacing existing content. File stems map to hyphenated
database slugs; runtime roles reference guides but do not own their configuration.

No warehouse-swap guide exists in this directory; migration is documented in
[functions conventions](../../functions/CLAUDE.md#derived-work-backfill-and-migration).

[Held runners](runners_held.md) explains deploy markers, takeover and the outside cadence watcher.
Open it when `/ops` reports an overdue close.
