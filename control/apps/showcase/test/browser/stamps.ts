// Local proof over dbt-built data. Run prepare.py first, then this script from the showcase package.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { createWriteStream } from "node:fs";
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import postgres from "postgres";
import { z } from "zod";
import { chromium } from "@playwright/test";
import { mint } from "@mdp/showcase-auth";
import { verifyCall } from "../../server/call-token";
import { signedCard } from "../../lib/calls";
import { readerTypes } from "../../../data-api/src/read";

const out = path.resolve("../../../ops/evidence/h-demo-stamps");
const state = z
  .object({
    database: z.string().regex(/^mbt_wh_[a-f0-9]+$/),
    cycle: z.object({ id: z.uuid(), run_id: z.string() }),
  })
  .parse(
    JSON.parse(await readFile("/tmp/h-demo-stamps-proof/state.json", "utf8")),
  );
const origin = "http://127.0.0.1:3194";
const upstream = "http://127.0.0.1:3195";
const url = (role: string, database: string) =>
  `postgresql://${role}:${role}@127.0.0.1:55487/${database}`;
const db = postgres(url("postgres", "control"));
const wh = postgres(url("postgres", state.database));
const viewer = {
  handle: "fixture",
  display_name: "Test viewer",
  email: "fixture@example.invalid",
  admin_key: "stamps-admin",
  api_key_id: "00000000-0000-4000-8000-000000000094",
};
Object.assign(process.env, {
  MDP_SHOWCASE_PEOPLE: JSON.stringify([viewer]),
  MDP_SHOWCASE_LINK_SECRET: "l".repeat(32),
  MDP_SHOWCASE_SESSION_SECRET: "s".repeat(32),
  MDP_SHOWCASE_ORIGIN: origin,
  MDP_CONTROL_RT_URL: url("control_rt", "control"),
  MDP_CONTROL_DATABASE_URL: url("control_rt", "control"),
  MDP_SHOWCASE_WH_URL: url("showcase_wh", state.database),
  MDP_CONTROL_API_URL: upstream,
  MDP_DATA_API_URL: upstream,
  MDP_SHOWCASE_READER_KEY: "stamps-reader",
  MDP_AUTH_MODE: "production",
  MDP_TRUSTED_BROWSER_ORIGINS: origin,
});
await wh.unsafe(
  `GRANT CONNECT ON DATABASE ${state.database} TO showcase_wh,reader_wh`,
);
await db`TRUNCATE control.showcase_call_rule,control.showcase_rule,control.showcase_call,control.showcase_draft CASCADE`;
for (const [key, role, id] of [
  [viewer.admin_key, "admin", viewer.api_key_id],
  ["stamps-reader", "reader", "00000000-0000-4000-8000-000000000095"],
]) {
  await db`INSERT INTO control.api_key(id,key_hash,label,role) VALUES (${id},${createHash("sha256").update(key!).digest("hex")},'stamps-fixture',${role}) ON CONFLICT(id) DO UPDATE SET key_hash=excluded.key_hash,revoked_at=NULL`;
}
await db`INSERT INTO control.cycle(id,cadence,scope,opened_at,closed_at,opened_by_dbt_run_id,status,close_no)
 VALUES (${state.cycle.id},'daily','global','2026-09-26T11:55:00Z','2026-09-26T12:00:00Z',${state.cycle.run_id},'closed',1) ON CONFLICT DO NOTHING`;
const reader = postgres(url("reader_wh", state.database), {
  types: readerTypes,
});
const keys = postgres(url("api_key_reader", "control"));
// An opaque specifier keeps the Hono JSX app out of the React typecheck.
const controlApp = "../../../control-api/src/app";
const { app } = await import(controlApp);
const { createApp } = await import("../../../data-api/src/app");
const data = createApp(reader, keys);
const server = createServer(async (req, res) => {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(Buffer.from(chunk));
  const body = Buffer.concat(chunks);
  const headers = new Headers();
  for (const [key, value] of Object.entries(req.headers)) {
    if (typeof value === "string") headers.set(key, value);
  }
  const request = new Request(upstream + req.url, {
    method: req.method,
    headers,
    ...(body.length ? { body } : {}),
  });
  const response = await (
    req.headers["x-api-key"] === "stamps-reader" ? data : app
  ).fetch(request);
  res.writeHead(response.status, Object.fromEntries(response.headers));
  res.end(Buffer.from(await response.arrayBuffer()));
});
await new Promise<void>((resolve) => server.listen(3195, "127.0.0.1", resolve));
const log = createWriteStream(path.join(out, "proof-server.log"));
const child = spawn(
  process.execPath,
  [
    fileURLToPath(import.meta.resolve("next/dist/bin/next")),
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    "3194",
  ],
  { env: process.env, stdio: ["ignore", "pipe", "pipe"] },
);
child.stdout.pipe(log);
child.stderr.pipe(log);
const browser = await chromium.launch({ headless: true });
try {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(origin + "/healthz")).ok) break;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.setDefaultTimeout(60000);
  await page.route("**/art/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#23343d"/><circle cx="200" cy="200" r="110" fill="#a9c885" opacity=".2"/></svg>',
    }),
  );
  await page.goto(await mint(db, viewer.handle));
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL("**/today");
  await page.goto(origin + "/rising");
  const button = page
    .getByRole("button", { name: "Call it", exact: true })
    .first();
  await writeFile(
    path.join(out, "proof-page.txt"),
    await page.locator("body").innerText(),
  );
  await button.waitFor();
  await button.scrollIntoViewIfNeeded();
  await button
    .locator("xpath=ancestor::article[1]")
    .screenshot({ path: path.join(out, "call-card.png") });
  await button.click();
  await page.locator(".sheet").waitFor();
  await page
    .locator(".sheet")
    .screenshot({ path: path.join(out, "call-sheet.png") });
  const submitted = page.waitForRequest(
    (request) =>
      request.method() === "POST" && request.url().includes("/calls"),
  );
  await page
    .locator(".sheet")
    .getByRole("button", { name: "Call it", exact: true })
    .click();
  const request = await submitted;
  const payload = z
    .object({ signed: signedCard })
    .parse(request.postDataJSON());
  const facts = verifyCall(payload.signed, viewer.handle);
  assert(
    facts &&
      facts.builds.every(
        (build) => build.stamped && build.cycle_id === state.cycle.id,
      ),
  );
  await page.waitForTimeout(800);
  const [saved] =
    await db`SELECT snapshot,facts FROM control.showcase_call WHERE author=${viewer.handle}`;
  assert.equal(saved?.snapshot, payload.signed.body);
  assert.deepEqual(saved?.facts, facts);
  await page.goto(origin + "/draft?board=1");
  await page.locator(".draft-tray button").first().waitFor();
  await page
    .locator(".draft-room")
    .screenshot({ path: path.join(out, "draft-tray.png") });
  const [draft] =
    await db`SELECT candidates FROM control.showcase_draft WHERE week_start='2026-09-25'`;
  const candidates = z
    .array(
      z.object({
        snapshot: z.object({
          facts_day: z.string(),
          builds: z.array(
            z.object({ stamped: z.boolean(), cycle_id: z.string() }),
          ),
        }),
      }),
    )
    .parse(draft?.candidates);
  assert(candidates.length > 0);
  assert(
    candidates.every(
      (candidate) =>
        candidate.snapshot.facts_day === "2026-09-26" &&
        candidate.snapshot.builds.every(
          (build) => build.stamped && build.cycle_id === state.cycle.id,
        ),
    ),
  );
  await writeFile(
    path.join(out, "browser-proof.json"),
    JSON.stringify(
      {
        call_saved: true,
        signed_body_matches: true,
        stamped_inputs: facts.builds.length,
        saturday_candidates: candidates.length,
        read_day: "2026-09-26",
      },
      null,
      2,
    ) + "\n",
  );
} finally {
  await browser.close();
  child.kill("SIGTERM");
  server.closeAllConnections();
  server.close();
  await Promise.all([db.end(), wh.end(), reader.end(), keys.end()]);
}
process.exit(0);
