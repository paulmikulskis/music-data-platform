import { stageMarksWalk } from "./stage-marks";
import { platformSources } from "@mdp/contracts/platform";
import { glyphsWalk } from "./glyphs";
import { teamLinksWalk } from "./team-links";
import { privateLinkChunks } from "./link-privacy";
import { rateWalk } from "./rate";
import { seedLineage } from "./lineage-fixture";
import { lineageWalk } from "./lineage-layout";
import { viewerReadersWalk } from "./viewer-readers";
import { productionWalk } from "./production";
import { syntheticSourcesAt } from "../synthetic-fixture";
import { redesignWalk } from "./redesign";
import { nightFixture } from "./night-fixture";
import { checkBrowserClock, pinBrowserClock, withServerTime } from "./clock";
import { edgesWalk, seedEdges } from "./edges";
import { searchWalk, seedSearch } from "./search";
import { draftWalk } from "./draft";
import { callsWalk, freezeVisibleCard } from "./calls";
import {
  pagesWalk,
  seedPages,
  shapedHoldings,
  shapedReadiness,
  shapedSongDays,
  shapedSources,
} from "./pages";
import { seed } from "./csv";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { createWriteStream, readFileSync, rmSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";
import assert from "node:assert/strict";
import postgres from "postgres";
import { chromium, type Page } from "@playwright/test";
import { mint } from "@mdp/showcase-auth";
import { z } from "zod";
import { navigation } from "./navigation";
import { stackWalk } from "./stack";
import { prepareStackArtifacts } from "./stack-artifacts";
import { cardsDataApi, cardsWalk, seedCards } from "./cards";
import { proxyChecks } from "./proxy-checks";
import { proofSummary } from "./proof";
import { density, overlay, densityPolicy } from "./density.mjs";
import {
  pressAndHold,
  provenanceWalk,
  recordHover,
  type Overlay,
} from "./provenance";
import {
  at,
  build,
  movers,
  days,
  keys,
  holdings,
  arrivals,
  earlySignals,
  readiness,
  sources,
  copies,
  artistIdentity,
  chartPlaces,
} from "./fixtures";
if (process.env.MDP_SHOWCASE_BROWSER_GLYPHS_ONLY) {
  const evidence = z
    .array(
      z
        .object({
          relation: z.string(),
          row_key: z.record(z.string(), z.unknown()),
        })
        .passthrough(),
    )
    .parse(JSON.parse(movers[0].evidence));
  const playlist = evidence.find(
    (entry) => entry.relation === "mart_playlist_events",
  );
  assert(playlist);
  movers[0].evidence = JSON.stringify([
    evidence[0],
    { ...playlist, row_key: { ...playlist.row_key, platform: "apple_music" } },
    ...evidence.slice(1),
  ]);
}
const capturedSources = process.env.MDP_SHOWCASE_BROWSER_SOURCES
  ? platformSources.parse(
      JSON.parse(
        readFileSync(process.env.MDP_SHOWCASE_BROWSER_SOURCES, "utf8"),
      ),
    )
  : undefined;
const browserTime = z.iso
  .datetime()
  .parse(
    process.env.MDP_SHOWCASE_BROWSER_TIME ?? `${at.slice(0, 10)}T12:00:00.000Z`,
  );
// Screens and logs land in the named evidence folder, relative to the repository root.
const out = path.resolve(
  "../../..",
  process.env.MDP_SHOWCASE_EVIDENCE_DIR ?? "ops/evidence/showcase-app",
);
await mkdir(out, { recursive: true });
const databaseURL =
  process.env.MDP_SHOWCASE_BROWSER_DB ??
  "postgresql://postgres:postgres@127.0.0.1:5508/control";
assert(
  ["127.0.0.1", "localhost"].includes(new URL(databaseURL).hostname),
  "Browser fixtures require a loopback database. Use a disposable local container.",
);
const db = postgres(databaseURL, { onnotice: () => {} });
const warehouseURL = new URL(databaseURL);
warehouseURL.pathname = "/warehouse";
const wh = postgres(warehouseURL.toString(), { onnotice: () => {} });
// The app port and the fixture upstream just above it. Override when another run holds them.
const appPort = Number(process.env.MDP_SHOWCASE_BROWSER_PORT ?? "3108");
const upstreamURL = `http://127.0.0.1:${appPort + 1}`;
const origin = `http://127.0.0.1:${appPort}`;
const previousImage = process.env.BROWSER_PREVIOUS_SHOWCASE_IMAGE;
if (previousImage)
  assert.match(
    previousImage,
    /^registry\.fly\.io\/mdp-showcase@sha256:[a-f0-9]{64}$/,
  );
const imageContainer = `showcase-previous-${appPort}`;
const key = "showcase-browser-test-key";
const person = {
  handle: "fixture",
  display_name: "Test viewer",
  email: "fixture@example.invalid",
  admin_key: key,
  api_key_id: "00000000-0000-4000-8000-000000000001",
};
const refusedPerson = {
  ...person,
  handle: "refused",
  admin_key: "revoked-fixture-key",
  api_key_id: "00000000-0000-4000-8000-000000000002",
};
Object.assign(process.env, {
  MDP_SHOWCASE_PEOPLE: JSON.stringify([person, refusedPerson]),
  MDP_SHOWCASE_LINK_SECRET: "l".repeat(32),
  MDP_SHOWCASE_SESSION_SECRET: "s".repeat(32),
  MDP_SHOWCASE_ORIGIN: origin,
  MDP_CONTROL_RT_URL: databaseURL,
  MDP_CONTROL_DATABASE_URL: databaseURL,
  MDP_AUTH_MODE: "production",
  MDP_TRUSTED_BROWSER_ORIGINS: origin,
  MDP_SERVICE_URL: upstreamURL,
  MDP_SERVICE_TOKEN: "fixture-service",
  MDP_WORKBENCH_URL: upstreamURL,
  MDP_WORKBENCH_SERVICE_TOKEN: "fixture-service",
});
await db`INSERT INTO control.api_key(id, key_hash, label, role) VALUES (${person.api_key_id}, ${createHash("sha256").update(key).digest("hex")}, 'browser-test', 'admin') ON CONFLICT (id) DO UPDATE SET key_hash=EXCLUDED.key_hash, revoked_at=NULL`;
await db`INSERT INTO control.api_key(id,key_hash,label,role,revoked_at) VALUES (${refusedPerson.api_key_id},${createHash("sha256").update(refusedPerson.admin_key).digest("hex")},'showcase-refused-fixture','admin',now()) ON CONFLICT(id) DO UPDATE SET revoked_at=now()`;
// Each browser run starts with a new visit baseline, including after another local proof.
await db`TRUNCATE control.showcase_seen, control.showcase_call_rule, control.showcase_rule, control.showcase_call, control.showcase_draft CASCADE`;
// Every stamp the call and draft reads check shares one closed cycle, as after a daily build.
// A dbt build on the same warehouse (check-adapters in CI) stamps cycle 'local' with no close
// number, so each fixture upsert replaces the whole stamp, as mdp_record_build does.
await wh.unsafe(`CREATE SCHEMA IF NOT EXISTS marts; CREATE SCHEMA IF NOT EXISTS reference; CREATE SCHEMA IF NOT EXISTS explore_intermediate; CREATE SCHEMA IF NOT EXISTS intermediate;
DROP TABLE IF EXISTS reference.rights_registry,intermediate.int_song_key__daily,intermediate.int_song_cluster__daily,intermediate.int_artist_identity,marts.mart_top_movers,marts.mart_arrivals_current,marts.mart_early_signals_current,marts.mart_song_day,marts.mart_shazam_chart_daily CASCADE;
CREATE TABLE IF NOT EXISTS marts._build (relation text primary key, cycle_id uuid, close_no bigint, built_at timestamptz);
CREATE TABLE marts.mart_arrivals_current (song_key text, cluster_key text, member_song_keys text DEFAULT '[]', rank bigint, day date, title_text text, artist_text text, markets text, discovery_entries bigint DEFAULT 0, entered_lists bigint, entered_charts bigint, reason_rule text, evidence text, window_days integer, chart_spread_gain bigint, list_reach_tier integer, market_count bigint, last_entered_at timestamptz, movement_list text, age_class text, age_basis text, artist_stage text, artist_stage_basis text, learning_eligible boolean, resale_permitted boolean, source_keys text);
CREATE TABLE marts.mart_early_signals_current (family text, rank bigint, day date, song_key text, title_text text, artist_text text, component text, value double precision, reason_rule text, evidence text, learning_eligible boolean, resale_permitted boolean, source_keys text, window_days integer, chart_spread_gain bigint, list_reach_tier integer, market_count bigint, last_entered_at timestamptz, movement_list text, age_class text, age_basis text, artist_stage text, artist_stage_basis text);
ALTER TABLE marts.mart_arrivals_current OWNER TO dbt_transform;
ALTER TABLE marts.mart_early_signals_current OWNER TO dbt_transform;
CREATE TABLE IF NOT EXISTS reference.rights_registry (source_key text, learning_eligible boolean, resale_permitted boolean, review_required boolean);
TRUNCATE reference.rights_registry;
DO $fixture$ BEGIN IF EXISTS (SELECT 1 FROM pg_class WHERE oid=to_regclass('explore_intermediate.int_song_key__daily') AND relkind='r') THEN DROP TABLE explore_intermediate.int_song_key__daily; END IF;
 IF EXISTS (SELECT 1 FROM pg_class WHERE oid=to_regclass('explore_intermediate.int_artist_identity') AND relkind='r') THEN DROP TABLE explore_intermediate.int_artist_identity; END IF;
 IF EXISTS (SELECT 1 FROM pg_class WHERE oid=to_regclass('explore_intermediate.int_song_cluster__daily') AND relkind='r') THEN DROP TABLE explore_intermediate.int_song_cluster__daily; END IF; END $fixture$;
CREATE TABLE IF NOT EXISTS intermediate.int_song_key__daily(platform text, platform_track_id text, resolved boolean, song_key text, source_keys text, primary_artist_id text, primary_artist_key text, title_text text, artist_text text);
TRUNCATE intermediate.int_song_key__daily;
INSERT INTO intermediate.int_song_key__daily SELECT platform, platform_track_id, true, '${keys[0]}', '["sp_playlist","sz_chart"]', CASE WHEN platform='spotify' THEN '${artistIdentity.platform_artist_id}' END, 'fixture-artist' FROM jsonb_to_recordset('${JSON.stringify(copies)}'::jsonb) AS c(platform text, platform_track_id text);
INSERT INTO intermediate.int_song_key__daily SELECT 'spotify', 'lead-'||k, true, k, '["sp_playlist","am_playlist"]', '${artistIdentity.platform_artist_id}', 'fixture-artist' FROM unnest(ARRAY['${keys[1]}','${keys[2]}','fixture-arrival-1','fixture-arrival-2','fixture-catalog-1','fixture-catalog-2','fixture-early-1','fixture-early-2','fixture-early-3','fixture-early-4']) k;
INSERT INTO intermediate.int_song_key__daily SELECT 'test source', 'tracked-'||n::text, true, 'fixture-song-'||n::text FROM generate_series(1,320) n;
CREATE TABLE intermediate.int_song_cluster__daily(song_key text, cluster_key text, cluster_methods text, cluster_confidence double precision, member_song_keys text, source_keys text);
INSERT INTO intermediate.int_song_cluster__daily SELECT k, '${keys[0]}', '["title_artist_duration"]', 0.9, '${JSON.stringify([keys[0], keys[1]].sort())}', '["sp_playlist","sz_chart"]' FROM unnest(ARRAY['${keys[0]}','${keys[1]}']) k;
CREATE TABLE intermediate.int_artist_identity(platform text, platform_artist_id text, mb_artist_gid text, mb_artist_name text, candidate_count bigint, method text, evidence text, wikidata_qid text);
INSERT INTO intermediate.int_artist_identity SELECT * FROM jsonb_populate_record(NULL::intermediate.int_artist_identity, '${JSON.stringify(artistIdentity)}'::jsonb);
CREATE TABLE marts.mart_song_day(song_key text, day date, title_text text, artist_text text, editorial_adds double precision, algorithmic_adds double precision, shazam_cities bigint, stream_rate double precision, billboard_position integer, list_count bigint, shazam_charts bigint);
INSERT INTO marts.mart_song_day(song_key, day, title_text, artist_text, list_count) SELECT '${keys[0]}', (now() AT TIME ZONE 'UTC')::date - n, 'Night tide', 'Test recording', CASE WHEN n <= 3 THEN 2 END FROM generate_series(0,27) n;
INSERT INTO marts.mart_song_day VALUES ('${keys[1]}', CURRENT_DATE, 'Slow return', 'Test recording'), ('${keys[2]}', CURRENT_DATE, 'After the rain', 'Test recording');
CREATE TABLE marts.mart_shazam_chart_daily(chart text, chart_date date, chart_type text, country text, city text, position integer, apple_song_id text, source_keys text, title_text text, artist_text text);
INSERT INTO marts.mart_shazam_chart_daily SELECT chart, chart_date, chart_type, country, city, position, '${copies[1].platform_track_id}', '["sz_chart"]' FROM jsonb_to_recordset('${JSON.stringify(chartPlaces)}'::jsonb) AS c(chart text, chart_date date, chart_type text, country text, city text, position integer);
INSERT INTO marts._build SELECT relation, '${build("x").cycle_id}'::uuid,41,'${at}'::timestamptz FROM unnest(ARRAY['intermediate.int_song_key__daily','intermediate.int_song_cluster__daily','intermediate.int_artist_identity','reference.rights_registry','marts.mart_arrivals_current','marts.mart_early_signals_current','marts.mart_song_day','marts.mart_shazam_chart_daily','intermediate.int_cluster_entries__daily','intermediate.int_playlist__snapshots']) relation ON CONFLICT (relation) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,close_no=EXCLUDED.close_no,built_at=EXCLUDED.built_at;
ALTER TABLE reference.rights_registry OWNER TO dbt_transform;
ALTER TABLE intermediate.int_song_key__daily OWNER TO dbt_transform;
ALTER TABLE intermediate.int_song_cluster__daily OWNER TO dbt_transform;
ALTER TABLE intermediate.int_artist_identity OWNER TO dbt_transform;
ALTER TABLE marts.mart_song_day OWNER TO dbt_transform;
ALTER TABLE marts.mart_shazam_chart_daily OWNER TO dbt_transform;
GRANT USAGE ON SCHEMA marts,reference,explore_intermediate TO showcase_wh;
GRANT SELECT ON ALL TABLES IN SCHEMA marts,reference,explore_intermediate TO showcase_wh;`);
await seedSearch(wh);
// The rights register as reviewed: every source with its two permissions.
await wh.unsafe(
  // An empty CSV cell is NULL, as dbt seed loads it.
  `INSERT INTO reference.rights_registry SELECT source_key, nullif(learning_eligible, '')::boolean,
     nullif(resale_permitted, '')::boolean, nullif(review_required, '')::boolean
   FROM jsonb_to_recordset($1::text::jsonb) AS r(source_key text, learning_eligible text, resale_permitted text, review_required text)`,
  [JSON.stringify(seed("rights_registry.csv"))],
);
const pagesMode = !!process.env.BROWSER_PAGES_ONLY;

// The fixture walk reads the synthetic register.
if (pagesMode) await seedPages(wh);
await wh`INSERT INTO marts.mart_arrivals_current ${wh(arrivals)}`;
await wh`INSERT INTO marts.mart_early_signals_current ${wh(earlySignals)}`;

await db`INSERT INTO control.streamline(source_key,layer,cadence_tag) VALUES ('browser_fixture','bronze','daily') ON CONFLICT (source_key) DO NOTHING`;
await db`INSERT INTO control.run(kind,work_key,scope,streamline_id,warehouse_id,status) SELECT 'invoke','browser-fixture-poll','global',s.id,w.id,'running' FROM control.streamline s CROSS JOIN control.warehouse w WHERE s.source_key='browser_fixture' AND w.is_production ON CONFLICT(work_key) DO UPDATE SET status='running'`;
await db`INSERT INTO control.run(id,kind,work_key,scope,streamline_id,warehouse_id,status)
  SELECT ids.id::uuid,'invoke','night-browser-'||ids.id,'global',s.id,w.id,'failed'
  FROM unnest(ARRAY['00000000-0000-4000-8000-000000000061','00000000-0000-4000-8000-000000000062']) ids(id)
  CROSS JOIN control.streamline s CROSS JOIN control.warehouse w
  WHERE s.source_key='browser_fixture' AND w.is_production ON CONFLICT(id) DO NOTHING`;
await wh.unsafe(
  `CREATE TABLE IF NOT EXISTS marts.mart_top_movers(rank bigint,day date,song_key text,title_text text,artist_text text,reason_rule text,ranking_build text,coverage text,evidence text,learning_eligible boolean,resale_permitted boolean,source_keys text,momentum_score numeric,score_parts text,window_days integer,age_basis text); ALTER TABLE marts.mart_top_movers ADD COLUMN IF NOT EXISTS movement_list text NOT NULL DEFAULT 'new_entries'; ALTER TABLE marts.mart_top_movers ADD COLUMN IF NOT EXISTS window_days integer; TRUNCATE marts.mart_top_movers; ALTER TABLE marts.mart_top_movers OWNER TO dbt_transform; GRANT SELECT ON marts.mart_top_movers TO showcase_wh;`,
);
for (const row of movers)
  await wh`INSERT INTO marts.mart_top_movers ${wh(row, "movement_list", "rank", "day", "song_key", "title_text", "artist_text", "reason_rule", "ranking_build", "coverage", "evidence", "learning_eligible", "resale_permitted", "source_keys", "momentum_score", "score_parts", "window_days", "age_basis")}`;
await wh`INSERT INTO marts._build(relation,cycle_id,close_no,built_at) VALUES ('marts.mart_top_movers',${build("x").cycle_id},41,${at}) ON CONFLICT(relation) DO UPDATE SET cycle_id=EXCLUDED.cycle_id,close_no=EXCLUDED.close_no,built_at=EXCLUDED.built_at`;
await seedLineage(wh, db);
if (process.env.BROWSER_CARDS_ONLY) await seedCards(wh);
if (process.env.BROWSER_EDGES_ONLY) await seedEdges(wh);
// The real operator console handles forms and its own authentication. Only new data RPCs use fixtures.
// The control app renders Hono JSX; an opaque specifier keeps it out of the React typecheck.
const controlApp = "../../../control-api/src/app";
const { app } = await import(controlApp);
let runnerState = "idle";
let dataUnavailable = process.env.MDP_SHOWCASE_BROWSER_NO_DATA === "1";
// The first pass shows Home and Rising before any song moves on two sources.
const redesign = !Object.keys(process.env).some(
  (key) => key.startsWith("BROWSER_") && process.env[key],
);
const firstPass =
  !redesign &&
  !previousImage &&
  !process.env.BROWSER_EDGES_ONLY &&
  !process.env.BROWSER_PROXY_ONLY &&
  !process.env.BROWSER_SSE_ONLY &&
  !process.env.BROWSER_SEARCH_ONLY &&
  !process.env.BROWSER_CALLS_ONLY &&
  !process.env.BROWSER_DRAFT_ONLY &&
  !process.env.BROWSER_CARDS_ONLY &&
  !process.env.BROWSER_STACK_ONLY &&
  !process.env.BROWSER_RATE_ONLY &&
  !pagesMode;
let moversEmpty = firstPass || !!process.env.BROWSER_EDGES_ONLY;
let youngHistory = firstPass;
const requests: { method: string; path: string }[] = [];
const functionReads: { metadata: boolean }[] = [];
// The data API answers a song's days only for a song the warehouse fixture holds, as production
// does, so a key that reaches it still percent-encoded finds nothing. The three fixture songs keep
// their shared days; any other held song reads under its own title.
async function heldSongDays(song: string) {
  const [held] = await wh<{ title_text: string | null }[]>`SELECT title_text
    FROM intermediate.int_song_key__daily WHERE song_key = ${song} LIMIT 1`;
  if (!held) return [];
  if (pagesMode) return shapedSongDays(song);
  return days.map((d) => ({
    ...d,
    song_key: song,
    title_text: keys.includes(song)
      ? d.title_text
      : (held.title_text ?? d.title_text),
  }));
}
const upstream = createServer(async (req, res) => {
  try {
    const url = new URL(req.url!, upstreamURL);
    requests.push({ method: req.method!, path: url.pathname });
    if (url.pathname === "/workbench" && url.searchParams.has("test_outage")) {
      res.writeHead(503);
      res.end("Fixture outage. Read source details.");
      return;
    }
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(Buffer.from(c));
    const body = Buffer.concat(chunks);
    if (/^\/v1\/runs\/[0-9a-f-]{36}$/.test(url.pathname)) {
      const [run] =
        await db`SELECT * FROM control.run WHERE id=${url.pathname.split("/").at(-1)!}`;
      assert(run);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ run, receipts: [], repairs_pending: 0 }));
      return;
    }
    if (url.pathname === "/ops" && url.searchParams.has("hostile")) {
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end(
        '<html><head><base href="https://foreign.invalid/"><style>div,nav,a,[data-showcase-navigation]{display:none!important;opacity:0!important}</style></head><body><div>Replaceable content</div><script>setTimeout(()=>document.querySelectorAll("div,nav,a").forEach(node=>node.remove()),50)</script></body></html>',
      );
      return;
    }
    const sourceKey = url.pathname.split("/").at(-1) ?? "";
    if (
      url.pathname.startsWith("/v1/functions/") &&
      ["browser_fixture", "sz_chart", "sp_playlist"].includes(sourceKey)
    ) {
      functionReads.push({
        metadata: url.searchParams.get("metadata_only") === "true",
      });
      const [manifest] =
        await db`SELECT * FROM control.streamline WHERE source_key=${sourceKey}`;
      const runs =
        await db`SELECT * FROM control.run WHERE streamline_id=${manifest.id}`;
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          manifest,
          last_runs: runs,
          receipts: runs.map(() => []),
          output_preview: [],
          rejected_sample: [],
          fingerprint_history: [],
          row_counts: [],
          log_url: `/functions/${sourceKey}/logs`,
        }),
      );
      return;
    }
    if (url.pathname === "/v1/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"status":"ok"}');
      return;
    }
    if (url.pathname === "/api/browser-download") {
      res.writeHead(200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=fixture.csv",
      });
      res.end("fixture\n1\n");
      return;
    }
    if (url.pathname.startsWith("/v1/workbench/")) {
      const action = url.pathname.split("/").at(-1);
      const input = JSON.parse(body.toString());
      const payloads: Record<string, unknown> = {
        createSession: {
          sessionId: "00000000-0000-4000-8000-000000000201",
          scratchSchema: "wb_fixture",
        },
        models: {
          models: ["mart_chart_history"],
          cycles: ["00000000-0000-4000-8000-000000000041"],
          cycleDetails: [],
          sources: [],
        },
        draft: { model: "mart_chart_history", sql: "select 1 as fixture" },
        history: { runs: [] },
        query: {
          runId: "00000000-0000-4000-8000-000000000202",
          status: "running",
          progress: 0,
          error: null,
        },
        previewModel: {
          runId: "00000000-0000-4000-8000-000000000203",
          status: "running",
          progress: 0,
          error: null,
        },
        explain: {
          compiledSql: "select 1 as fixture",
          upstream: [],
          downstream: [],
          sourceFreshness: [],
          producingRuns: [],
        },
        status: {
          runId: input.runId,
          status: "running",
          progress: 0,
          error: null,
        },
      };
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(payloads[action!] ?? {}));
      return;
    }
    if (
      dataUnavailable &&
      (url.pathname.startsWith("/rpc/mart_") ||
        url.pathname === "/rpc/platform/sources")
    ) {
      res.writeHead(503);
      res.end("Fixture outage. Retry shortly.");
      return;
    }
    
    if (
      req.headers["x-api-key"] !== refusedPerson.admin_key &&
      (url.pathname.startsWith("/rpc/platform/") ||
        url.pathname.startsWith("/rpc/mart_"))
    ) {
      const input = body.length
        ? (JSON.parse(body.toString()).json ?? {})
        : (JSON.parse(url.searchParams.get("data") ?? "{}").json ?? {});
      const name = url.pathname.split("/").at(-1);
      const songDays =
        name === "mart_song_day" && !process.env.BROWSER_CARDS_ONLY
          ? await heldSongDays(String(input.filters?.song_key ?? keys[0]))
          : [];
      let json: unknown;
      if (name === "events")
        json = {
          events: [],
          next_cursor: "fixture-cursor",
          overlap_start: null,
          has_more: false,
          runner: {
            state: runnerState,
            busy: runnerState !== "idle",
            sessions: [],
            next_scheduled_at: new Date(Date.now() + 3600000).toISOString(),
            next_step: "Open /ops.",
          },
          next_step: "Open /ops.",
        };
      else if (name === "night") json = nightFixture(input);
      else if (name === "holdings")
        json = pagesMode ? shapedHoldings : holdings;
      else if (name === "sources")
        json = {
          queried_at: at,
          sources:
            redesign || process.env.BROWSER_STACK_ONLY
              ? syntheticSourcesAt(Date.now(), capturedSources).sources
              : pagesMode
                ? shapedSources
                : sources,
          next_step: "Open /functions.",
        };
      else
        json = {
          rows:
            (process.env.BROWSER_CARDS_ONLY
              ? cardsDataApi(name!, input)
              : null) ??
            (name === "mart_top_movers_current"
              ? moversEmpty
                ? []
                : (pagesMode
                      ? movers.slice(1)
                      : movers
                  ).filter(
                    (row) =>
                      !input.filters?.song_key ||
                      row.song_key === input.filters.song_key,
                  )
              : name === "mart_song_cluster_members"
                ? [
                    {
                      // A song's group read asks by representative; each key is its own group
                      // here, so no song borrows another's movement or days.
                      song_key:
                        input.filters?.song_key ??
                        input.filters?.representative_song_key,
                      cluster_key:
                        input.filters?.song_key ??
                        input.filters?.representative_song_key,
                      representative_song_key:
                        input.filters?.song_key ??
                        input.filters?.representative_song_key,
                      cluster_methods:
                        '["isrc_crosswalk","title_artist_duration"]',
                      learning_eligible: false,
                      resale_permitted: false,
                      source_keys: '["sp_playlist","sz_chart"]',
                    },
                  ]
                : name === "mart_readiness"
                  ? pagesMode
                    ? shapedReadiness
                    : readiness(youngHistory)
                  : name === "mart_song_day"
                    ? songDays
                    : []),
          next_cursor: null,
          build: build(name!),
          labels: {},
        };
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ json }));
      return;
    }
    const response = await app.fetch(
      new Request(url, {
        method: req.method,
        headers: Object.entries(req.headers).flatMap(
          ([name, value]): [string, string][] =>
            value === undefined
              ? []
              : [[name, Array.isArray(value) ? value.join(",") : value]],
        ),
        body: ["GET", "HEAD"].includes(req.method!) ? undefined : body,
      }),
    );
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(500);
    res.end("Test upstream failed. Inspect the test log.");
  }
});
await new Promise<void>((resolve) =>
  upstream.listen(appPort + 1, "127.0.0.1", resolve),
);
const roleURL = (role: string, database: string) => {
  const url = new URL(databaseURL);
  url.username = role;
  url.password = role;
  url.pathname = "/" + database;
  return url.toString();
};
const log = createWriteStream(path.join(out, "browser-server.log"));
// The app runs with a validated Stack artifact beside it, as the image does. BROWSER_STACK_DATED=1
// leaves it out to walk the dated fallback instead.
const stackArtifacts = prepareStackArtifacts(
  path.resolve("../../.."),
  spawnSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).stdout.trim(),
);
const childEnv = {
  ...process.env,
  ...(stackArtifacts ? { MDP_SHOWCASE_ARTIFACTS_DIR: stackArtifacts } : {}),
  MDP_CONTROL_API_URL: upstreamURL,
  MDP_DATA_API_URL: upstreamURL,
  MDP_SHOWCASE_READER_KEY: "fixture-reader",
  MDP_CONTROL_RT_URL: roleURL("control_rt", "control"),
  MDP_SHOWCASE_WH_URL: roleURL("showcase_wh", "warehouse"),
};
const child = spawn(
  previousImage ? "docker" : process.execPath,
  previousImage
    ? [
        "run",
        "--rm",
        "--name",
        imageContainer,
        "--network",
        "host",
        "-e",
        `PORT=${appPort}`,
        "-e",
        "HOSTNAME=127.0.0.1",
        ...Object.keys(childEnv)
          .filter((name) => name.startsWith("MDP_"))
          .flatMap((name) => ["-e", name]),
        previousImage,
      ]
    : [
        fileURLToPath(import.meta.resolve("next/dist/bin/next")),
        "start",
        "--hostname",
        "127.0.0.1",
        "--port",
        String(appPort),
      ],
  {
    env: childEnv,
    stdio: ["ignore", "pipe", "pipe"],
  },
);
child.stdout.pipe(log);
child.stderr.pipe(log);
let browser: Awaited<ReturnType<typeof chromium.launch>> | undefined;
try {
  for (let n = 0; n < 80; n++) {
    try {
      if ((await fetch(origin + "/healthz")).ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  await privateLinkChunks();
  browser = await chromium.launch({ headless: true });
  if (
    process.env.BROWSER_RATE_ONLY ||
    (redesign && !process.env.MDP_SHOWCASE_BROWSER_GLYPHS_ONLY)
  ) {
    await rateWalk(
      browser,
      origin,
      out,
      () => mint(db, person.handle),
      browserTime,
    );
  }
  const measurements: Record<string, ReturnType<typeof density>> = {};
  const overlays: Record<string, Overlay> = {};
  // Measure the newest open sheet or hover card against its word budget.
  const measureOverlay = async (
    page: Page,
    name: string,
    kind: "popover" | "sheet" = "sheet",
  ) => {
    await page.waitForTimeout(250);
    overlays[name] = {
      kind,
      ...(await page.evaluate(overlay, { kind: kind, policy: densityPolicy })),
    };
    assert(overlays[name]!.found, `${name} opens its ${kind}.`);
  };
  // Before the first two-source song: Home leads with arrivals and Rising shows early signals.
  if (firstPass) {
    for (const viewport of [
      { width: 390, height: 844 },
      { width: 1440, height: 900 },
    ]) {
      const size = `${viewport.width}x${viewport.height}`;
      const context = await browser.newContext({ viewport, timezoneId: "UTC" });
      const page = await context.newPage();
      await pinBrowserClock(page, browserTime);
      if (process.env.BROWSER_EDGES_ONLY) {
        page.on("pageerror", (error) =>
          console.log("browser error", error.message),
        );
        page.on("response", (response) => {
          if (response.status() >= 400)
            console.log(
              "HTTP",
              response.status(),
              new URL(response.url()).pathname,
            );
        });
      }
      page.setDefaultTimeout(20000);
      page.setDefaultNavigationTimeout(30000);
      await page.route("**/art/**", (route) =>
        route.fulfill({
          contentType: "image/svg+xml",
          body: `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"><rect width="800" height="800" fill="#1b2a30"/><circle cx="420" cy="330" r="200" fill="#a9c885" opacity=".55"/><path d="M0 610Q260 420 470 560T800 470V800H0" fill="#e8b16d" opacity=".35"/><text x="55" y="65" fill="#edf4f6" font-family="sans-serif" font-size="18" letter-spacing="5">TEST ART</text></svg>`,
        }),
      );
      await page.goto(await mint(db, person.handle));
      if (
        !(await page
          .getByRole("button", { name: "Sign in", exact: true })
          .count())
      ) {
        await page.screenshot({ path: path.join(out, "sign-in-failure.png") });
        console.log("Sign-in page:", await page.locator("body").innerText());
      }
      await withServerTime(page, async () => {
        await page
          .getByRole("button", { name: "Sign in", exact: true })
          .click();
      });
      await page.waitForURL(previousImage ? "**/today" : origin + "/");
      await page.waitForTimeout(1200);
      assert.equal(
        await page.locator("h1").count(),
        1,
        "Home renders arrivals",
      );
      await page
        .getByText("two-source ranking from", { exact: false })
        .waitFor();
      await page
        .locator(".arrival-caption")
        .getByText("UK · US · Canada")
        .waitFor();
      assert.equal(
        await page.locator(".arrivals .value").innerText(),
        "296",
        "Home counts new arrivals only.",
      );
      await page.screenshot({
        path: path.join(out, `home-arrivals-${size}.png`),
      });
      if (viewport.width === 390)
        measurements["home-arrivals"] = await page.evaluate(
          density,
          densityPolicy,
        );
      await page.locator(".arrival-caption .countries").hover();
      await page.locator(".hover-card .city-map").waitFor();
      await page.screenshot({
        path: path.join(out, `hover-discovery-${size}.png`),
      });
      await measureOverlay(page, `hover-discovery-${size}`, "popover");
      await page.mouse.move(2, 2);
      await page.locator(".arrivals .metric-button").click();
      await page
        .getByText("Older songs sit in Places", {
          exact: false,
        })
        .waitFor();
      await page.waitForTimeout(250);
      await page.screenshot({
        path: path.join(out, `home-arrivals-proof-${size}.png`),
      });
      await measureOverlay(page, `home-arrivals-how-${size}`);
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await freezeVisibleCard(page, db, "arrival", [
        { country: "united-kingdom", city: null },
        { country: "united-states", city: null },
        { country: "canada", city: null },
      ]);
      await page.goto(origin + "/rising");
      await page.waitForTimeout(1200);
      assert.deepEqual(
        await page.locator(".family-name").allInnerTexts(),
        ["PLAYLISTS", "SHAZAM CITIES", "PLAYS"],
        "Rising groups early signals by family.",
      );
      assert.equal(
        await page.getByText("Catalog fixture", { exact: true }).count(),
        0,
        "Rising shows new songs only.",
      );
      await page.getByText("Seen on 3 playlists.", { exact: true }).waitFor();
      const unknownCard = page
        .locator("[data-card]")
        .filter({ hasText: "age unknown" })
        .first();
      await unknownCard.getByText("new list", { exact: true }).waitFor();
      await unknownCard.screenshot({
        path: path.join(out, `rising-age-unknown-${size}.png`),
      });
      await page.screenshot({
        path: path.join(out, `rising-early-${size}.png`),
      });
      if (viewport.width === 390)
        measurements["rising-early"] = await page.evaluate(
          density,
          densityPolicy,
        );
      await page.getByText("Seen on 3 playlists.", { exact: true }).click();
      await page
        .getByText("Counted from the tracked editorial", { exact: false })
        .waitFor();
      await page.screenshot({
        path: path.join(out, `rising-early-source-${size}.png`),
      });
      await measureOverlay(page, `song-card-source-${size}`);
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await freezeVisibleCard(page, db, "early", null);
      await context.close();
    }
    // Cached reads last thirty seconds. Wait them out before the two-source pass.
    moversEmpty = false;
    youngHistory = false;
    await new Promise((resolve) => setTimeout(resolve, 31000));
  }
  for (const viewport of process.env.BROWSER_RATE_ONLY
    ? []
    : [
        { width: 390, height: 844 },
        { width: 1440, height: 900 },
      ]) {
    const context = await browser.newContext({
      viewport,
      timezoneId: "UTC",
      recordVideo: undefined,
      // The production-shaped walk taps as a phone does.
      ...(pagesMode && viewport.width < 700
        ? { hasTouch: true, isMobile: true }
        : {}),
    });
    const page = await context.newPage();
    await pinBrowserClock(page, browserTime);
    console.log(
      `Browser clock ${browserTime}, UTC, ${viewport.width}px. Open the density evidence.`,
    );
    if (process.env.BROWSER_EDGES_ONLY) {
      page.on("pageerror", (error) =>
        console.log("browser error", error.message),
      );
      page.on("response", (response) => {
        if (response.status() >= 400)
          console.log(
            "HTTP",
            response.status(),
            new URL(response.url()).pathname,
          );
      });
    }
    page.setDefaultTimeout(20000);
    page.setDefaultNavigationTimeout(30000);
    if (process.env.BROWSER_SSE_ONLY) {
      page.on("response", (response) => {
        if (new URL(response.url()).pathname === "/events")
          console.log(
            "stream response",
            response.request().method(),
            response.status(),
          );
      });
      page.on("console", (message) =>
        console.log("browser", message.text().slice(0, 200)),
      );
    }

    // The cards pass reads real covers through the app's own /art route.
    if (!process.env.BROWSER_CARDS_ONLY)
      await page.route("**/art/**", (route) =>
        route.fulfill({
          contentType: "image/svg+xml",
          body: `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"><defs><radialGradient id="g"><stop stop-color="#789694"/><stop offset="1" stop-color="#23343d"/></radialGradient></defs><rect width="800" height="800" fill="url(#g)"/><circle cx="340" cy="300" r="170" fill="#18272c"/><path d="M0 580Q210 270 400 500T800 340V800H0" fill="#90aaa2" opacity=".5"/><path d="M0 600Q200 400 450 580T800 400" fill="none" stroke="#edf4f6" opacity=".6"/><text x="55" y="65" fill="#edf4f6" font-family="sans-serif" font-size="18" letter-spacing="5">TEST ART</text></svg>`,
        }),
      );
    if (previousImage) {
      await page.goto(await mint(db, person.handle));
      await withServerTime(page, async () => {
        await page
          .getByRole("button", { name: "Sign in", exact: true })
          .click();
      });
      await page.waitForURL(previousImage ? "**/today" : origin + "/");
      for (const route of [
        "/today",
        "/holdings",
        "/s/song/" + encodeURIComponent(keys[0]),
      ]) {
        const response = await page.goto(origin + route);
        assert.equal(response?.status(), 200);
        await page.locator("h1").waitFor();
        const content = await page.locator("main").innerText();
        assert(!/out of reach|something went wrong/i.test(content), route);
        if (route === "/holdings") assert.match(content, /playlists/i);
        if (route.includes("/s/song/"))
          assert.equal(await page.locator(".lane-value").count(), 3);
      }
      console.log(
        `Previous showcase image reads new source and song payloads at ${viewport.width}px. Open previous-showcase.log.`,
      );
      await context.close();
      continue;
    }
    await page.goto(origin + "/sign-in");
    await checkBrowserClock(page, browserTime);
    await page
      .getByText(
        "Open your sign-in link to continue. If you do not have one, ask the platform operator.",
        { exact: true },
      )
      .waitFor();
    assert.equal(await page.getByText(/Your sign-in ended/).count(), 0);
    await page.locator(".welcome").screenshot({
      path: path.join(out, `sign-in-no-link-${viewport.width}.png`),
    });
    await page.goto(await mint(db, person.handle));
    if (!process.env.BROWSER_PROXY_ONLY)
      await page.screenshot({
        path: path.join(
          out,
          `sign-in-${viewport.width}x${viewport.height}.png`,
        ),
      });
    // Both screen sizes share one IP. Start sign-in in a fresh rate-limit window.
    await page.waitForTimeout(1100);
    await withServerTime(page, async () => {
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
    });
    await page.waitForURL(previousImage ? "**/today" : origin + "/");
    await page.waitForTimeout(1100);
    if (redesign && !dataUnavailable) {
      await glyphsWalk(page, origin, out, viewport.width);
      if (process.env.MDP_SHOWCASE_BROWSER_GLYPHS_ONLY) {
        await context.close();
        continue;
      }
    }
    if (redesign) {
      const marksOnly =
        process.env.MDP_SHOWCASE_BROWSER_STAGE_MARKS_ONLY === "1";
      if (!dataUnavailable)
        await stageMarksWalk(page, origin, out, viewport.width);
      if (marksOnly) {
        await context.close();
        continue;
      }
      const readersOnly = process.env.MDP_SHOWCASE_BROWSER_READERS_ONLY === "1";
      if (!process.env.MDP_SHOWCASE_BROWSER_TRACE_ONLY && !readersOnly) {
        if (!dataUnavailable)
          await productionWalk(page, origin, out, viewport.width);
        await redesignWalk(page, origin, out, viewport.width, dataUnavailable);
      }
      if (!dataUnavailable)
        await viewerReadersWalk(page, origin, out, viewport.width);
      if (!dataUnavailable && !readersOnly)
        await lineageWalk(page, origin, out, viewport.width);
      const cookie = (await context.cookies()).find(
        (entry) => entry.name === "__Host-mdp_showcase",
      );
      assert(cookie);
      await db`UPDATE control.showcase_session SET revoked_at=now() WHERE id_hash=${createHash("sha256").update(cookie.value).digest("hex")}`;
      await page.goto(origin + "/sources");
      await page.waitForURL((url) => url.pathname === "/sign-in");
      assert.equal(await page.locator(".reviewed-source").count(), 0);
      await page.screenshot({
        path: path.join(
          out,
          `session-ended-${viewport.width}x${viewport.height}.jpg`,
        ),
        type: "jpeg",
        quality: 65,
      });
      console.log(
        `Session revocation clears viewer data at ${viewport.width}px.`,
      );
      await context.close();
      continue;
    }
    
    if (process.env.BROWSER_SSE_ONLY) {
      await page.goto(origin + "/songs?view=places");
      await page
        .getByRole("button", { name: "Collection. Open details", exact: true })
        .waitFor();
      const id = (await context.cookies()).find(
        (c) => c.name === "__Host-mdp_showcase",
      )!.value;
      const revoked =
        await db`UPDATE control.showcase_session SET revoked_at=now() WHERE id_hash=${createHash("sha256").update(id).digest("hex")} RETURNING handle`;
      assert.equal(revoked.length, 1);
      console.log(
        "head after revoke",
        await page.evaluate(() =>
          fetch("/events", { method: "HEAD" }).then((r) => r.status),
        ),
      );
      await page
        .waitForURL("**/sign-in?reason=ended", { timeout: 65000 })
        .catch(async (error) => {
          console.log("final path", page.url());
          throw error;
        });
      console.log("SSE revoke passes");
      await context.close();
      break;
    }
    if (process.env.BROWSER_EDGES_ONLY) {
      await edgesWalk(
        page,
        db,
        origin,
        out,
        `${viewport.width}x${viewport.height}`,
      );
      await context.close();
      continue;
    }
    if (process.env.BROWSER_PROXY_ONLY) {
      await proxyChecks(
        page,
        context,
        db,
        origin,
        `api-key:${person.api_key_id}`,
        requests,
      );
      await context.close();
      break;
    }
    if (process.env.BROWSER_STACK_ONLY) {
      await productionWalk(page, origin, out, viewport.width);
      await stackWalk(
        page,
        origin,
        out,
        viewport.width,
        process.env.BROWSER_STACK_DATED === "1",
      );
      await teamLinksWalk(
        page,
        origin,
        out,
        viewport.width,
        process.env.BROWSER_STACK_DATED === "1",
      );
      await context.close();
      continue;
    }
    if (process.env.BROWSER_SEARCH_ONLY) {
      await searchWalk(
        page,
        db,
        wh,
        origin,
        out,
        `${viewport.width}x${viewport.height}`,
        overlays,
      );
      await context.close();
      continue;
    }
    if (process.env.BROWSER_CARDS_ONLY) {
      await cardsWalk(
        page,
        origin,
        out,
        `${viewport.width}x${viewport.height}`,
        measurements,
      );
      await context.close();
      continue;
    }
    if (pagesMode) {
      await pagesWalk(
        page,
        origin,
        out,
        `${viewport.width}x${viewport.height}`,
        keys[0],
        overlays,
        measurements,
      );
      await context.close();
      continue;
    }
    if (process.env.BROWSER_DRAFT_ONLY) {
      await draftWalk(
        page,
        db,
        wh,
        origin,
        out,
        `${viewport.width}x${viewport.height}`,
      );
      await context.close();
      continue;
    }
    if (process.env.BROWSER_CALLS_ONLY) {
      await callsWalk(
        page,
        db,
        wh,
        origin,
        out,
        `${viewport.width}x${viewport.height}`,
        keys[0],
      );
      await context.close();
      continue;
    }
    const screens: [string, string][] = [
      ["home", "/today"],
      ["rising", "/rising"],
      ["rising28", "/rising?days=28"],
      ["song", `/s/song/${keys[0]}`],
      ["places", "/songs?view=places"],
      ["picks", "/picks"],
      ["holdings", "/holdings"],
    ];
    for (const [name, url] of screens) {
      console.log(`Checking ${name} at ${viewport.width}.`);
      await page.goto(origin + url);
      await page.waitForTimeout(1200);
      if ((await page.locator("h1").count()) !== 1)
        await page.screenshot({ path: path.join(out, `${name}-failure.png`) });
      assert.equal(
        await page.locator("h1").count(),
        1,
        `${name} renders its room: ${(await page.locator("main").innerText()).slice(0, 300)}`,
      );
      await page.screenshot({
        path: path.join(
          out,
          `${name}-${viewport.width}x${viewport.height}.png`,
        ),
      });
      assert.equal(
        await page.locator(".view-nav [aria-current]").count(),
        name === "home" ? 0 : 1,
      );
      const quiet = await page
        .locator(".view-nav a:not([aria-current]) .view-symbol")
        .evaluateAll((nodes) => [
          ...new Set(nodes.map((node) => getComputedStyle(node).color)),
        ]);
      assert.equal(quiet.length, 1, "Inactive views share a quiet outline.");
      if (viewport.width === 390)
        measurements[name] = await page.evaluate(density, densityPolicy);
      if (name === "home") {
        await page
          .getByRole("button", {
            name: "Collection. Open details",
            exact: true,
          })
          .click();
        await page.screenshot({
          path: path.join(out, `live-${viewport.width}x${viewport.height}.png`),
        });
        await measureOverlay(page, `live-${viewport.width}x${viewport.height}`);
        assert.equal(
          await page.getByText("Next run due now", { exact: true }).count(),
          0,
        );
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await page.locator(".activity").scrollIntoViewIfNeeded();
        await page.screenshot({
          path: path.join(
            out,
            `home-pulse-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await page.evaluate(() => window.scrollTo(0, 0));
        const apps = await page.evaluate(() =>
          fetch("/s/apps").then((response) => response.json()),
        );
        assert.equal(
          apps.apps[0].state,
          "live",
          "The real control /health route keeps the operator console live.",
        );
        await page.getByRole("button", { name: "Open menu" }).click();
        // A status line shows only once a check answers; unknown checks show none.
        await page.locator(".app-links .status").first().waitFor();
        assert.equal(await page.getByText("Health unknown").count(), 0);
        await page.screenshot({
          path: path.join(out, `menu-${viewport.width}x${viewport.height}.png`),
        });
        await measureOverlay(page, `menu-${viewport.width}x${viewport.height}`);
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await page.locator(".freshness button").click();
        await measureOverlay(
          page,
          `last-update-${viewport.width}x${viewport.height}`,
        );
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await page.getByRole("button", { name: "Next song" }).click();
        await page
          .locator(".mover-caption h2")
          .filter({ hasText: "Slow return" })
          .waitFor();
        await page.getByRole("button", { name: "See why" }).click();
        await page.locator(".mover-back").waitFor();
        await page.waitForTimeout(350);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({
          path: path.join(
            out,
            `home-flip-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await page.locator(".music-facts button").first().click();
        await page.getByText("Behind the movement", { exact: true }).waitFor();
        await measureOverlay(
          page,
          `movement-fact-${viewport.width}x${viewport.height}`,
        );
        // See proof opens the plain summary of the playlist behind the fact.
        await page
          .getByRole("link", { name: "See proof →", exact: true })
          .click();
        await proofSummary(
          page,
          origin,
          out,
          "proof-mover",
          `${viewport.width}x${viewport.height}`,
          {
            headline: /^1 playlist\.$/,
            back: "/today",
            item: "Night playlist",
          },
          measurements,
        );
      }
      if (name === "song") {
        assert.equal(
          await page
            .getByRole("link", { name: "Rising", exact: true })
            .getAttribute("aria-current"),
          "page",
        );
        assert.equal(await page.locator(".lane-value").count(), 3);
        assert.equal(
          await page.locator(".lane-value").nth(2).innerText(),
          "building history",
          "A family collecting its first week says so.",
        );
        await page.getByRole("button", { name: "Open song identity" }).click();
        await page.screenshot({
          path: path.join(
            out,
            `song-identity-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await measureOverlay(
          page,
          `song-identity-${viewport.width}x${viewport.height}`,
        );
        await page.getByRole("button", { name: "Close", exact: true }).click();
        const lane = await page
          .getByRole("button", { name: "Open Shazam cities mark" })
          .boundingBox();
        assert(lane);
        await page.mouse.move(
          lane.x + lane.width * 0.9,
          lane.y + lane.height * 0.6,
        );
        await page.mouse.down();
        await page.mouse.move(
          lane.x + lane.width * 0.2,
          lane.y + lane.height * 0.6,
          { steps: 12 },
        );
        await page.mouse.up();
        assert.equal(
          await page.locator("dialog[open]").count(),
          0,
          "A scrub does not open a mark sheet.",
        );
        await page.getByRole("slider", { name: "Scrub day" }).fill("12");
        assert.equal(
          await page.locator(".lane-value").nth(1).innerText(),
          "4 cities",
        );
        const bubble = await page.locator(".day-bubble").boundingBox();
        const footer = await page.locator(".freshness").boundingBox();
        assert(
          bubble && footer && bubble.y + bubble.height <= footer.y,
          "The day bubble stays above the footer.",
        );
        await page.screenshot({
          path: path.join(
            out,
            `song-scrub-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await page.getByRole("slider", { name: "Scrub day" }).fill("9");
        assert.equal(
          await page.locator(".lane-value").nth(1).innerText(),
          "not collected",
        );
        await page
          .getByRole("button", { name: "Open Shazam cities mark" })
          .click();
        await page.getByText("not collected", { exact: true }).last().waitFor();
        await page.screenshot({
          path: path.join(
            out,
            `song-gap-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await page.getByRole("button", { name: "Close", exact: true }).click();
        await page.getByRole("slider", { name: "Scrub day" }).fill("12");
        await page
          .getByRole("button", { name: "Open Shazam cities mark" })
          .click();
        await page
          .locator("dialog[open] .value")
          .filter({ hasText: /^4$/ })
          .waitFor();
        await page.screenshot({
          path: path.join(
            out,
            `song-mark-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await measureOverlay(
          page,
          `song-mark-${viewport.width}x${viewport.height}`,
        );
        await page
          .getByRole("link", { name: "See proof →", exact: true })
          .click();
        // The lane's proof names that day's city charts, or says there were none.
        await proofSummary(
          page,
          origin,
          out,
          "song-proof",
          `${viewport.width}x${viewport.height}`,
          { headline: /city charts?\.$/, back: `/s/song/${keys[0]}` },
        );
      }
      if (name === "places") {
        await page
          .getByText("Reached 3 new markets.", { exact: true })
          .waitFor();
        await page.getByText("Reached 3 new markets.", { exact: true }).click();
        await page
          .getByRole("dialog", { name: "Where this came from" })
          .getByRole("link", { name: "See proof →", exact: true })
          .click();
        await proofSummary(
          page,
          origin,
          out,
          "proof-arrival",
          `${viewport.width}x${viewport.height}`,
          { headline: /^1 playlist\.$/, back: "/songs?view=places" },
          measurements,
        );
        assert.equal(
          await page.getByText("Unplaced fixture", { exact: true }).count(),
          0,
          "Places never shows unplaced songs.",
        );
        await page
          .getByText("Needs artist audience size", { exact: false })
          .waitFor();
        const [established, catalog] = await Promise.all(
          ["established artists", "catalog waking up"].map((name) =>
            page.getByRole("heading", { name, exact: true }).boundingBox(),
          ),
        );
        assert(
          established && catalog && established.y > catalog.y,
          "Places shows catalog cards before the established-artist placeholder.",
        );

      }
      if (name === "holdings") {
        assert.equal(
          await page
            .getByText("Cost · not measured yet", { exact: true })
            .count(),
          1,
        );
        assert.equal(
          await page.getByText("per thousand entries", { exact: true }).count(),
          0,
        );
        await page
          .getByRole("button", { name: /^Entries each day this week/ })
          .click();
        await page
          .getByText("Stored totals · not measured yet", { exact: true })
          .waitFor();
        assert.doesNotMatch(
          await page.locator("dialog[open]").innerText(),
          /140,000|Stored on/,
        );
        await page.screenshot({
          path: path.join(
            out,
            `holdings-details-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await measureOverlay(
          page,
          `holdings-details-${viewport.width}x${viewport.height}`,
        );
        await page.getByRole("button", { name: "Close", exact: true }).click();
        assert.equal(
          await page
            .getByRole("button", { name: "Export slide", exact: true })
            .count(),
          0,
        );
        await page.getByRole("tab", { name: "Rights", exact: true }).click();
        await page.waitForTimeout(300);
        await page.screenshot({
          path: path.join(
            out,
            `rights-${viewport.width}x${viewport.height}.png`,
          ),
        });
        if (viewport.width === 390)
          measurements.rights = await page.evaluate(density, densityPolicy);
        await page
          .getByRole("button", { name: /^Collecting now · \d+$/ })
          .click();
        await page.waitForTimeout(300);
        await page.screenshot({
          path: path.join(
            out,
            `rights-details-${viewport.width}x${viewport.height}.png`,
          ),
        });
        await measureOverlay(
          page,
          `rights-details-${viewport.width}x${viewport.height}`,
        );
        await page.getByRole("button", { name: "Close", exact: true }).click();
      }
    }
    await searchWalk(
      page,
      db,
      wh,
      origin,
      out,
      `${viewport.width}x${viewport.height}`,
      overlays,
    );
    await provenanceWalk(
      page,
      origin,
      out,
      `${viewport.width}x${viewport.height}`,
      keys[0],
      overlays,
    );
    await callsWalk(
      page,
      db,
      wh,
      origin,
      out,
      `${viewport.width}x${viewport.height}`,
      keys[0],
    );
    await draftWalk(
      page,
      db,
      wh,
      origin,
      out,
      `${viewport.width}x${viewport.height}`,
    );
    await page.goto(origin + "/ops");
    await navigation(page, origin + "/today");
    await page.screenshot({
      path: path.join(out, `engine-${viewport.width}x${viewport.height}.png`),
    });
    const rejected = await page.request.post(origin + "/actions", {
      maxRedirects: 0,
      headers: {
        origin: "https://foreign.invalid",
        "sec-fetch-site": "cross-site",
        cookie: (await context.cookies())
          .map((c) => `${c.name}=${c.value}`)
          .join("; "),
      },
      data: "action=tenants.create",
    });
    assert.equal(rejected.status(), 403);
    if (viewport.width === 390) {
      await proxyChecks(
        page,
        context,
        db,
        origin,
        `api-key:${person.api_key_id}`,
        requests,
      );
      await page.waitForTimeout(1100);
      await page.goto(origin + "/ops?hostile=1");
      await page.waitForTimeout(150);
      await navigation(page, origin + "/today");
      assert.equal(await page.locator("base").count(), 0);
      await page.mouse.click(90, 26);
      await page.waitForURL(previousImage ? "**/today" : origin + "/");
      const state = await context.storageState();
      await pressAndHold(browser, state, origin, out, overlays);
      for (const clip of ["home-swipe-flip", "song-collapse-scrub"]) {
        const recording = await browser.newContext({
          viewport,
          storageState: state,
          recordVideo: { dir: path.join(out, "video"), size: viewport },
        });
        const scene = await recording.newPage();
        await scene.route("**/art/**", (route) =>
          route.fulfill({
            contentType: "image/svg+xml",
            body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800"><rect width="800" height="800" fill="#a9c885"/><circle cx="350" cy="300" r="190" fill="#23343d"/><text x="50" y="65" fill="#edf4f6">TEST ART</text></svg>',
          }),
        );
        await scene.goto(
          origin + (clip.startsWith("home") ? "/today" : `/s/song/${keys[0]}`),
        );
        await scene.waitForTimeout(1400);
        if (clip.startsWith("home")) {
          // Swipe across the cover, never from a badge or verb: the stack ignores a gesture that
          // starts on a button, and where the badge row sits depends on the platform's font metrics.
          const art = await scene.locator(".stack .art").first().boundingBox();
          assert(art, "The mover stack shows its cover art.");
          const swipeY = art.y + art.height / 2;
          await scene.mouse.move(art.x + art.width * 0.75, swipeY);
          await scene.mouse.down();
          await scene.mouse.move(art.x + art.width * 0.15, swipeY, {
            steps: 16,
          });
          await scene.mouse.up();
          await scene
            .locator(".mover-caption h2")
            .filter({ hasText: "Slow return" })
            .waitFor();
          await scene.waitForTimeout(350);
          assert.equal(
            await scene.evaluate(() => window.scrollY),
            0,
            "A native swipe keeps the page in place.",
          );
          const why = await scene
            .getByRole("button", { name: "See why" })
            .boundingBox();
          assert(why);
          await scene.mouse.click(
            why.x + why.width / 2,
            why.y + why.height / 2,
          );
          await scene.locator(".mover-back").waitFor();
          await scene.waitForTimeout(350);
          assert.equal(
            await scene.evaluate(() => window.scrollY),
            0,
            "A native flip keeps the page in place.",
          );
        } else {
          const slider = await scene
            .getByRole("slider", { name: "Scrub day" })
            .boundingBox();
          assert(slider);
          await scene.mouse.move(
            slider.x + slider.width - 9,
            slider.y + slider.height / 2,
          );
          await scene.mouse.down();
          await scene.mouse.move(
            slider.x + slider.width * 0.3,
            slider.y + slider.height / 2,
            { steps: 24 },
          );
          await scene.mouse.up();
        }
        await scene.waitForTimeout(1800);
        const video = scene.video();
        await recording.close();
        await video?.saveAs(path.join(out, clip + ".webm"));
      }
    }
    if (viewport.width === 1440) {
      await recordHover(browser, await context.storageState(), origin, out);
      await page.goto(origin + "/today");
      runnerState = "busy";
      await page.waitForTimeout(11500);
      const reads = requests.filter((r) =>
        r.path.startsWith("/rpc/mart_"),
      ).length;
      await page.goto(origin + "/today");
      await page
        .getByText("Saved while collecting", { exact: false })
        .waitFor();
      assert.equal(
        requests.filter((r) => r.path.startsWith("/rpc/mart_")).length,
        reads,
        "Busy runners prevent fresh mart reads.",
      );
      const [driftAlert] =
        await db`INSERT INTO control.alert(class,severity,subject_type,subject_id) SELECT 'schema_drift','warning','streamline',id::text FROM control.streamline WHERE source_key='browser_fixture' RETURNING id`;
      for (const state of ["busy", "unknown"]) {
        runnerState = state;
        await page.waitForTimeout(10500);
        const headers = {
          origin,
          "sec-fetch-site": "same-origin",
          cookie: (await context.cookies())
            .map((c) => `${c.name}=${c.value}`)
            .join("; "),
        };
        const count = requests.length;
        const result = await page.request.get(
          origin + "/explorer?cold=" + state,
          { headers },
        );
        assert.equal(result.status(), 503);
        assert((await result.text()).includes("Open /status"));
        const preview = await page.request.post(
          origin + "/rpc/functions/page",
          {
            headers,
            data: {
              json: { source_key: "browser_fixture", preview_table: state },
            },
          },
        );
        assert.equal(preview.status(), 503);
        assert((await preview.text()).includes("Open /status"));
        assert(
          !requests
            .slice(count)
            .some(
              (r) => r.path === "/explorer" || r.path === "/rpc/functions/page",
            ),
        );
        const before = functionReads.length;
        const ops = await page.request.get(
          origin + "/ops?classification=" + state,
          { headers },
        );
        assert.equal(ops.status(), 200);
        assert(
          functionReads.length > before,
          "Ops reads the fixture drift metadata.",
        );
        assert(
          functionReads.slice(before).every((read) => read.metadata),
          "Ops never requests warehouse previews.",
        );
      }
      await db`DELETE FROM control.alert WHERE id=${driftAlert!.id}`;
      runnerState = "busy";
      await page.screenshot({ path: path.join(out, "home-busy-1440x900.png") });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: path.join(out, "home-busy-390x844.png") });
      measurements["home-busy"] = await page.evaluate(density, densityPolicy);
      await page.setViewportSize(viewport);
      runnerState = "idle";
      dataUnavailable = true;
      await page.waitForTimeout(31000);
      await page.goto(origin + "/today");
      await page
        .locator(".freshness button")
        .filter({ hasText: /^Saved / })
        .waitFor();
      // The fixture's row count follows the UTC weekday, so read the expected value from it.
      const todayRows = Number(
        holdings.ingestion.summary.today_rows,
      ).toLocaleString("en-US");
      await page
        .locator(".activity strong")
        .filter({ hasText: new RegExp(`^${todayRows}$`) })
        .waitFor();
      await page.waitForFunction(() =>
        Array.from(
          document.querySelectorAll<HTMLImageElement>(".mover-front img"),
        ).every((img) => img.complete),
      );
      await page.screenshot({
        path: path.join(out, "home-last-good-1440x900.png"),
      });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({
        path: path.join(out, "home-last-good-390x844.png"),
      });
      measurements["home-last-good"] = await page.evaluate(
        density,
        densityPolicy,
      );
      await page.setViewportSize(viewport);
      dataUnavailable = false;
      await page.waitForTimeout(1100);
      await page.bringToFront();
      await page.goto(origin + "/songs?view=places");
      await page
        .getByRole("button", { name: "Collection. Open details", exact: true })
        .waitFor();
      assert.equal(
        await page.evaluate(() => document.visibilityState),
        "visible",
      );
      const sessionCookie = (await context.cookies()).find(
        (cookie) => cookie.name === "__Host-mdp_showcase",
      )!;
      await db`UPDATE control.showcase_session SET revoked_at=now() WHERE id_hash=${createHash("sha256").update(sessionCookie.value).digest("hex")}`;
      await page
        .waitForURL("**/sign-in?reason=ended", { timeout: 65000 })
        .catch(async (error) => {
          console.log(
            "Revocation check",
            page.url(),
            await page.evaluate(() => document.visibilityState),
          );
          await page.screenshot({
            path: path.join(out, "revocation-failure.png"),
          });
          throw error;
        });
      console.log(
        "Busy-cache suppression, last-good rendering and active SSE session revocation pass.",
      );
    }
    await context.close();
  }
  if (!process.env.BROWSER_PROXY_ONLY && !process.env.BROWSER_SSE_ONLY)
    await writeFile(
      path.join(out, "density.json"),
      JSON.stringify(measurements, null, 2) + "\n",
    );
  if (!process.env.BROWSER_PROXY_ONLY && !process.env.BROWSER_SSE_ONLY)
    await writeFile(
      path.join(out, "overlay-density.json"),
      JSON.stringify(overlays, null, 2) + "\n",
    );
  await writeFile(
    path.join(out, "proxy-requests.json"),
    JSON.stringify(requests, null, 2) + "\n",
  );
  for (const [name, value] of Object.entries(measurements)) {
    const d = value;
    if ("clockViolations" in d)
      assert.deepEqual(
        d.clockViolations,
        [],
        `${name}: every clock names its zone`,
      );
    assert(
      !d.jargonWarnings?.length,
      `${name}: ${d.jargonWarnings}. Open docs/copy.md.`,
    );
    assert(d.words <= 40, `${name}: ${d.words} words`);
    assert(d.numbers <= 3, `${name}: ${d.numbers} numbers`);
    assert(!d.banned.length, `${name}: ${d.banned}`);
    assert(
      d.headlines.length === 1 && d.headlines.every((n: number) => n <= 8),
    );
    assert(
      !d.headlinePatterns.length,
      `${name}: performative headline ${d.headlinePatterns}`,
    );
    assert(
      d.cards.every((n: number) => n === 1),
      `${name}: card actions ${d.cards}`,
    );
    assert(!d.tables && !d.monospace);
  }
  for (const [name, d] of Object.entries(overlays)) {
    if ("clockViolations" in d)
      assert.deepEqual(
        d.clockViolations,
        [],
        `${name}: every clock names its zone`,
      );
    assert(
      !d.jargonWarnings?.length,
      `${name}: ${d.jargonWarnings}. Open docs/copy.md.`,
    );
    assert(!d.banned.length, `${name}: ${d.banned}`);
    if (d.kind === "popover") {
      assert(d.words <= 30, `${name}: ${d.words} words in a hover card`);
      assert(d.numbers <= 4, `${name}: ${d.numbers} numbers in a hover card`);
    } else assert(d.words <= 80, `${name}: ${d.words} words in a sheet`);
  }
  console.log(
    process.env.MDP_SHOWCASE_BROWSER_GLYPHS_ONLY
      ? "Source glyph colours, focus, bounds and viewport widths pass. Open glyphs-390x844.json."
      : process.env.BROWSER_RATE_ONLY
        ? "Request limits and retry states pass. Open request-measurements.json."
        : previousImage
          ? "Previous showcase image compatibility passes. Open previous-showcase.log."
          : process.env.MDP_SHOWCASE_BROWSER_READERS_ONLY === "1"
            ? `Viewer reader cards, dates, count links, text limits and viewport fit pass. Open ${path.relative(path.resolve("../../.."), path.join(out, "viewer-readers-390.json"))}.`
            : redesign
              ? `Browser screens, interactions, session revocation and word limits pass. Open ${path.relative(path.resolve("../../.."), path.join(out, dataUnavailable ? "music-unavailable-density-390x844.json" : "redesign-density-390x844.json"))}.`
              : process.env.BROWSER_STACK_ONLY
                ? `Stack and onboarding screens, sheets, failure states and word limits pass. Open ${path.relative(path.resolve("../../.."), path.join(out, "stack-density-390x844.json"))}.`
                : process.env.BROWSER_SEARCH_ONLY
                  ? `Search screens and density pass. Open ${path.relative(path.resolve("../../.."), path.join(out, "overlay-density.json"))}.`
                  : process.env.BROWSER_CALLS_ONLY
                    ? `Picks screens and phone density pass. Open ${path.relative(path.resolve("../../.."), path.join(out, "calls-density-390x844.json"))}.`
                    : process.env.BROWSER_CARDS_ONLY
                      ? `Card covers, badges, matched song and empty calls week pass. Open ${path.relative(path.resolve("../../.."), path.join(out, "density.json"))}.`
                      : `Browser screens, interactions, proxy origin refusal and phone density pass. Open ${path.relative(path.resolve("../../.."), path.join(out, "density.json"))}.`,
  );
} catch (error) {
  for (const context of browser?.contexts() ?? [])
    for (const page of context.pages()) {
      console.log("Stopped at", new URL(page.url()).pathname);
      await page
        .screenshot({ path: path.join(out, "browser-failure.png") })
        .catch(() => {});
    }
  throw error;
} finally {
  await browser?.close();
  child.kill("SIGTERM");
  if (previousImage)
    spawnSync("docker", ["rm", "-f", "-v", imageContainer], {
      stdio: "ignore",
    });
  upstream.closeAllConnections();
  upstream.close();
  if (stackArtifacts) rmSync(stackArtifacts, { recursive: true, force: true });
  await db.end();
  await wh.end();
  log.end();
  const controlDb = "../../../control-api/src/db";
  await (await import(controlDb)).database().end();
}
