# Glossary

Each term links to the code or guide that defines its behavior.
Names sort alphabetically, ignoring a leading underscore.

| Term | Meaning and owner |
|---|---|
| Access | How a collection job reaches a source: an official API, a public page, a data service or a supplied file ([flow vocabulary](../control/packages/contracts/src/flows.ts)). |
| Alert and runbook | An alert records a problem to investigate, and its runbook gives the checks and recovery steps ([runbook index](../ops/runbooks/README.md)). |
| Analysis folder | Source, SQL, tests, lockfile and model provenance under `analyses/<handle>/<topic>/` ([R guide](r.md#save-work)). |
| Arrival | An arrival is a song entering a tracked list, chart or Shazam chart inside the movement window; it carries a rank and no score ([arrivals mart](../dbt/models/marts/global/mart_arrivals_current.sql)). |
| Backtest harness | The song backtest replays the movement rules over closed daily cycles in a private container and scores what happened next; it measures fixed rules and never tunes them ([harness guide](../ops/backtest/README.md)). |
| Bronze | A bronze function fetches outside data into declared raw tables, which bronze SQL staging cleans and deduplicates ([layer guide](DEVELOPING.md)). |
| Build stamp | A build stamp records a table's cycle, close number and build time in `marts._build`, in the same transaction as its contents ([stamp macro](../dbt/macros/mdp_annotate.sql)). |
| Cadence | Cadence is the hourly, daily or weekly schedule assigned to work ([runner](../ops/run.sh)). |
| Cadence tag | A tag such as `cadence:daily` puts a dbt model in exactly one schedule ([selector definitions](../dbt/selectors.yml)). |
| Call | A call saves a song pick by a person or an approved draft rule, with frozen facts and places from one cycle and close. A person has a weekly pick limit and a limited undo window ([calls and drafts](../control/CLAUDE.md#drafts-and-rules)). |
| Category | A label saying whether data is public, tenant monitoring, vendor-licensed, tenant-private or personal ([query guide](analyst-access.md#query-everything)). |
| Choice | A question that chooses one named option and returns every option's probability ([guide](jev.md#evaluation)). |
| `close_no` | A close number is a sequence committed in order within one scope when a cycle closes ([cycle service](../functions/src/mdp_functions/cycles.py)). |
| Collection job | Code that reads a source on a schedule and saves its results ([flow vocabulary](../control/packages/contracts/src/flows.ts)). |
| Confidence | Jev's certainty value for Choice and Score, separate from any one option's probability ([guide](jev.md#evaluation)). |
| Contract | A mart contract is YAML that declares its column names, types and constraints so dbt can check the SQL result ([example contract](../dbt/models/marts/_marts__models.yml)). |
| Cross-tenant | A query whose inputs contain more than one tenant's data; staff can run it, and the red label calls for review ([query guide](analyst-access.md#query-everything)). |
| Cursor | A collector cursor is a saved checkpoint that advances with a committed page so later work can resume ([collector context](../functions/src/mdp_functions/layers.py)). |
| Cycle | A cycle groups work for one scope and cadence around frozen target membership and a close that fixes its inputs ([cycle service](../functions/src/mdp_functions/cycles.py)). |
| Deploy hold | A deploy holds each runner machine without a schedule and with an owner marker until the new services pass their release check, then restores the hourly schedule ([deploy script](../ops/deploy.sh)). |
| Draft | A draft holds a frozen song tray for one weekly selection period. It closes on the application's schedule; an unopened draft past its deadline is skipped ([draft lifecycle](../control/CLAUDE.md#drafts-and-rules)). |
| Early signal | An early signal is a song with positive movement in exactly one independent family; a mover has two ([early signals mart](../dbt/models/marts/global/mart_early_signals_current.sql)). |
| Error catalog | The shared error summaries, next steps and runbook links for TypeScript, Python and R ([catalog](errors/catalog.yml)). |
| Explore login | An individual staff login that reads warehouse layers through `explorer_ro`, with raw identifiers replaced by pseudonyms ([query guide](analyst-access.md#query-everything)). |
| Fixture | A fixture is saved test input, such as an HTTP response replayed without contacting the provider ([fixture transport](../functions/src/mdp_functions/http.py)). |
| Gold | A gold function enriches declared warehouse inputs through budgeted external calls or model steps ([function classes](../functions/CLAUDE.md#ownership)). |
| Jev | A model that answers typed questions about supplied state ([guide](jev.md)). |
| Knobs | Knobs are operator settings, such as enabled state and timeout, that control a function without changing its code ([streamline schema](../control/packages/control-db/src/schema/config.ts)). |
| Label | Generated layer, category, tenant and rights metadata that follows a relation or result without filtering rows ([generator](../functions/src/mdp_functions/relation_labels.py)). |
| `learning_gate` | This SQL filter lets derivative consumers use only eligible rows, treats unknown eligibility as false and refuses tenant material ([macro](../dbt/macros/learning_gate.sql)). |
| Licence status | The registry's evidence for a source's licence; unverified or unknown grants no learning or resale permission ([query guide](analyst-access.md#query-everything)). |
| Manifest | A cycle manifest identifies the stored batches visible to that cycle through its close numbers and derived outputs, with older cycles using a saved list ([manifest filtering](../dbt/macros/mdp_context.sql)). |
| `mdpr` | The R package for service-file connections, rights guards and analysis proposals ([package](../r/mdpr/README.md)). |
| Movement group | A movement group joins copies of one song for scoring only, under measured merge rules; identity keys stay strict and unchanged ([cluster model](../dbt/models/intermediate/int_song_cluster__daily.sql)). |
| Noul | A question that returns the probability a statement is true ([guide](jev.md#evaluation)). |
| Pin | A versioned local fitted model with input and environment metadata ([R models](r.md#models)). |
| Preview / Backtest | Preview builds a draft against one closed cycle in scratch tables, while Backtest builds two cycles and compares their business rows ([workbench](../functions/src/mdp_functions/workbench.py)). |
| Pseudonym | A keyed, one-way identifier used for joins without revealing a private person's identity ([macro](../dbt/macros/mdp_pseudonym.sql)). |
| Question set | A versioned field list, questions, model and thresholds; its content hash identifies it ([guide](jev.md#store-a-question-set)). |
| Retry | Retry runs the full job again on its bound cycle and resumes unfinished work with fresh attempts ([runner guide](../ops/CLAUDE.md#runtime-and-clock)). |
| Rights annotation | Rights annotation adds learning and resale flags from contributing sources to each served row without removing rows ([annotation macro](../dbt/macros/mdp_annotate.sql)). |
| Rule | A showcase rule stores conditions and a pick limit. A person approves it before it can choose songs from a frozen draft tray ([rule storage](../control/apps/showcase/server/draft-store.ts)). |
| Run and receipt | A run records one function's work, and a receipt reports its loaded output, row counts and coverage ([runtime](../functions/src/mdp_functions/runs.py)). |
| Sandbox | An analyst-owned schema readable by other analysts, outside serving and dbt dependencies ([R rules](r.md#rules)). |
| Scope (global / tenant) | Scope says whether work belongs to the shared platform (`global`) or one tenant (`tenant:<id>`) ([runner guide](../ops/CLAUDE.md#runtime-and-clock)). |
| Score | A question that rates state on ordered levels and returns their weighted value ([guide](jev.md#evaluation)). |
| Selector | A selector is a named dbt model selection, such as `daily_global_transform`, that keeps scheduled work within its cadence and scope ([definitions](../dbt/selectors.yml)). |
| Showcase | The showcase is the viewer app: signed links, per-person keys, served movement, calls and a proxied console ([showcase guide](../control/apps/showcase/README.md)). |
| Silver | A silver function reshapes declared warehouse inputs without network access, while silver SQL owns joins and aggregates ([function classes](../functions/CLAUDE.md#ownership)). |
| `_source_keys` | This JSON array stored as text names every source contributing to a row so rights can follow its inputs ([annotation macro](../dbt/macros/mdp_annotate.sql)). |
| Streamline | A streamline is the control record for a registered function, holding its source key, declarations and operator settings ([schema](../control/packages/control-db/src/schema/config.ts)). |
| Target and target set | A target is an item to collect, such as an account or playlist, and a target set groups items of one kind for global work or one tenant ([schema](../control/packages/control-db/src/schema/config.ts)). |
| Tenant | A tenant is an organization with its own targets, settings and restricted data access ([onboarding](operating.md#onboard-a-tenant)). |
| Universal | A universal function declares its warehouse reads, any writes and whether it needs outside access ([function classes](../functions/CLAUDE.md#ownership)). |
| Watermark | A cycle watermark is a committed close sequence that bounds visible data, with `mirrored_close_no` tracking how far the warehouse has caught up ([cycle service](../functions/src/mdp_functions/cycles.py)). |
| Workbench | The workbench is a browser workspace for querying inputs and trying SQL in temporary `wb_*` schemas ([workbench guide](../control/CLAUDE.md#workbench)). |
