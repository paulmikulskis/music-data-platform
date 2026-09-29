# Showcase

Run `pnpm --dir control --filter @mdp/showcase dev` with the runtime variables below.
Open the printed local address after creating a link. For local HTTP, use `localhost` so the
browser accepts Secure host cookies on loopback. Production uses HTTPS.

| Variable | Use |
|---|---|
| `MDP_SHOWCASE_PEOPLE` | JSON array of handle, display_name, admin_key and api_key_id. Email is optional and unused. Each handle has its own admin key. |
| `MDP_SHOWCASE_LINK_SECRET` | At least 32 random bytes for signed links. |
| `MDP_SHOWCASE_SESSION_SECRET` | At least 32 random bytes for pre-authentication challenges. |
| `MDP_CONTROL_RT_URL` | Control connection for showcase tables and audit rows. |
| `MDP_SHOWCASE_WH_URL` | Private TLS warehouse connection as showcase_wh. |
| `MDP_SHOWCASE_READER_KEY` | Global data API reader key from `pnpm --dir control mdp keys create --role reader --global --label showcase-reader`. It reads global marts only; control-api and tenant marts refuse it. |
| `MDP_CONTROL_API_URL`, `MDP_DATA_API_URL` | Private API addresses. |
| `MDP_SHOWCASE_INVENTORY_ENABLED` | Set to `1` to capture storage at 00:30 UTC and check completed builds for exact counts every minute. The Fly definition enables it. |
| `MDP_SERVICE_URL`, `MDP_SERVICE_TOKEN` | Private functions endpoint and token for failed storage alerts. |
| `MDP_SHOWCASE_IDLE_DAYS` | Optional whole days without a request before sign-in ends. Defaults to 14. |
| `MDP_SHOWCASE_MAX_DAYS` | Optional whole days from sign-in before it ends. Defaults to 60. |
| `MDP_SHOWCASE_ORIGIN` | Optional exact origin. Defaults to https://mdp-showcase.example.invalid. Use http://localhost:3000 locally. |

Follow [Give a viewer access](../../../docs/operating.md#give-a-viewer-access) for the six setup steps.
Both day settings accept 1 through 365. Idle days cannot exceed maximum days.
Invalid settings stop startup with a message naming the setting to fix.
A shorter maximum applies to existing sessions; a longer one does not extend their stored expiry.
Restart the app after changing settings, then mint a new link if sign-in ends.

People and credentials exist only in the runtime environment. Do not commit their values.
Load the operator admin key as `MDP_API_KEY` and set `MDP_CONTROL_API_URL`.
Control-api needs the same people, link secret and origin as showcase.
Mint a link with `pnpm --dir control mdp showcase link --person <handle> [--ttl 24h]`.
Open its URL or scan its terminal QR, then press **Sign in**. A GET never consumes a link.
Revocation still needs `MDP_CONTROL_RT_URL` and the people configuration on the operator machine.
Revoke unused links and sessions with `pnpm --dir control mdp showcase revoke --person <handle>`.
Removing a configured person refuses their next request. Restart the process after changing its
runtime environment. Key rotation preserves the old actor mapping.


## Screens

The five tabs are Home (`/`), Songs (`/songs`), Sources (`/sources`), Stack (`/stack`) and Analyst onboarding (`/team`).
Search opens at `/search`. Choose a tab to load its facts.

Home shows up to four movers, or the leading arrival while the two-source ranking has no songs.
Tap a cover to open the song. **Last night** opens the reading timeline: reads, joins and ready tables,
with source details and dates. A closed collection alone does not prove a table is ready;
the ready step needs its table's saved build stamp. Open a timeline step to inspect it.

Songs has Rising (`?view=rising`), Places (`?view=places`), Waking (`?view=waking`),
Picks (`?view=picks`) and Weekly picks (`?view=friday`). Movement cards state their window and
whether a song is new or catalog. A song joins its confirmed copies and draws its observation lanes.
Tap a lane for that day's value or drag to scrub. **Places** opens its Shazam map and dated counts.
A lane's proof reads every matched copy that contributes to its value. Choose **See proof** to inspect them.

Picks saves a person's choice with the facts and places known at that time.
Hold **Pick this song**, or use the keyboard and confirm. Each accepted pick counts toward the weekly
limit, even if undone. Undo is available for a limited time.
The board at `/songs?view=picks` explains when no song can be picked or the slots are full.
Choose **Earlier weeks**, or open `/songs/picks/<id>` to compare a pick's saved places with later chart days.
Its proof opens at `/songs/picks/<id>/proof`.

Sources groups music readers with their weekly variants. Details keep each reader's own counts and dates.
Collection and Rights are views at `/sources?view=collection` and `/sources?view=rights`.
Collection separates landed records from storage and permissions. Days without readings stay missing.
Sample loads from sources waiting for access do not count. Open a number for its dated source.

Source states come from `lib/honesty.ts`: up to date, reading, queued, partly delivered, overdue,
paused, switched off, waiting for provider access, tenant-bound, empty or unknown.
An enabled source without targets differs from one without a successful reading.
The recorded reader cadence sets freshness, including daily readers that rotate weekly targets.
`lib/rights.ts` separately describes collection and permission states from the rights registry.
Missing evidence stays unknown; a configured default never proves collection. Open source details
for the attempt, last successful read and available recovery step.


### Data source viewer

A card's data source button opens the path from outside sources through readers and tables to the screen.
The generated graph describes what **Can feed this**. Only matching facts light a contributing path;
missing or changed builds stay unconfirmed. Expand a step to inspect its dependencies.
Reader cards show their own last successful read from `platform.sources`, independent of count capture.
A count shows its saved capture time. **Counted at** opens the nearest counted step's details.

**See rows** opens a peek of at most five rows with reviewed fields, ordering and filters.
`ops/showcase/lineage/peek.json` owns the review. Missing projections lead to source details.
Raw, tenant and retained-text rows are not peeked. Rights annotate the displayed rows.
Choose **Open in Workbench** where offered to inspect the same reviewed SQL before running it.

Every number, source and card says where it came from, one hover away (press and hold on a phone).
From the keyboard, Tab onto a number, badge, artist or counter; Enter or Space opens it as a sheet,
Escape closes it and focus returns.
Source badges draw brand glyphs from the CC0 `simple-icons` package; hover shows what the source read
today, and a tap opens its card with **See source details**. Tap a number for **How we know**:
the trail in plain words, a small map or curve, **See the entries** and public platform links.
**See proof**, **See the entries** and **See the charts** open a plain summary first: the exact charts,
playlists or play counters behind the fact, when and where they were read, and **Back** to the screen
the link came from. **Open in Console** is a quiet link from there.
The button at a card's corner shows the path from the sources to the card with real counts.
An artist name opens the artist card: a Wikimedia Commons photo found through the artist's Wikidata
item, else a monogram. A photo needs a license and a Commons file page; the card shows it only after
its author and license have loaded. `/artist-photo/<item>` serves only items the warehouse links to
exactly one artist. The server reads only `www.wikidata.org`, `commons.wikimedia.org`,
`upload.wikimedia.org` and `thumb.wikimedia.org`, one photo at a time with an eight-second deadline,
and never hot-links. Photos keep their own cache of at most 96 photos and 12 MB, so they never push
out saved room values; a missing photo is remembered for ten minutes. Holdings counts what mdp
tends every day. A source line says "read today" only for a read today; any other read shows its date.
Counts come from `platform.sources` and the warehouse; a missing count says `not measured yet`.
The world map is generated: run `python3 ops/showcase/world-land.py` to rewrite `lib/world-land.ts`
from Natural Earth's public-domain land outline. City and market positions live in `lib/places.ts`.

All fact reads pass through one process-wide gate: four heavy reads, eight light reads, and twenty
queued requests. Queue waits stop after three seconds. A failed read keeps the last good value and
its original time. A busy or unknown runner prevents heavy refreshes. Keep one showcase instance.
Caches are bounded and in memory; a restart clears them. Private reads are keyed by API-key identity.
Two light slots are reserved for sessions and heartbeat reads. Artwork has two workers before admission.
Every protected request validates the session. Streams repeat validation every minute.

The heartbeat shares one ten-second event poller. Opaque API cursors survive reconnects through SSE;
stable event keys remove overlap duplicates. New tabs start with the current state. Reconnecting tabs
use their last event ID to replay saved events. Busy or unknown runners limit history catch-up
to one page per tick. Hidden tabs close their streams. Historic events do not
animate as new work. The idle line uses `runner.next_scheduled_at`. This is the earliest time a declared global Core job
is eligible. The host starts on its next hourly tick. Tap **Live** for the next read in your local time
and the song’s permissions. Missing schedule data stays unmeasured.

The console uses a fixed upstream and enumerated paths. Each request uses the person's server-side
key. Non-GET requests need the exact Origin header and same-origin Fetch Metadata. Proxied HTML adds a
same-origin referrer policy so native forms carry their Origin header. The sign-in page keeps no-referrer.
Workbench cookies stay on the server, keyed by showcase session. Every upstream Set-Cookie is removed.
RPC reads use their declared operation, including POST requests. Function previews are heavy;
the operations page reads function metadata only. Safe reads share a cache by identity and input.
The Back to showcase bar is a navigation landmark inside the page body. An open shadow root
keeps console styles and scripts from hiding it; assistive tools can still read its link. Its absolute link
works with a keyboard and screen reader. Upstream base tags are removed. Console code is trusted; escaping its data remains the console’s job. The bar returns
from signed proof links to the proof summary the viewer opened (a viewer screen only, else the song).
Signed proof handoffs omit global alerts and write forms. Their internal links keep that reading context.
The avatar menu still opens the operator console. Captured evidence remains tied to its original ranking.
Open Home's **Last night** for the reading timeline, or `/sources` for each reader's status.

A song’s **Daily adds** lane counts distinct playlists entered that UTC day across its matched copies.
Cards count distinct playlists over the period printed on the card. For example, two adds on separate
days can produce a two-playlist card and a one-playlist daily reading. Chart lists appear in Places.
Places marks a market **New** only when its first tracked chart appearance falls within that source’s
ranking period. Open the card’s sheet to compare new markets with places already reached.

The Library opens songs on their page and every other result in a sheet: a playlist's owner (platform
lists only), followers and newest songs; a chart's place and top five; an artist's matched songs, one
each, and the first day one had something on its page; a source's card. No Library result opens the
operator console's Explorer.

Artwork comes from the [Cover Art Archive](https://musicbrainz.org/doc/Cover_Art_Archive/API)
through recording releases, the [official Spotify embed thumbnail](https://developer.spotify.com/documentation/embeds/reference/oembed),
or Apple's public [iTunes lookup](https://performance-partners.apple.com/search-api) by Apple song id.
A Spotify or Apple song key uses its own cover. Any other song key tries its Spotify copies, then
its Apple copies, then MusicBrainz, which allows one request a second. A key that is not a song key
(uuid, `isrc:`, `spotify:`, `apple:` or `bandcamp:`) has no cover and never reaches a warehouse read.
Apple ids wait in one batch of up to 50; lookups stay at least three seconds apart, and a found
cover URL is kept for a day, a missing one for ten minutes.
The server checks every redirect host, limits image sizes, and caches at most sixty-four images.
Images load lazily, require a session and are never included in exports. Missing art shows the Music Data Platform monogram.
A cover appears only once it loads; a failed load retries twice, each after a random one to three
seconds, then keeps the monogram. Requests use four separate per-second buckets:

| Bucket | Per session | Per IP |
|---|---|---|
| Auth | 10 | 10 |
| Document | 18 | 36 |
| Data | 42 | 84 |
| Art | 40 | 80 |

Both limits must pass. `server/rate-limit.ts` owns these numbers; `lib/request-bucket.ts` assigns routes.
Page and RSC navigation uses Document. JSON handlers, RPCs and mutations use Data.
Sign-in and sign-out use Auth; covers and artist photos use Art. These arrival limits are separate
from the concurrent read budget. Inspect those two files before changing a limit.
The two OFL fonts and brand assets live in `public/`. Their licence files travel with the fonts.

The named query adapters in `server/reads.ts` and `server/platform.ts` follow the data and control
contracts. Data rows pass runtime validation. No fixtures or fallback songs run in the application.
Open those modules to inspect query limits and timeouts.
Run `MDP_PG_PORT=<free-port> bash ops/showcase/check-adapters.sh` after local initialization
to build their inputs and test every direct adapter with the restricted warehouse role.

Run `pnpm --dir control --filter @mdp/showcase test`, `lint`, `typecheck`, `typecheck:browser` (the browser harness under `test/browser/tsconfig.json`) and `build`.
Run database auth tests with `MDP_SHOWCASE_TEST_URL=<isolated-control_rt-url> pnpm --dir control exec vitest run packages/showcase-auth/test`.
The test database must be disposable and on loopback; its owner password is the local fixture value.
See [the developer guide](../../../docs/DEVELOPING.md) for the broader control checks.

Deploy through `ops/deploy.sh` after control-api and data-api. A first install must include
mdp-functions or mdp-postgres so bootstrap applies migrations and roles before the app starts.
Inspect `bash ops/deploy.sh --dry-run --app mdp-showcase` before deploying.

The rendered browser gate uses a disposable local PostgreSQL database. Initialize it with
`MDP_PG_PORT=<free-port> bash ops/local/init.sh`. Build the reviewed inputs with
`MDP_PG_PORT=<free-port> bash ops/showcase/check-adapters.sh`, then build the app and run
`MDP_SHOWCASE_BROWSER_DB=<loopback-owner-url> pnpm --dir control --filter @mdp/showcase density`.
The harness binds ports 3108 and 3109; set `MDP_SHOWCASE_BROWSER_PORT` to use that port and the next one instead. It starts the app with `control_rt` and `showcase_wh` roles.
It seeds marked test data, signs in through the real link flow, checks the console, records phone and
laptop screens, opens every hover card and sheet of the "where it came from" layer, and enforces the
phone, hover card and sheet text budgets. Never point it at a shared database.
With no `BROWSER_*` variable set, the default walk checks request limits, production-shaped screens,
the five tabs, the night timeline, the viewer, reader dates, peeks and session revocation.
It measures 390 px phone and 1440 px desktop screens. Run it under `flock /tmp/mdp-density.lock`
so only one density gate uses the host at a time.
Set `MDP_SHOWCASE_EVIDENCE_DIR` (relative to the repository root) to choose where screens land.
Choose one focused walk below by setting its variable to `1`, then run the same density command.

| Variable | Walk |
|---|---|
| `MDP_SHOWCASE_BROWSER_TRACE_ONLY` | Viewer and reader cards in the default walk. |
| `MDP_SHOWCASE_BROWSER_READERS_ONLY` | Reader cards, last-read dates and count links in the default walk. |
| `MDP_SHOWCASE_BROWSER_NO_DATA` | Missing music data in the default walk. |
| `BROWSER_RATE_ONLY` | Request buckets, concurrent tabs and retry states. |
| `BROWSER_STACK_ONLY` | Stack and onboarding screens, sheets and failures. |
| `BROWSER_SEARCH_ONLY` | Search results, ranking and sheets. |
| `BROWSER_CALLS_ONLY` | Picks and saved places. |
| `BROWSER_DRAFT_ONLY` | Weekly picks and rule selection. |
| `BROWSER_VIEWER_ONLY` | Picks, proof summaries and Weekly picks together. |
| `BROWSER_PROXY_ONLY` | Console proxy, forms and origin refusals. |
| `BROWSER_SSE_ONLY` | Event stream, reconnect and revocation. |
| `BROWSER_EDGES_ONLY` | Sparse data, counts and permission edge cases. |
| `BROWSER_PAGES_ONLY` | Production-shaped page fixtures in `test/browser/pages.ts`. |
| `BROWSER_CARDS_ONLY` | Cards, real covers and a matched song; needs network access and stays out of CI. |

The `BROWSER_*` switches select focused harness paths; some visit redirecting URLs.
The default walk starts with populated fixtures.
`BROWSER_STACK_DATED=1` omits the Stack artifact to check its dated fallback; pair it with
`BROWSER_STACK_ONLY=1`. Otherwise the harness collects the dated artifact used by the image.
`BROWSER_PREVIOUS_SHOWCASE_IMAGE=<image>` runs compatibility against a prior Docker image.
`MDP_SHOWCASE_BROWSER_TIME=<ISO timestamp>` pins the browser clock; the default is fixture-day noon UTC.
Database and port settings are above. Open `test/browser/run.ts` to inspect a walk before choosing it.

Stack (`/stack`) shows the platform's servers and databases: one hand-drawn picture of outside sources
flowing into the data platform and out to the platform's apps, then one card per deployed service in
the Data platform group, and a Planned row in text with one evidence line each.
Each card names its kind (Server, Database, On a timer), a tagline, a facts line of dated hand
facts from `ops/showcase/stack/facts.json`, provider marks, **See more** (a sheet), **Code
(private)** (the access note opens before the link) and **Open** where a screen exists. Measured
counts, the deployed revision for code links and the disk sizes come from the Stack artifact the
deploy context installs beside the server (`MDP_SHOWCASE_ARTIFACTS_DIR` overrides its directory);
without a valid artifact the cards render from the dated facts and say `Counts not refreshed at
this deploy.` An artifact that carries a private address, host, identifier or secret-looking string
is refused the same way. Status dots fill in after `/s/stack` runs its checks: each names its probe
and time, or says nothing checked it; a failed read shows `Status not checked at <time>` with Retry.
A check proves only what it names; no HTTP answer stands for job success.

Analyst onboarding (`/team`) offers a starter SQL question, links to analysis guides and the reviewed warehouse layers. The fixed query is in `lib/team-questions.ts` and `ops/showcase/queries.json`; the adapter check runs it as `showcase_wh`.

The avatar opens the operator console using the signed-in viewer’s key.

Viewer links disable automatic page prefetch. Opening one room does not silently query every other
room or use up the public request limit. Choose a view to load its facts.

Storage capture runs at 00:30 UTC through Next instrumentation. A control transaction holds the
singleton advisory lock while the app reads table counts and byte sizes with `showcase_wh`.
Catalog row estimates cannot exclude synthetic identities. Current and saved historical row totals
stay unknown; Holdings also suppresses estimates from older API responses. It records each
UTC day once. Busy or unknown collection defers the capture by a minute within that day. Missed days
stay missing. A failed capture calls the narrow functions alert endpoint, including admission and
connection failures. Functions resolves the warehouse independently; the job keeps that alert subject.
Failed alert delivery retries after 30 seconds, doubling to one hour between attempts. Delivery retries
do not repeat the capture. The pending retry lives in the app process. The app never inserts alerts itself. Open `/runbooks/showcase-inventory-failed` to inspect a missed capture.

Exact relation counts run at startup and every minute when `MDP_SHOWCASE_INVENTORY_ENABLED=1`.
`server/relation-counts.ts` counts only reviewed global relations after their cycle closes and their
build stamp commits. Busy or unknown runners defer the work. Each count locks the table, checks its
stamp and reads it in one snapshot as `showcase_wh`. A changed build publishes no count.
`control.showcase_relation_count` stores the exact count once per warehouse, relation and build key.
Missing counts stay unknown. Open a viewer count for its capture time and source details.

The local load check runs real Core and Workbench jobs with two identities and six tabs.
Open its setup, timings and lock waits to repeat it.


## Weekly picks

Open `/songs?view=friday` from Songs. This stable route opens the weekly selection and its saved results. Drag a cover into **your picks**, or tap it and choose **Pick this song**. Enter and Space open the same confirmation. Personal picks share the weekly limit.

Choose **See rules** to approve an example. Conditions come from a fixed list; arbitrary text and SQL are not accepted. The timer freezes the tray after its scheduled warehouse read and closes it at the weekly deadline. The schedule is defined in `lib/calls.ts` and `lib/draft.ts`; the page displays the next opening and deadline.

The timer and **Finish weekly picks** button share a transaction lock. A second close returns the committed result; a failed close rolls back every pick. Restart catches up open drafts. Warehouse downtime does not block closing an already frozen tray. Open a result to inspect its saved facts and later observations.

## Add a room read

1. Declare the mart's columns and grain in `dbt/models/marts/_marts__models.yml` before its SQL.
   Build the mart and generate its catalog. Follow [the generation commands](../../../docs/DEVELOPING.md#control-checks-and-generated-types).
2. Generate the SDK with `pnpm --dir control --filter @mdp/data-sdk generate`.
   Add the route with `pnpm --dir control mdp scaffold api <mart>`.
   Run `pnpm --dir control exec vitest run apps/data-api/test/serving-inventory.test.ts` to check the serving inventory.
3. Add the read to `server/reads.ts` with its runtime schema, columns, range and limit.
   Keep its citation on the read result. Add the exact relations and grants to
   `ops/showcase/queries.json`. See the query guide.
4. Run `MDP_PG_PORT=<free-port> bash ops/showcase/check-adapters.sh` on an initialized local stack.
   Add a caller test that checks the selected columns and copied citation.
5. Build with `pnpm --dir control --filter @mdp/showcase build`.
   Run `flock /tmp/mdp-density.lock pnpm --dir control --filter @mdp/showcase density`
   with `MDP_SHOWCASE_BROWSER_DB` set to the disposable local owner URL.
   Open the captured phone and laptop screens before submitting the branch.
