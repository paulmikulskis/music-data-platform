# Control plane conventions


## Adding one control-plane RPC

An RPC is a typed request to the control API, such as `tenants.create`.
Adding one control-plane RPC touches these files, in this order:

1. Contract: declare the input, output and route in the domain file under `packages/contracts/src/`
   (for example [tenants.ts](packages/contracts/src/tenants.ts)); `index.ts` only composes the domains.
2. Router: implement it in the matching `apps/control-api/src/router.<domain>.ts` (for example
   [router.tenants.ts](apps/control-api/src/router.tenants.ts)) with the helpers in `router.shared.ts`;
   `router.ts` only composes the domains. A new domain adds one file to each folder and one line to each composer.
3. [CLI verb](packages/mdp-cli/src/index.ts): call the typed client and print the result; `tenants` is a small example.
4. [Page](apps/control-api/src/pages.tsx) and [page routes](apps/control-api/src/app.tsx): expose the command through the operator form and its authenticated handler.
5. OpenAPI/SDK regeneration: run `pnpm --dir control --filter @mdp/contracts openapi` for
   [control OpenAPI](packages/contracts/openapi/control-api.json); the control client takes its types
   directly from the contract, so it has no separate generated SDK. If the change also touches mart
   contracts, follow [catalog and data SDK generation](../docs/DEVELOPING.md#control-checks-and-generated-types).
6. Test the command through its callers, including invalid input and denied access; use
   [tenant tests](apps/control-api/test/tenants.test.ts) and
   [OpenAPI drift tests](packages/contracts/test/openapi-drift.test.ts) as examples, then run
   `pnpm --dir control typecheck` and `pnpm --dir control test`.


## Workspace and contracts

Node 22 and pnpm 10 run the ESM workspace. `pnpm install`, `pnpm typecheck` and `pnpm test`
cover packages and scripts. All `@orpc/*` dependencies share catalog pin 1.15.1.
Drizzle owns control DDL; function schemas own raw DDL; dbt owns transformed relations.
Drizzle applies a migration only when its journal timestamp is newer than the last one applied, and
skips an older one silently. A branch whose migrations predate main's newest regenerates them before merge.
`packages/contracts` defines oRPC routes, DTOs and commands; control-api implements them.
Row DTOs select Drizzle columns; bigint/decimal values are strings and timestamps are UTC.
Generate OpenAPI with `pnpm --filter @mdp/contracts openapi`; export the service contract
from the repo root with `uv run --project functions mdp openapi export`.
Conformance tests compare recorded service responses with both OpenAPI and consuming DTOs;
router drift tests regenerate the control document. Live checks require service URL/token.
Database tests require `MDP_CONTROL_INTEGRATION=1` and the isolated role URLs. Offline skips
never satisfy `ops/ci/accept-control.sh`.


## Writes and administration

`streamlines.dryProbe` makes a small live sample through the functions probe endpoint and publishes
nothing. It returns the canary outcome and validation count, audited as the caller.
Use `pnpm --dir control mdp probe <source> --scope global` or the function page's Probe button.
Use Run now for a full run. `/actions/run-sample` starts no work and links to Functions.

`alerts.list` filters by `resolved` and `acknowledged`; `/ops` shows open (unresolved, unacknowledged)
alerts apart from the acknowledged quiet count. `alerts.acknowledge` and `alerts.resolve` are admin
actions and audited; a resolved row carries `resolved_by` and `resolution_reason`. The service has no
update grant on `control.alert`: runtime recovery resolves through `control.resolve_recovered_alerts(run,
cycle)` (SECURITY DEFINER, `functions_rt` EXECUTE), which closes a run's warning classes after a later
scheduled run of that source succeeds with full coverage, a canary warning after a newer passing canary,
and `cadence_failed` after a scheduled build passes with its close mirrored; each row reads
`system:recovery` and writes an `alerts.resolve` audit row.
Buttons change knobs, activation, approved budgets, alerts, runner mode and reset generations,
or request runs/repairs/re-imports. Knobs also change through the audited
`pnpm --dir control mdp knobs set <source_key> --enable|--disable [--batch-size n] [--max-concurrency n]`. A re-import only records the request (control_rt may write
nothing else on reference_source); `ops/fly/mb-import/refresh.py --if-requested` acts on it. PRs change declarations, cadence, schema, prompts, marts and migrations.
Cadence changes return a declaration diff; configured GitHub credentials/repository permit a
draft PR. Without them `pr_opened=false`. Authors regenerate and review generated artifacts.
`pnpm --dir control mdp new function <key> --class bronze|silver|gold|universal --cadence hourly|daily|weekly`
creates an unimplemented source and exports declarations; `pnpm --dir control mdp register` syncs the registry.
`pnpm --dir control mdp new mart <name>`, `new prompt <key>` and `scaffold api <mart>` support authoring.
A streamline row carries `parked_inputs`: the count a gold function's latest read left out while
its `inputs_parked` alert is open (the function page's card, `pnpm --dir control mdp status <key>`).
`streamlines.unpark` (the card's button, `pnpm --dir control mdp unpark <key>`) resolves that alert, and the
next read counts only failures after it, so every parked input is tried again.
The runner writes `control.audit_log` with `heartbeat.sent`, `heartbeat.skipped` or
`heartbeat.failed` once per successful scheduled build, with cadence and configuration state but no ping URL. `/ops` and `mdp status` show
its latest result and age. The unauthenticated `/health/status` exposes only check time, health and
cadence names and a recovery step, through a bounded read-only transaction. Showcase forwards that summary publicly;
GitHub's cadence watch needs no platform credential. See [held runners](../ops/runbooks/runners_held.md).

The email worker mails open, unacknowledged critical alerts and the emailing warning classes through
Resend (`RESEND_API_KEY`) or SMTP (`SMTP_URL`) with MDP_EMAIL_FROM/MDP_EMAIL_TO; dev auth logs them.
Missing transport, sender or recipient settings make no delivery attempt.
Each open alert gets one durable `email.skipped` row with setup guidance, even across restarts.
A complete configuration resumes delivery without removing that row.
A delivery failure retries after 30 s, doubling to 1 h; the tenth writes `email.gave_up`.
Open [alert email setup](../ops/runbooks/service_unreachable.md#alert-email) before enabling delivery.


## Authentication and pages

Clerk takes precedence when CLERK_SECRET_KEY exists. MDP_AUTHORIZED_PARTIES restricts token
parties; public metadata mdp_admin/mdp_tenant_id grants administration/maps tenant identity.
`mdp_staff: true` opens console GET pages and Workbench analysis; `mdp_admin` takes precedence.
Staff API keys use role `staff`, issued by an admin with
`pnpm --dir control mdp keys create --role staff --label <text>` and revoked with `keys revoke <id>`.
Optional `--warehouse-role analyst_<handle>` or Clerk `mdp_warehouse_role` links Sandbox status
only to that owner. Missing links show setup guidance. Sandbox operator view and all operator
mutations need admin. Reader keys cannot open the console; staff keys cannot use the data API.
API keys use x-api-key, stored hashes, expiry/revocation and optional active-tenant scope.
Reader keys cannot administer control. A `promoter` key (the mdp-functions promoters', minted at
bootstrap) reaches only the target commands `ControlTargets` calls (`listSets`, the keyed `lookup`,
`createSet`, `importTargets`, `resolve`, `setSpec`, `bulkActivate`), and the data API refuses it.
It writes the global `playlist`, `account` and `artist_page` sets and proposes into tenant `track`
sets (import and spec there; `resolve` and any activation are refused, so an operator confirms).
A target's provenance is its importer, the actor of the succeeded `targets.importTargets` audit row
that added it (an update in place is not an import; `lookup` shows it as `promoter_import`). Without a reason the promoter resolves,
moves or adopts only what a promoter key imported, and never over a person's deactivation. `setSpec`
always needs its `promotion_reason`; it writes only where no spec exists (on its own import) or its
own reason holds one, and with `activate` commits the spec and the activation together, refused
(409 `target_held`) after a person's deactivation. With `promotion_reason`, `targets.bulkActivate`
changes only targets whose spec carries it and reactivates one only when its last deactivation (the
newest succeeded audit row whose input set `active=false` and whose result shows it deactivated; an
edit-only patch is none) is the caller's or a promoter key's (`lookup` shows the other case as
`person_deactivated`).
Missing auth configuration fails closed.
`apiKeys.create|revoke|list` (audited; `pnpm --dir control mdp keys create --tenant <slug>`) issue tenant reader
keys: the key is returned once, only its sha256 is stored, and list returns metadata. `keys create --role reader
--global` issues a tenantless reader (the showcase's `MDP_SHOWCASE_READER_KEY`): it reads global marts, tenant marts
refuse it with `tenant_required`, and control-api refuses every request from any reader key. A reader key takes
exactly one of `--tenant` and `--global`; the CLI checks its options with the contract's input schema.
`MDP_AUTH_MODE=dev` explicitly enables fixed dev-user on local listeners only; local tenant
identity comes from MDP_DEV_TENANT_ID/MDP_DEV_TENANT_SLUG, never a data request body.
Browser mutations accept the request origin and exact comma-separated origins in
`MDP_TRUSTED_BROWSER_ORIGINS`. Never trust forwarded headers to expand that list.
The workbench session cookie is Secure; session POST redirects use 303.

Hono serves `/ops`, `/functions`, `/functions/:source_key`, `/targets/:id`, `/screen/weekly`,
`/runs`, `/runs/:id`, `/traces/:id`, `/functions/:source_key/logs`, `/runbooks/:slug`,
`/audit/:id`, `/tenants`, `/reference`, `/sandbox`, `/sandboxes`, `/queries`, `/explorer` and `/workbench`. Forms call the same router as `/rpc` and `/api` and redirect
with 303 to a result/audit link; refresh does not repeat a mutation. Pages are server-rendered
with progressive browser enhancements. Local trace pages read durable events without OTLP.
Staff forms for operator actions render disabled with the admin requirement and access runbook.
The held-runner and heartbeat guides ship from `ops/runbooks/` in the control-api image.
Their `/runbooks` pages use bundled text before database seeds, with section anchors for setup links.
HTML and JSON errors use the error catalog's next step and runbook.
Staff function pages omit output and rejected-record previews; explicit previews need admin.
Function previews use declared tables through service_read, signed page cursors, a 100-row
cap and statement timeout. CSV exports the displayed page; arbitrary preview SQL is refused.
Fixture controls require both dev authentication and service fixture capability.


## Data reads and generated SDK


## Workbench

`/workbench` forwards authenticated actors to the separate Python workbench service.
Explicit POST creates a one-day session; an HttpOnly cookie reuses it. GET does not create
schemas or runs. Run executes plain SELECTs under the session read role with a timeout and row cap. It never builds
a dbt project. Preview and Backtest use workbench_wh with an
isolated wb_* schema and NOLOGIN session role with non-inherited membership.
Provisioning/expiry uses MDP_WORKBENCH_ADMIN_URL; user SQL never uses that administrator.
Limits default to 100 rows, 30 seconds, 100 MiB pre-build schema size, one active preview per
session and two per user. Expiry drops scratch resources. Inputs receive read-only grants. Admin sessions inherit explorer_ro.
Staff sessions use separate ownership ids and inherit analyst_ro with SET ROLE disabled.
Each execution removes old independent grants and checks current input permissions.
Staff SQL resolves to the generated global marts, intermediate and staging copies, plus catalog.
Preview builds only the draft for staff and admin sessions; refs read existing permitted safe copies.
An ephemeral ref offers a stored model to use instead. Open `/explorer` to choose an input.
The rights registry ref resolves to `catalog.learning_rights`, which exposes only `source_key`,
`learning_eligible` and `resale_permitted` to human roles. The warehouse grants trigger refreshes
this view when the registry is rebuilt. Production dbt refs keep their normal registry relation.
Use the unchanged `mart_chart_history` draft to check Preview and Backtest.
A missing or restricted input returns workbench_permission_denied with a permitted global input to use.
Staff cannot query sandbox, tenant or raw relations. See the access runbook for role changes.
Queries shows only the staff actor's records. Explorer hides restricted warehouse relations.
Draft SQL persists with workbench runs and builds in scratch dbt projects; invokes stay inert.
Admin Backtest rebuilds each selected global cycle from frozen safe inputs. The generated
`explore_raw` copies expose only global dump membership, close stamps and load counts, without raw grants.
Backtest copies each input and checks its row count against every listed dump's committed receipt.
Missing or partial history refuses; later retention cannot change a checked copy.
Supported upstream SQL compiles into CTEs; invokes, hooks and build stamps stay inert.
An input without dump membership, an unsupported helper or a surviving current-table read
returns `workbench_history_unavailable`. Choose Preview to read current permitted inputs.
Staff Backtest uses current permitted global inputs for both cycle contexts.
Comparisons use contracted keys and non-lineage columns. Select two closed cycles to compare them.
Artifacts have distinct A/B keys and authenticated local downloads or expiring object URLs.
Save as PR requires a successful preview/backtest of the exact draft and confirmation of the
current diff; a changed draft/repository invalidates that review. With GitHub auth it
publishes from a disposable clone/worktree and labels the PR workbench; without auth it
returns an applicable SQL and contract patch with download, copy and clone commands, and `pr_opened=false`. The working checkout is never committed.
`DBT_CLOUD_IDE_URL` supplies the IDE link. Enrichment reruns pin config_version; costsync
reconciles proxy spend. Local topology and evidence limits are in the [developer guide](../docs/DEVELOPING.md#status).


## Showcase

`apps/showcase` is the viewer app (Next.js, deployed as `mdp-showcase`); its [guide](apps/showcase/README.md)
lists every runtime variable and screen. People are runtime entries in `MDP_SHOWCASE_PEOPLE`, each with
an admin key of their own. The app reads served marts through the data API (`MDP_SHOWCASE_READER_KEY`),
the warehouse as `showcase_wh` (INHERIT in `explorer_ro`, read-only, four connections), and control
through `control_rt`. The console is proxied with the person's server-side key, so the audit log names
the person. See [deploy app selections](../docs/operating.md#deploy) before releasing a change.


### Read budget

One process owns the read gate: four heavy slots and one shared light pool.
Ordinary light reads enter while that pool has fewer than six active reads; essential reads
can enter up to eight. The queue holds twenty requests for at most three seconds.
A session has at most two active requests when the caller supplies its session key.
Heavy cached reads do not refresh while runner state is busy or unknown.
Keep one app instance. Concurrency lives in [read-budget.ts](apps/showcase/server/read-budget.ts).
Per-second request limits live in [rate-limit.ts](apps/showcase/server/rate-limit.ts):
auth 10/10, documents 18/36, data 42/84 and art 40/80 (session/IP).
Route assignment lives in [request-bucket.ts](apps/showcase/lib/request-bucket.ts).
Inspect the matching file before changing a limit.
Direct warehouse reads follow showcase reads.
Shared platform SQL and the runner-state read live in `@mdp/contracts` (`platform-sql.ts`,
`runner-state.ts`), so control-api and the showcase read one definition. A proof link opens a plain
summary in the showcase first (the exact charts, playlists or counters, when and where they were read,
Back to the screen it came from); its operator console link opens the contributing run through the captured
cycle, and the console's Back bar returns to the summary. A number's citation copies the SQL or CLI
command that produced it, which an analyst can run as-is. Library results never open the Explorer.
The Explorer lists warehouse relations as `reader_wh` (`MDP_READER_URL`); without it the page says so. Add a read only through
[Add a room read](apps/showcase/README.md#add-a-room-read): contract, SDK, route, `server/reads.ts`,
`ops/showcase/queries.json`, adapter check, then the browser density check.


### Links and sessions

Control tables: `showcase_link` (signed one-time links), `showcase_session`, `showcase_actor` (the
person behind an api key id), `showcase_share`, `showcase_seen`, `showcase_inventory` (the 00:30 UTC
storage capture), `showcase_relation_count` (exact counts per relation build), `showcase_call`,
`showcase_rule`, `showcase_draft` and `showcase_call_rule`.
`control_rt` has only the grants each table needs; `functions_rt` and PUBLIC have no grant.
`control_rt` cannot delete a `showcase_actor` row, and on `showcase_call` it may insert
and update only `undone_at`, `hidden_at` and `hidden_by`, so a call is write-once.
A session ends after `MDP_SHOWCASE_IDLE_DAYS` without a request (default 14) or
`MDP_SHOWCASE_MAX_DAYS` from sign-in (default 60); both take 1 through 365 and idle cannot exceed max.

`showcase.link` (`pnpm --dir control mdp showcase link --person <handle> [--ttl 24h]`) is the admin RPC
that mints a link: control-api needs `MDP_SHOWCASE_PEOPLE` and `MDP_SHOWCASE_LINK_SECRET`, answers
`showcase_link_unavailable` with setup guidance without them, inserts the link row, records the actor and
audits the caller. A GET never consumes a link; the person presses Sign in.
`mdp showcase revoke --person <handle>` ends links and sessions through `MDP_CONTROL_RT_URL` directly.
Removing a person from the runtime list refuses their next request.


### Calls

A call (`control.showcase_call`) freezes a pick: the song key, its platform-track anchors,
the places shown on the card, the fact snapshot, its day and close number.
An idempotency key unique per author makes a retried post return the saved call. `author_kind` is `ear` for a person and `rule` only for the
`rules` author. The week boundary, pick limit and undo window are defined in
[call settings](apps/showcase/lib/calls.ts). An undone call still uses its slot;
late undo returns `call_undo_expired`. Later chart
days are compared with the frozen anchors; grouped Spotify calls find their Shazam places through
`mart_song_cluster_members`, and a representative change never moves a call. The Picks board lists
the week's picks and earlier weeks at `/songs?view=picks`. Open a pick at `/songs/picks/<id>`;
its proof is at `/songs/picks/<id>/proof`.

Every contributing build must be stamped, have a build time, and share one non-null cycle id
and close number. A human submission checks this when creating the offer and verifying its
signed token, including the snapshot's close number. A new insert verifies the token before saving.
Rule calls use the provenance checked when the tray is captured. Closing the draft inserts
their frozen snapshots without a signed token or another warehouse read.
Missing or mixed stamps prevent an offer or tray capture; inspect
[call-builds.ts](apps/showcase/server/call-builds.ts) and the captured build envelopes.


### Drafts and rules

`showcase_draft` stores one frozen candidate tray per weekly selection key.
The tray comes from the weekly opening day's UTC read after its scheduled daily cycle closes.
Capture applies the [call provenance check](#calls) to its contributing builds.
Daily playlist candidates need an opening-day editorial or new-music add or head entry.
Weekly playlist candidates also need the whole observation interval to start on the opening day.
Shazam candidates need a Discovery chart entry. Read [the tray query](apps/showcase/server/draft-reads.ts).

The draft closes on the [weekly schedule](apps/showcase/lib/draft.ts), or earlier through Close the draft.
The app timer checks due drafts every minute and can close a frozen tray while the warehouse is down.
An unopened draft past its deadline is skipped, including when a build arrives late.
Both the read path and the locked insert check that deadline. Open `/songs?view=friday` for the next opening.

`showcase_rule` holds conditions, a pick limit and the person backing the rule.
Only approved rules produce picks. Close evaluates them against the frozen tray, saves the backed
rules on the draft and creates calls by the `rules` author in one transaction.
`showcase_call_rule` links each resulting call to its matching rules.
`showcase_call.draft_week` links both a person's draft pick and a rule pick to the weekly key.
Closing again returns the saved result. There is no reopen action.
The candidate tray and call facts are immutable; only backing and close fields have update grants.
See [draft storage](apps/showcase/server/draft-store.ts) before adding a draft action.


## Error catalog

[docs/errors/catalog.yml](../docs/errors/catalog.yml) owns each error's summary, next step and runbook.
[The generator](packages/contracts/scripts/error-catalog.py) writes three bindings:
TypeScript in `packages/contracts/src/error-catalog.ts`, Python in
`functions/src/mdp_functions/error_catalog.json`, and R in `r/mdpr/inst/extdata/error_catalog.json`
(the latter two paths are relative to the repository root).
Add each new error class to the catalog, then run
`pnpm --dir control --filter @mdp/contracts error-catalog` from the repository root.
`ops/ci/lint_error_catalog.py` checks coverage and generated files.
