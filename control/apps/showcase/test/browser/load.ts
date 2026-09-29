// Times scheduled Core runs with and without showcase traffic on the ops/local/up.sh stack.
// Open ops/evidence/showcase-load/README.md for the commands and the latest result.
import assert from "node:assert/strict";
import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { createWriteStream, type WriteStream } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { loadavg } from "node:os";
import path from "node:path";
import postgres from "postgres";
import { z } from "zod";
import { chromium, type BrowserContext, type Page } from "@playwright/test";
import { mint } from "@mdp/showcase-auth";
import { peekView } from "../../lib/trace";
import { platformNight } from "@mdp/contracts";
import { observeViewer } from "./load-viewer";

function env(name: string) {
  const value = process.env[name];
  if (!value)
    throw new Error(`Set ${name}: run source ops/local/env.sh first.`);
  return value;
}

const root = path.resolve("../../..");
const out = path.resolve(
  root,
  process.env.LOAD_EVIDENCE_DIR ?? "ops/evidence/showcase-load",
);
await mkdir(out, { recursive: true });
const controlURL = env("MDP_CONTROL_ADMIN_URL");
assert(
  new URL(controlURL).hostname === "127.0.0.1",
  "Use the disposable loopback stack.",
);

// baseline: no showcase. reads: showcase traffic only. workbench: the same traffic plus Workbench jobs.
const conditions = ["baseline", "reads", "workbench"] as const;
type Condition = (typeof conditions)[number];
const caseSchema = z.enum(["hourly-full", "daily-first", "daily-full"]);
const cases = caseSchema
  .array()
  .parse((process.env.LOAD_CASES ?? "daily-full").split(","));
const repetitions = z.coerce
  .number()
  .int()
  .min(2)
  .parse(process.env.LOAD_REPETITIONS ?? "7");
const limitPercent = 10;

const runSchema = z.object({
  label: z.string(),
  case: caseSchema,
  condition: z.enum(conditions),
  repeat: z.number(),
  position: z.number(),
  host_load_start: z.array(z.number()),
  host_load_end: z.array(z.number()),
  duration_s: z.number(),
  dbt_phases_s: z.array(z.number()),
  workbench_overlap_s: z.number(),
  max_lock_wait_s: z.number(),
  lock_samples: z.number(),
  wait_observations: z.number(),
  max_sample_gap_ms: z.number(),
});
type Run = z.infer<typeof runSchema>;

const db = postgres(controlURL, { max: 2, onnotice: () => {} });
const wh = postgres(env("MDP_WAREHOUSE_ADMIN_URL"), {
  max: 2,
  onnotice: () => {},
});
const origin = "http://127.0.0.1:3108";
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const people = ["amber", "quartz"].map((handle, i) => ({
  handle,
  display_name: "Test viewer",
  email: handle + "@example.invalid",
  admin_key: "local-load-" + handle,
  api_key_id: `00000000-0000-4000-8000-00000000040${i}`,
}));
Object.assign(process.env, {
  MDP_SHOWCASE_PEOPLE: JSON.stringify(people),
  MDP_SHOWCASE_LINK_SECRET: "l".repeat(32),
  MDP_SHOWCASE_SESSION_SECRET: "s".repeat(32),
  MDP_SHOWCASE_ORIGIN: origin,
});
for (const person of people) {
  const hash = createHash("sha256").update(person.admin_key).digest("hex");
  await db`INSERT INTO control.api_key(id, key_hash, label, role)
    VALUES (${person.api_key_id}, ${hash}, 'local-load', 'admin')
    ON CONFLICT (id) DO UPDATE SET revoked_at = NULL`;
}

function roleURL(role: string, database: string) {
  const url = new URL(controlURL);
  url.username = role;
  url.password = role;
  url.pathname = "/" + database;
  return url.toString();
}

const browser = await chromium.launch({ headless: true });
let app: ChildProcess | undefined;
let contexts: BrowserContext[] = [];
let interactions: WriteStream | undefined;
const runsFile = path.join(out, "runs.json");
const results: Run[] =
  process.env.LOAD_RESUME === "1"
    ? runSchema.array().parse(JSON.parse(await readFile(runsFile, "utf8")))
    : [];

async function startApp(label: string) {
  interactions = createWriteStream(
    path.join(out, label + "-interactions.jsonl"),
  );
  await writeFile(path.join(out, label + "-art.jsonl"), "");
  const log = createWriteStream(path.join(out, label + "-app.log"));
  app = spawn(
    process.execPath,
    [
      path.resolve("node_modules/next/dist/bin/next"),
      "start",
      "--hostname",
      "127.0.0.1",
      "--port",
      "3108",
    ],
    {
      cwd: path.join(root, "control/apps/showcase"),
      env: {
        ...process.env,
        MDP_CONTROL_RT_URL: roleURL("control_rt", "control"),
        MDP_SHOWCASE_WH_URL: roleURL("showcase_wh", "warehouse"),
        MDP_SHOWCASE_READER_KEY: env("MDP_DATA_API_KEY"),
        NODE_OPTIONS: `--import=${path.resolve("test/browser/load-artwork.mjs")}`,
        SHOWCASE_ART_LOG: path.join(out, label + "-art.jsonl"),
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  app.stdout?.pipe(log);
  app.stderr?.pipe(log);
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(origin + "/healthz")).ok) return;
    } catch {}
    await pause(100);
  }
  throw new Error(
    "The local showcase did not start. Open its -app.log in the evidence folder.",
  );
}

async function stopApp() {
  for (const context of contexts) await context.close();
  contexts = [];
  if (interactions) {
    const stream = interactions;
    await new Promise<void>((resolve) => stream.end(resolve));
    interactions = undefined;
  }
  const running = app;
  if (!running) return;
  const stopped = new Promise<void>((resolve) =>
    running.once("exit", () => resolve()),
  );
  running.kill("SIGTERM");
  await stopped;
  app = undefined;
}

async function authHeaders(page: Page) {
  const cookies = await page.context().cookies();
  return {
    origin,
    "sec-fetch-site": "same-origin",
    cookie: cookies
      .map((cookie) => `${cookie.name}=${cookie.value}`)
      .join("; "),
  };
}

async function rpc<T>(
  page: Page,
  name: string,
  input: unknown,
  schema: z.ZodType<T>,
) {
  const response = await page.request.post(origin + "/rpc/workbench/" + name, {
    headers: {
      ...(await authHeaders(page)),
      "content-type": "application/json",
    },
    data: { json: input },
  });
  assert.equal(response.status(), 200, `${name}: ${await response.text()}`);
  return z.object({ json: schema }).parse(await response.json()).json;
}

const started = z.object({ runId: z.string() });
const jobRow = z.object({
  kind: z.string(),
  status: z.string(),
  duration_ms: z.number().nullable(),
  created_at: z.date(),
});

async function signIn(person: (typeof people)[number]) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    extraHTTPHeaders: { "fly-client-ip": "load-" + person.handle },
  });
  contexts.push(context);
  const page = await context.newPage();
  await page.route(origin + "/", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: '<a href="/songs?view=places">Continue</a>',
    }),
  );
  await page.goto(await mint(db, person.handle));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(origin + "/");
  await page.unroute(origin + "/");
  await pause(1100);
  await page.goto(origin + "/songs?view=places");
  // The heartbeat reports an idle runner before any run starts.
  await page
    .getByRole("button", { name: "Open last night", exact: true })
    .waitFor();
  await pause(1100);
  return page;
}

// Starts the showcase, signs in two people with three tabs each, and starts their traffic.
async function startShowcase(
  label: string,
  withWorkbench: boolean,
  buildStarted: Promise<void>,
) {
  await startApp(label);
  const pages: Page[] = [];
  const jobs: string[] = [];
  const sessions: string[] = [];
  for (const person of people) {
    const page = await signIn(person);
    if (withWorkbench) {
      const session = await rpc(
        page,
        "createSession",
        {},
        z.object({ sessionId: z.string() }),
      );
      sessions.push(session.sessionId);
    }
    pages.push(
      page,
      await page.context().newPage(),
      await page.context().newPage(),
    );
  }
  const viewers = pages.map((page, index) =>
    observeViewer(page, index, out, label, (event) => {
      interactions?.write(JSON.stringify(event) + "\n");
    }),
  );
  // Warm the selected safe projection while the runner is idle. During collection,
  // the same dated cache serves readers without competing for a warehouse connection.
  for (const page of pages) {
    const result = await page.request.get(
      origin + "/s/peek?relation=marts.mart_playlist_profile",
      { headers: await authHeaders(page) },
    );
    assert.equal(
      result.status(),
      200,
      "The safe peek must load before collection. Open the app log.",
    );
    assert.equal(peekView.parse(await result.json()).state, "ready");
    await pause(1100);
  }
  if (withWorkbench) {
    const [cycle] = await db<{ id: string }[]>`SELECT id FROM control.cycle
      WHERE status = 'closed' AND cadence = 'hourly' ORDER BY opened_at DESC LIMIT 1`;
    assert(
      cycle,
      "Run an hourly Core build first so the preview has a closed cycle.",
    );
    const sql =
      "SELECT platform, playlist_id, followers FROM explore_marts.mart_playlist_profile LIMIT 20";
    // Real query and dbt preview jobs cross the proxy and run on the workbench service.
    for (let i = 0; i < people.length; i++) {
      const page = pages[i * 3];
      assert(page);
      const query = await rpc(
        page,
        "query",
        { sessionId: sessions[i], model: "mart_load_query", sql },
        started,
      );
      jobs.push(query.runId);
      for (let n = 0; n < 100; n++) {
        const state = await rpc(
          page,
          "status",
          { runId: query.runId },
          z.object({ status: z.string() }),
        );
        if (state.status === "succeeded") break;
        assert.notEqual(state.status, "failed", JSON.stringify(state));
        await pause(100);
      }
      const preview = await rpc(
        page,
        "previewModel",
        {
          sessionId: sessions[i],
          model: "mart_load_preview",
          sql,
          cycleId: cycle.id,
        },
        started,
      );
      jobs.push(preview.runId);
    }
  }
  let done = false;
  const peekReads: {
    tab: number;
    results: number;
    started_at: number;
    finished_at: number;
  }[] = [];
  const responses: { path: string; status: number; ms: number }[] = [];
  const nightReads: { tab: number; started_at: number; finished_at: number }[] =
    [];
  // All tabs share one bounded window so the read exercises its real single-flight cache.
  // Include the upcoming build while keeping the same request throughout its measurement.
  const now = Date.now();
  const nightWindow = {
    since: new Date(now - 12 * 3600000).toISOString(),
    until: new Date(now + 3600000).toISOString(),
  };
  async function art(page: Page, index: number) {
    for (let n = 0; n < 2; n++) {
      const key = `00000000-0000-4000-8000-${String(index * 2 + n + 100).padStart(12, "0")}`;
      const start = performance.now();
      let result = await page.request.get(origin + "/art/" + key, {
        headers: await authHeaders(page),
      });
      for (let retry = 0; result.status() === 429 && retry < 6; retry++) {
        responses.push({ path: "/art-rate-limit", status: 429, ms: 0 });
        await pause(1200 + index * 100);
        result = await page.request.get(origin + "/art/" + key, {
          headers: await authHeaders(page),
        });
      }
      responses.push({
        path: "/art",
        status: result.status(),
        ms: performance.now() - start,
      });
    }
  }
  const traffic = pages.map(async (page, index) => {
    page.on("response", (response) => {
      if (new URL(response.url()).pathname === "/s/peek")
        responses.push({ path: "/s/peek", status: response.status(), ms: 0 });
    });
    await buildStarted;
    await pause((index % 3) * 650);
    const room = ["/", "/sources", "/songs"][index % 3] ?? "/today";
    for (let tick = 0; !done; tick++) {
      const before = performance.now();
      try {
        const nightStarted = Date.now();
        const night = await page.request.post(origin + "/rpc/platform/night", {
          headers: {
            ...(await authHeaders(page)),
            "content-type": "application/json",
          },
          data: { json: nightWindow },
          timeout: 10000,
        });
        responses.push({
          path: "/rpc/platform/night",
          status: night.status(),
          ms: Date.now() - nightStarted,
        });
        assert.equal(
          night.status(),
          200,
          "Night readings must succeed. Open the traffic file.",
        );
        z.object({ json: platformNight }).parse(await night.json());
        nightReads.push({
          tab: index,
          started_at: nightStarted,
          finished_at: Date.now(),
        });
        if (tick === 0) {
          const peekStarted = Date.now();
          const result = await viewers[index].open(origin);
          peekReads.push({
            tab: index,
            results: result.rows.length,
            started_at: peekStarted,
            finished_at: Date.now(),
          });
        }
        const response = await page.goto(origin + room);
        responses.push({
          path: room,
          status: response?.status() ?? 0,
          ms: performance.now() - before,
        });
        if (tick % 2 === 0) {
          const result = await page.request.get(origin + "/ops", {
            headers: await authHeaders(page),
          });
          responses.push({ path: "/ops", status: result.status(), ms: 0 });
        }
        if (tick === 0) await art(page, index);
        const check = await page.request.head(origin + "/events", {
          headers: await authHeaders(page),
        });
        responses.push({ path: "/events", status: check.status(), ms: 0 });
      } catch (error) {
        await viewers[index].failed(error);
        responses.push({
          path: "browser-error",
          status: 0,
          ms: performance.now() - before,
        });
        console.log(String(error).slice(0, 100));
      }
      await pause(3000 + index * 200);
    }
  });
  // Stops traffic after the Core run, then records the jobs and how long they overlapped it.
  return async (coreStart: number, coreEnd: number) => {
    done = true;
    await Promise.all(traffic);
    for (const id of jobs) {
      for (let n = 0; n < 150; n++) {
        const [job] =
          await db`SELECT status, error FROM control.workbench_run WHERE id = ${id}`;
        if (job?.status === "succeeded") break;
        if (job?.status === "failed") assert.fail(JSON.stringify(job));
        await pause(200);
      }
    }
    const work = jobRow.array().parse(
      await db`SELECT kind, status, duration_ms, created_at FROM control.workbench_run
        WHERE id = ANY(${jobs}::uuid[])`,
    );
    assert.equal(work.length, jobs.length);
    assert(work.every((row) => row.status === "succeeded"));
    let overlap = 0;
    for (const row of work) {
      const begin = row.created_at.getTime();
      const end = begin + (row.duration_ms ?? 0);
      overlap +=
        Math.max(0, Math.min(end, coreEnd) - Math.max(begin, coreStart)) / 1000;
    }
    const evidence = {
      identities: people.length,
      tabs: pages.length,
      jobs: work,
      responses,
      build: { started_at: coreStart, ended_at: coreEnd },
      night_window: nightWindow,
      night_reads: nightReads,
      peek_reads: peekReads,
    };
    await writeFile(
      path.join(out, label + "-traffic.json"),
      JSON.stringify(evidence, null, 2) + "\n",
    );
    assert.equal(
      new Set(peekReads.map((read) => read.tab)).size,
      pages.length,
      "Every tab opens a safe peek. Open the traffic file.",
    );
    const nightTabs = new Set(
      nightReads
        .filter(
          (read) => read.started_at >= coreStart && read.finished_at <= coreEnd,
        )
        .map((read) => read.tab),
    );
    assert.equal(
      nightTabs.size,
      pages.length,
      "Every tab must read platform.night during the build. Open the traffic file.",
    );
    const sessionChecks = responses.filter(
      (r) => r.path === "/events" && [200, 204].includes(r.status),
    );
    assert(
      sessionChecks.length >= 6,
      "Session checks must succeed. Open the traffic file.",
    );
    const covers = responses.filter(
      (r) => r.path === "/art" && [200, 404].includes(r.status),
    );
    assert.equal(
      covers.length,
      12,
      "Every cover request must finish. Open the traffic file.",
    );
    await stopApp();
    return overlap;
  };
}

async function coreRun(
  label: string,
  entry: z.infer<typeof caseSchema>,
  condition: Condition,
) {
  const [cadence, mode] = entry.split("-");
  assert(cadence);
  if (mode === "first") {
    await wh.unsafe(`DROP TABLE IF EXISTS marts.mart_early_signals_current, marts.mart_readiness,
      marts.mart_top_movers_current, marts.mart_top_movers, marts.mart_song_day, marts.mart_song_aliases,
      intermediate.int_song_alias_history, intermediate.int_song_key__daily,
      intermediate.int_song_followers__daily CASCADE`);
  }
  let markBuildStarted: () => void = () => {};
  const buildStarted = new Promise<void>((resolve) => {
    markBuildStarted = resolve;
  });
  const finish =
    condition === "baseline"
      ? undefined
      : await startShowcase(label, condition === "workbench", buildStarted);
  const hostLoad = loadavg();
  const log = createWriteStream(path.join(out, label + ".log"));
  const coreStart = Date.now();
  const start = performance.now();
  let running = true;
  let maxWait = 0;
  let samples = 0;
  let maxGap = 0;
  let last = start;
  let waits = 0;
  // A 50 ms sampler reads how long dbt_transform has waited for any lock.
  const monitor = (async () => {
    while (running) {
      const now = performance.now();
      maxGap = Math.max(maxGap, now - last);
      last = now;
      const rows = await wh<{ seconds: number }[]>`SELECT
        extract(epoch FROM clock_timestamp() - l.waitstart)::float8 AS seconds
        FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
        WHERE NOT l.granted AND a.usename = 'dbt_transform' AND l.waitstart IS NOT NULL`;
      samples++;
      for (const row of rows) {
        maxWait = Math.max(maxWait, row.seconds);
        waits++;
      }
      await pause(50);
    }
  })();
  let output = "";
  const runner = spawn(
    "bash",
    [
      "ops/run.sh",
      cadence,
      "--target",
      "pg_local",
      "--reason-category",
      "scheduled",
    ],
    {
      cwd: root,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  markBuildStarted();
  runner.stdout.on("data", (chunk: Buffer) => (output += chunk.toString()));
  runner.stdout.pipe(log);
  runner.stderr.pipe(log);
  const code = await new Promise<number | null>((resolve) =>
    runner.once("exit", resolve),
  );
  const duration = (performance.now() - start) / 1000;
  const coreEnd = Date.now();
  running = false;
  await monitor;
  log.end();
  const overlap = finish ? await finish(coreStart, coreEnd) : 0;
  assert.equal(
    code,
    0,
    `${label} must finish its real Core build. Open ${label}.log.`,
  );
  const phases = [
    ...output.matchAll(/Finished running .*\((\d+(?:\.\d+)?)s\)\./g),
  ].map((m) => Number(m[1]));
  return {
    host_load_start: hostLoad,
    host_load_end: loadavg(),
    duration_s: duration,
    dbt_phases_s: phases,
    workbench_overlap_s: overlap,
    max_lock_wait_s: maxWait,
    lock_samples: samples,
    wait_observations: waits,
    max_sample_gap_ms: maxGap,
  };
}

const sorted = (values: number[]) => [...values].sort((a, b) => a - b);
const mean = (values: number[]) =>
  values.reduce((sum, v) => sum + v, 0) / values.length;
function median(values: number[]) {
  const s = sorted(values);
  const mid = Math.floor(s.length / 2);
  return s.length % 2
    ? (s[mid] ?? NaN)
    : ((s[mid - 1] ?? NaN) + (s[mid] ?? NaN)) / 2;
}
// Drops the fastest and slowest 20% (one of seven runs on each side).
function trimmedMean(values: number[]) {
  const cut = Math.floor(values.length * 0.2);
  return mean(sorted(values).slice(cut, values.length - cut));
}
// Percentile bootstrap over repetitions, seeded so a rerun of the analysis prints the same interval.
function bootstrap(
  values: number[],
  statistic: (sample: number[]) => number,
  rounds = 10000,
) {
  let seed = 20260925;
  const random = () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const stats: number[] = [];
  for (let round = 0; round < rounds; round++) {
    const sample = values.map(
      () => values[Math.floor(random() * values.length)] ?? NaN,
    );
    stats.push(statistic(sample));
  }
  const s = sorted(stats);
  return [
    s[Math.floor(rounds * 0.025)] ?? NaN,
    s[Math.ceil(rounds * 0.975) - 1] ?? NaN,
  ];
}

function summarize(entry: z.infer<typeof caseSchema>) {
  const rows = results.filter((r) => r.case === entry);
  const durations = (condition: Condition) =>
    rows.filter((r) => r.condition === condition).map((r) => r.duration_s);
  const byCondition = Object.fromEntries(
    conditions.map((condition) => {
      const values = durations(condition);
      return [
        condition,
        {
          runs: values.length,
          median_s: median(values),
          trimmed_mean_s: trimmedMean(values),
          mean_s: mean(values),
          min_s: Math.min(...values),
          max_s: Math.max(...values),
        },
      ];
    }),
  );
  const paired = (condition: Condition, against: Condition) => {
    const deltas: number[] = [];
    for (let repeat = 1; repeat <= repetitions; repeat++) {
      const a = rows.find(
        (r) => r.repeat === repeat && r.condition === condition,
      );
      const b = rows.find(
        (r) => r.repeat === repeat && r.condition === against,
      );
      if (a && b) deltas.push((a.duration_s / b.duration_s - 1) * 100);
    }
    const [low, high] = bootstrap(deltas, median);
    return {
      condition,
      against,
      deltas_percent: deltas,
      median_percent: median(deltas),
      trimmed_mean_percent: trimmedMean(deltas),
      ci95_median_percent: [low, high],
      ...(against === "baseline"
        ? { within_limit: median(deltas) <= limitPercent }
        : {}),
    };
  };
  return {
    case: entry,
    conditions: byCondition,
    paired: [
      paired("reads", "baseline"),
      paired("workbench", "baseline"),
      paired("workbench", "reads"),
    ],
    max_lock_wait_s: Math.max(...rows.map((r) => r.max_lock_wait_s)),
  };
}

try {
  for (const entry of cases) {
    for (let repeat = 1; repeat <= repetitions; repeat++) {
      // Rotate the order each repetition so every condition runs first, second and third.
      const shift = (repeat - 1) % conditions.length;
      const order = [...conditions.slice(shift), ...conditions.slice(0, shift)];
      for (const [position, condition] of order.entries()) {
        const label = `${entry}-${repeat}-${condition}`;
        if (results.some((row) => row.label === label)) continue;
        console.log(
          `Run ${label}. Open its log in ${path.relative(root, out)}.`,
        );
        const measured = await coreRun(label, entry, condition);
        results.push({
          label,
          case: entry,
          condition,
          repeat,
          position: position + 1,
          ...measured,
        });
        await writeFile(runsFile, JSON.stringify(results, null, 2) + "\n");
        assert(
          measured.max_lock_wait_s <= 5,
          `${label} waited over 5 s for a lock. Open ${label}.log.`,
        );
      }
    }
  }
  const comparisons = cases.map(summarize);
  const revision = execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
  }).trim();
  const summary = {
    revision,
    repetitions,
    limit_percent: limitPercent,
    statistic:
      "Median of paired per-repetition changes; 95% percentile bootstrap interval, 10000 resamples.",
    comparisons,
    fixture_transport:
      "Recorded vendor responses; artwork transport waits 400 ms per fetch; real Core, control, data, warehouse and workbench services.",
    next_step: "Open runs.json and the per-run traffic evidence.",
  };
  await writeFile(
    path.join(out, "summary.json"),
    JSON.stringify(summary, null, 2) + "\n",
  );
  for (const comparison of comparisons) {
    for (const row of comparison.paired) {
      const [low, high] = row.ci95_median_percent;
      console.log(
        `${comparison.case} ${row.condition} vs ${row.against}: median ${row.median_percent.toFixed(1)}%, ` +
          `95% interval ${low?.toFixed(1)}% to ${high?.toFixed(1)}%`,
      );
    }
  }
  const failed = comparisons.flatMap((c) =>
    c.paired.filter((row) => row.within_limit === false),
  );
  assert.equal(
    failed.length,
    0,
    "A condition exceeds the 10% limit. Open summary.json and runs.json.",
  );
  console.log(
    `Load thresholds pass. Open ${path.relative(root, out)}/summary.json.`,
  );
} finally {
  await stopApp();
  await browser.close();
  await db.end();
  await wh.end();
}
