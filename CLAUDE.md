# Music Data Platform conventions

Start with the [glossary](docs/glossary.md) and [architecture](docs/architecture.md).

## Where things are

- `README.md` is the visual overview; `docs/DEVELOPING.md` has verified layout, local commands, and
  `docs/operating.md` has day-two steps.
- `ops/readme/cards.py` regenerates the README art; SVGs under `docs/images/` are generated, so change the script, not the file.
- `docs/sources.md` is the generated source catalog; judgment and planned rows live in `docs/sources/annotations.csv`.
- `dbt/` owns SQL models, contracts, seeds, sources, macros, profiles, and the dbt environment.
- `functions/` contains the Python package and version CLI; runtime and workbench follow their local guide.
- `r/` owns the `mdpr` R package, its tests and analysis templates; start with `r/CLAUDE.md`.
- `analyses/` holds analyst projects under `<handle>/<topic>/`; start with `analyses/README.md`.
- `control/` owns Drizzle control storage, API contracts, the CLI, operator pages, and typed mart reads.
- `ops/` contains the Core runner, deployed Fly app definitions, runbooks, and acceptance scripts.
- `docs/vision.md` is the public vision; `docs/architecture.md` explains the design.
- Local profiles and inputs are ignored. Generated artifacts carry their producer's contract.

## Data and execution boundaries

Rights annotate, never gate serving rows. Each served mart attaches registry flags with `mdp_annotate()`;
`learning_gate` is for derivative consumers and coalesces unknown eligibility to false.
Served marts carry a playlist's description and owner id only for platform-owned owners.
Layers follow entry points: bronze fetches the world, silver reshapes declared warehouse inputs
without egress, gold enriches declared inputs, universal declares its reads, writes, and egress.
SQL owns joins and aggregates; functions shape records and never silently drop them.
Global is the default scope; tenant variants exist only for tenant-bound work.
Text a person wrote lands only where a function declares `retain_days`; the retention sweep deletes it, and
every stored copy (by dump day, and by the oldest input row a run copied), after that many days, and only
frozen counts outlive it.
Local inputs stay under the ignored `inputs/` directories.
Raw DDL belongs to the function's declared schema, not Drizzle. `bootstrap_raw` is a generated local shim;
on Fly, `ops/fly/bootstrap.py` creates every declared raw table and column before a transform can read it.
Drizzle owns control DDL; dbt owns transformed relations. Each fact has one writer.
A cycle binds scope and cadence to frozen membership and a close stamp: its manifest is every
dump of its scope stamped at or before its close, plus its derived rows (cycles closed before
stamps keep their lists). A watermark is a committed sequence (close numbers commit in order per
scope under `scope_close`), not a moving latest pointer; the warehouse mirror catches up by
`mirrored_close_no`, and a close returns only once its stamps and `raw.cycles` row are mirrored.
A work key includes cycle and scope;
retries resume that work with fresh attempts rather than mixing cycles.
Membership freezes with the export of the run that opens a cycle, which fails loudly (`scope_mismatch`)
when a bronze source's membership is missing; a restore, Retry or Replay of a cycle frozen before the
source or its target set existed records that source `not_in_cycle`, an empty success, and closes.
The service refuses a bind from a pre-stamp dbt hook (`runner_outdated`) and never closes a cycle
for one. A deploy selecting `mdp-core-runner` holds its machines until functions and control-api
pass the release check, then runs restore rebuilds before any selected data-api replacement.
Use the [deploy app selections](docs/operating.md#deploy) for changes spanning those services.
For a legacy cycle without `close_no`, bind, close
and catch-up use list mode with close number zero; catch-up restores its complete `raw.cycles` mirror row.
Invoke models are tables with explicit `depends_on`, one cadence tag, and a dependent.
Cadence selectors do not cross cadence boundaries with graph expansion.
Update mart contracts before SQL. Generated files are never hand-edited: regenerate the catalog
and SDK; function declarations drive source YAML, local DDL, tests, and registry rows.
Generated export/invoke/close stubs are explicitly editable exceptions.

## Credentials

| Credential | Owner and access |
|---|---|
| `migrator` | Drizzle migrations; DDL on control |
| `rights_sync` | deployed bootstrap; upsert rights_source |
| `control_rt` | control-api, mdp-cli and the Core runner; read control, write configuration/knobs and `runner_restore`, acknowledge alerts, bump reset_generation |
| `functions_rt` | service; read config, write ledgers, open alerts |
| `service_read` | service; SELECT on non-control warehouse schemas for declared reads and previews |
| `loader_wh` | service; raw DDL/COPY (owner of `raw`), load receipts, cycles, rejected records, targets; DELETE of old `raw.mb_*` generations (spine retention) and of rows past a function's `retain_days` |
| `dbt_transform` | dbt; SELECT raw, own reference/staging/intermediate/marts/tenant schemas, TEMPORARY on warehouse (incremental delete+insert), EXECUTE mdp.invoke |
| `workbench_wh` | workbench; SELECT generated safe warehouse copies, DDL only wb_*, no EXECUTE on mdp.* |
| `reader_wh` | data-api, and control-api's Explorer listing; SELECT `marts` and `tenant_*_marts` only (no other schema, column or default privilege), raw lineage |
| `api_key_reader` | data-api; SELECT control.api_key and control.tenant for key checks only |
| `mb_reader` | functions; read MusicBrainz generation tables and MDP lookup helpers on mdp-mb-db |
| `mdp_udf_owner` | NOLOGIN owner of private mdp.config and SECURITY DEFINER mdp.invoke; dbt_transform receives EXECUTE |
| `analyst_ro` | NOLOGIN group for individual analyst logins; SELECT global `marts`, `intermediate`, `staging` only; no writes or `mdp.*` execution |
| `showcase_wh` | INHERIT login in `explorer_ro`; read-only transactions, four connections, five-second statement and one-second lock timeouts |
| `explorer_ro` | NOLOGIN staff group; SELECT warehouse layers and tenant schemas, raw through `explore_raw` pseudonym views only; no control, writes or `mdp.*` execution |

See [showcase queries](ops/showcase/queries.json) before adding a direct read.
