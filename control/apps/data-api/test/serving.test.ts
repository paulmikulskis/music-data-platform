import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import postgres from "postgres";
import { serve, type ServerType } from "@hono/node-server";
import { createRouterClient } from "@orpc/server";
import { createDataClient, type mart_playlist_events } from "@mdp/data-sdk";
import { router as controlRouter } from "../../control-api/src/router.js";
import { database } from "../../control-api/src/db.js";
import { createApp } from "../src/app.js";
import { readerTypes } from "../src/read.js";

// acceptance against ops/evidence/serving/accept_pg.py: run its setup, seed,
// build-global 1, and build-tenant acme/bravo-co 1 first, then this suite with MDP_SERVING_ACCEPT=1.
// bravo-co's hyphenated slug puts its build and reads in quoted tenant_bravo-co_* schemas.
const enabled = process.env.MDP_SERVING_ACCEPT === "1";
const run = promisify(execFile);
const repo = resolve(fileURLToPath(import.meta.url), "../../../../..");
// Asynchronous, so the in-process data API keeps serving while dbt rebuilds.
async function harness(...args: string[]) {
  await run("uv", ["run", "--project", resolve(repo, "functions"), "python", resolve(repo, "ops/evidence/serving/accept_pg.py"), ...args], {
    cwd: repo,
    maxBuffer: 16 * 1024 * 1024,
  }).catch((error: unknown) => {
    throw new Error(`accept_pg ${args.join(" ")} failed: ${error instanceof Error ? error.message.slice(-3000) : "unknown"}`);
  });
}
function required(name: string) {
  const value = process.env[name];
  if (!value) throw new Error(`Set ${name}`);
  return value;
}
type PlaylistEvent = mart_playlist_events;
const grain = (r: PlaylistEvent) => [r.platform, r.playlist_id, r.variant, r.occurrence_key, r.event_type, r.observed_at];
// The data API orders the text grain columns in byte order.
function inGrainOrder(a: PlaylistEvent, b: PlaylistEvent) {
  const [x, y] = [grain(a), grain(b)];
  for (let i = 0; i < x.length; i++) {
    const [p, q] = [x[i] ?? "", y[i] ?? ""];
    if (p !== q) return Buffer.compare(Buffer.from(p), Buffer.from(q));
  }
  return 0;
}
describe.skipIf(!enabled)("serving to API consumers", () => {
  let server: ServerType | undefined;
  let url = "";
  let warehouse: postgres.Sql;
  let keys: postgres.Sql;
  const control = createRouterClient(controlRouter, {
    context: () => ({ identity: { actor: "serving-acceptance", tenant_id: null, tenant_slug: null, admin: true }, db: database() }),
  });
  const issued: Record<string, string> = {};
  const client = (key: string) => createDataClient(url, { "x-api-key": key });
  async function traverse(key: string, limit: number) {
    const rows: PlaylistEvent[] = [];
    let cursor: string | undefined;
    do {
      const page = await client(key).mart_playlist_events({ limit, cursor });
      rows.push(...page.rows);
      cursor = page.next_cursor ?? undefined;
    } while (cursor);
    return rows;
  }
  beforeAll(async () => {
    warehouse = postgres(required("MDP_READER_URL"), { max: 2, types: readerTypes });
    keys = postgres(required("MDP_API_KEY_READER_URL"), { max: 1 });
    await new Promise<void>((done) => {
      server = serve({ fetch: createApp(warehouse, keys).fetch, port: 0, hostname: "127.0.0.1" }, (info) => {
        url = `http://127.0.0.1:${info.port}`;
        done();
      });
    });
    for (const slug of ["acme", "bravo-co"]) {
      const created = await control.apiKeys.create({ tenant_slug: slug, label: `${slug} console` });
      issued[slug] = created.api_key;
    }
  });
  afterAll(async () => {
    server?.close();
    await warehouse.end();
    await keys.end();
    await database().end();
  });

  it("issues keys shown once, stores only their hash, and lists metadata", async () => {
    const key = issued.acme ?? "";
    expect(key).toMatch(/^mdp_[A-Za-z0-9_-]{43}$/);
    const stored = await database().unsafe("SELECT key_hash, role FROM control.api_key WHERE key_hash=$1", [
      createHash("sha256").update(key).digest("hex"),
    ]);
    expect(stored).toEqual([{ key_hash: createHash("sha256").update(key).digest("hex"), role: "reader" }]);
    const listed = await control.apiKeys.list({ tenant_slug: "acme", include_revoked: false });
    expect(listed.some((k) => k.tenant_slug === "acme" && k.revoked_at === null)).toBe(true);
    expect(JSON.stringify(listed)).not.toContain(key);
    expect(JSON.stringify(listed)).not.toContain("key_hash");
    const audits = await database().unsafe("SELECT after::text AS after FROM control.audit_log WHERE action='apiKeys.create'");
    expect(audits.length).toBeGreaterThanOrEqual(2);
    for (const audit of audits) expect(String(audit.after)).not.toContain(key.slice(4));
  });

  it("refuses a revoked key on its next request", async () => {
    const temporary = await control.apiKeys.create({ tenant_slug: "acme", label: "revocation proof" });
    await expect(client(temporary.api_key).mart_playlist_events({ limit: 1 })).resolves.toBeDefined();
    const revoked = await control.apiKeys.revoke({ id: temporary.id });
    expect(revoked.revoked_at).not.toBeNull();
    await expect(client(temporary.api_key).mart_playlist_events({ limit: 1 })).rejects.toMatchObject({
      code: "DATA",
      status: 401,
      data: { error_class: "unauthorized" },
    });
    await expect(client("mdp_not-a-key").mart_playlist_events({ limit: 1 })).rejects.toMatchObject({
      status: 401,
      data: { error_class: "unauthorized" },
    });
  });

  it("refuses the promoters' key, which serves target promotion only", async () => {
    const secret = "mdp_promoter-serving-proof";
    const hash = createHash("sha256").update(secret).digest("hex");
    await database().unsafe("INSERT INTO control.api_key(key_hash,label,role) VALUES ($1,'promoter proof','promoter')", [hash]);
    try {
      await expect(client(secret).mart_playlist_events({ limit: 1 })).rejects.toMatchObject({
        status: 403,
        data: { error_class: "forbidden" },
      });
    } finally {
      await database().unsafe("DELETE FROM control.api_key WHERE key_hash=$1", [hash]);
    }
  });

  it("serves a playlist's description and owner id only when the platform's own account owns it", async () => {
    const page = await client(issued.acme ?? "").mart_playlist_profile({ limit: 100 });
    const owners = new Map(page.rows.map((r) => [r.playlist_id, [r.owner_class, r.owner_id, r.description]]));
    expect(owners.get("subject_pl")).toEqual(["editorial", "1526756058", "subject_pl description"]);
    expect(owners.get("user_pl")).toEqual(["user", null, null]);
    expect(owners.get("unknown_pl")).toEqual(["unknown", null, null]);
  });

  it("answers contract_pending for a mart built before its contract, and serves it after the rebuild", async () => {
    // The previous release built mart_playlist_events without the two annotation columns.
    const admin = postgres(required("MDP_WAREHOUSE_ADMIN_URL"), { max: 1 });
    await admin.unsafe("DROP VIEW IF EXISTS explore_marts.mart_playlist_events");
    await admin.unsafe("ALTER TABLE marts.mart_playlist_events DROP COLUMN resale_permitted, DROP COLUMN source_keys");
    await admin.end();
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      await expect(client(issued.acme ?? "").mart_playlist_events({ limit: 1 })).rejects.toMatchObject({
        code: "DATA", status: 503, data: { error_class: "contract_pending" },
      });
      const lines = logged.mock.calls.map((call) => String(call[0]));
      expect(lines.some((line) => line.includes('"event":"contract_pending"') && line.includes('"mart":"mart_playlist_events"')
        && line.includes('"missing":["resale_permitted","source_keys"]'))).toBe(true);
    } finally {
      logged.mockRestore();
    }
    // The deploy's rebuild runs the same build on the new models.
    await harness("build-global", "1");
    const page = await client(issued.acme ?? "").mart_playlist_events({ limit: 1 });
    expect(page.rows).toHaveLength(1);
  }, 120_000);

  it("serves ineligible rows with all three annotations through the SDK", async () => {
    const page = await client(issued.acme ?? "").mart_playlist_events({ limit: 100 });
    expect(page.rows.length).toBeGreaterThan(0);
    const row: { learning_eligible: boolean; resale_permitted: boolean; source_keys: string } | undefined = page.rows[0];
    expect(row).toMatchObject({ learning_eligible: false, resale_permitted: false });
    for (const event of page.rows)
      expect(JSON.parse(event.source_keys)).toEqual(expect.arrayContaining(["sp_playlist", "am_playlist"]));
    const events = await traverse(issued.acme ?? "", 100);
    expect(events.every((p) => !p.learning_eligible && !p.resale_permitted && p.source_keys.startsWith("["))).toBe(true);
  });

  it("filters time ranges half-open and pages in declared grain order", async () => {
    const day2 = await client(issued.acme ?? "").mart_playlist_events({
      limit: 100,
      range: { observed_at: { from: "2026-09-02T00:00:00.000Z", to: "2026-09-03T00:00:00.000Z" } },
    });
    expect(day2.rows.length).toBeGreaterThan(0);
    expect(day2.rows.every((r) => r.observed_at.startsWith("2026-09-02"))).toBe(true);
    const paged = await traverse(issued.acme ?? "", 1);
    expect(paged).toEqual([...paged].sort(inGrainOrder));
    expect(paged).toHaveLength(5);
    await expect(
      client(issued.acme ?? "").mart_playlist_events({ limit: 1, cursor: Buffer.from("{}").toString("base64url") }),
    ).rejects.toMatchObject({ status: 400, data: { error_class: "invalid_cursor" } });
  });

  it("answers cursor_stale after a rebuild, then a fresh traversal returns every row once", async () => {
    const first = await client(issued.acme ?? "").mart_playlist_events({ limit: 1 });
    expect(first.next_cursor).not.toBeNull();
    // A changed registry and a new marts._build row from a real dbt rebuild of the same cycle.
    await harness("flip-rights");
    await harness("build-global", "1");
    await expect(
      client(issued.acme ?? "").mart_playlist_events({ limit: 1, cursor: first.next_cursor ?? "" }),
    ).rejects.toMatchObject({ code: "DATA", status: 409, data: { error_class: "cursor_stale" } });
    const fresh = await traverse(issued.acme ?? "", 1);
    expect(fresh).toHaveLength(5);
    expect(new Set(fresh.map((r) => JSON.stringify(grain(r)))).size).toBe(5);
    expect(fresh).toEqual([...fresh].sort(inGrainOrder));
    // Allowing one writer cannot override the other denied upstream writers; tenant learning stays false.
    for (const r of fresh) expect([r.resale_permitted, r.learning_eligible]).toEqual([false, false]);
  }, 120_000);
});
