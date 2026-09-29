import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { createHash, randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { serve, type ServerType } from "@hono/node-server";
import postgres from "postgres";
import { z } from "zod";
import { createRouterClient } from "@orpc/server";
import { createDataClient } from "@mdp/data-sdk";
import { createApp as dataApp } from "../../data-api/src/app.js";
import { readerTypes } from "../../data-api/src/read.js";
import { keys } from "../../../packages/mdp-cli/src/index.js";
import { app } from "../src/app.js";
import { router } from "../src/router.js";
import { database } from "../src/db.js";

// The showcase's global reader key: minted by an admin, read-only on global marts through the
// data API, refused by control-api and by every tenant mart. control-ci supplies a disposable database.
const url = process.env.MDP_TENANTS_TEST_URL ?? "";
const columns = `rank bigint, day date, song_key text, title_text text, artist_text text,
  momentum_score double precision, score_parts text, coverage text, reason_rule text, evidence text,
  ranking_build text, learning_eligible boolean, resale_permitted boolean, source_keys text,
  window_days integer, chart_spread_gain bigint, list_reach_tier integer, market_count bigint,
  last_entered_at timestamptz, movement_list text, age_class text, age_basis text, artist_stage text,
  artist_stage_basis text, cluster_key text, member_song_keys text`;
const role = (base: string, name: string, database: string) => {
  const target = new URL(base);
  target.username = name;
  target.password = name;
  target.pathname = `/${database}`;
  return target.toString();
};
const listening = (fetch: (request: Request) => Response | Promise<Response>) =>
  new Promise<{ server: ServerType; base: string }>((resolve) => {
    const server = serve({ fetch, hostname: "127.0.0.1", port: 0 }, (info) =>
      resolve({ server, base: `http://127.0.0.1:${info.port}` }),
    );
  });
const close = (server: ServerType | undefined) =>
  new Promise<void>((resolve, reject) => {
    if (!server) return resolve();
    server.close((error) => (error ? reject(error) : resolve()));
  });

describe.skipIf(!url)("global reader keys", () => {
  const label = `global-reader-test-${randomUUID().slice(0, 8)}`;
  const adminIdentity = { actor: label, admin: true, tenant_id: null, tenant_slug: null };
  const admin = () => createRouterClient(router, { context: { identity: adminIdentity, db: database() } });
  const adminKey = `mdp_${randomUUID()}`;
  let owner: postgres.Sql;
  let warehouse: postgres.Sql;
  let reader: postgres.Sql;
  let keyReader: postgres.Sql;
  let control: { server: ServerType | undefined; base: string } = { server: undefined, base: "" };
  let data: { server: ServerType | undefined; base: string } = { server: undefined, base: "" };
  let readerKey = "";

  beforeAll(async () => {
    vi.stubEnv("MDP_CONTROL_RT_URL", role(url, "control_rt", "control"));
    vi.stubEnv("MDP_AUTH_MODE", "production");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    owner = postgres(url, { onnotice: () => {} });
    await owner`INSERT INTO control.api_key(key_hash,label,role) VALUES (${createHash("sha256").update(adminKey).digest("hex")},${label},'admin')`;
    const warehouseOwner = new URL(url);
    warehouseOwner.pathname = "/warehouse";
    warehouse = postgres(warehouseOwner.toString(), { onnotice: () => {} });
    await warehouse.unsafe(`CREATE TABLE marts.mart_top_movers_current (${columns})`);
    await warehouse`INSERT INTO marts.mart_top_movers_current (rank, day, song_key, title_text, learning_eligible,
      resale_permitted, source_keys, movement_list, cluster_key, member_song_keys)
      VALUES (1, '2026-09-25', 'song-fixture', 'Fixture song', false, false, '["fixture"]', 'movers', 'song-fixture', '["song-fixture"]')`;
    await warehouse`GRANT USAGE ON SCHEMA marts TO reader_wh`;
    await warehouse`GRANT SELECT ON marts.mart_top_movers_current TO reader_wh`;
    reader = postgres(role(url, "reader_wh", "warehouse"), { max: 2, types: readerTypes });
    keyReader = postgres(role(url, "api_key_reader", "control"), { max: 1 });
    control = await listening(app.fetch);
    data = await listening(dataApp(reader, keyReader).fetch);
  });
  afterAll(async () => {
    await close(control.server);
    await close(data.server);
    await reader?.end();
    await keyReader?.end();
    if (owner) {
      await owner`DELETE FROM control.audit_log WHERE actor=${label}
        OR actor IN (SELECT 'api-key:' || id FROM control.api_key WHERE label LIKE ${label + "%"})`;
      await owner`DELETE FROM control.api_key WHERE label LIKE ${label + "%"}`;
      await owner.end();
    }
    if (warehouse) {
      // CASCADE drops the explore_marts view the warehouse event trigger adds for every mart.
      await warehouse`DROP TABLE IF EXISTS marts.mart_top_movers_current CASCADE`;
      await warehouse.end();
    }
    await database().end();
    vi.unstubAllEnvs();
  });

  it("mints a tenantless reader through the typed client, audited, with the key shown once", async () => {
    const created = await admin().apiKeys.create({ role: "reader", global: true, label: `${label}-typed` });
    expect(created).toMatchObject({ role: "reader", tenant_id: null, tenant_slug: null, warehouse_role: null, revoked_at: null });
    expect(created.api_key).toMatch(/^mdp_[A-Za-z0-9_-]{43}$/);
    readerKey = created.api_key;
    const hash = createHash("sha256").update(readerKey).digest("hex");
    expect(await owner`SELECT role, tenant_id FROM control.api_key WHERE key_hash=${hash}`).toEqual([{ role: "reader", tenant_id: null }]);
    const listed = await admin().apiKeys.list({ include_revoked: false });
    expect(listed.find((row) => row.id === created.id)).toMatchObject({ role: "reader", tenant_slug: null });
    expect(JSON.stringify(listed)).not.toContain(readerKey);
    const audits = await owner`SELECT after->>'state' AS state, after->'input'->>'global' AS global, after::text AS after
      FROM control.audit_log WHERE actor=${label} AND action='apiKeys.create'`;
    expect(audits.map((row) => [row.state, row.global])).toEqual([["succeeded", "true"]]);
    expect(String(audits[0]?.after)).not.toContain(readerKey.slice(4));
  });

  it("refuses a reader key with both, or neither, of --tenant and --global, and a staff key with --global", async () => {
    const refused = (message: string) => ({ status: 400, data: { issues: [expect.objectContaining({ message: expect.stringContaining(message) })] } });
    await expect(admin().apiKeys.create({ role: "reader", global: true, tenant_slug: "acme", label })).rejects.toMatchObject(refused("Use --tenant or --global, not both."));
    await expect(admin().apiKeys.create({ role: "reader", label })).rejects.toMatchObject(refused("A reader key needs --tenant <slug> or --global."));
    await expect(admin().apiKeys.create({ role: "staff", global: true, label })).rejects.toMatchObject(refused("Staff keys take no --tenant or --global."));
    const notAdmin = createRouterClient(router, { context: { identity: { ...adminIdentity, admin: false }, db: database() } });
    await expect(notAdmin.apiKeys.create({ role: "reader", global: true, label })).rejects.toMatchObject({ status: 403 });
  });

  it("reads mart_top_movers_current through the data API", async () => {
    const client = createDataClient(data.base, { "x-api-key": readerKey });
    const page = await client.mart_top_movers_current({ limit: 5 });
    expect(page.rows.map((row) => [row.rank, row.song_key, row.title_text])).toEqual([["1", "song-fixture", "Fixture song"]]);
    expect(page.build).toMatchObject({ relation: "marts.mart_top_movers_current", scope: "global", tenant_slug: null });
    const http = await fetch(`${data.base}/api/marts/mart_top_movers_current?limit=1`, { headers: { "x-api-key": readerKey } });
    expect(http.status).toBe(200);
  });

  it("is refused every control-api request over HTTP", async () => {
    const body = JSON.stringify({ role: "reader", global: true, label });
    const requests: [string, RequestInit][] = [
      ["/api/api-keys/create", { method: "POST", body, headers: { "content-type": "application/json" } }],
      ["/api/tenants/create", { method: "POST", body: JSON.stringify({ slug: `${label}-t`, name: "Refused" }), headers: { "content-type": "application/json" } }],
      ["/api/tenants", { method: "GET" }],
    ];
    for (const [path, init] of requests) {
      const response = await fetch(`${control.base}${path}`, { ...init, headers: { ...init.headers, "x-api-key": readerKey } });
      expect(response.status).toBe(403);
      const refusal = z.object({ error_class: z.string(), next_step: z.string() }).parse(await response.json());
      expect(refusal.error_class).toBe("forbidden");
      expect(refusal.next_step.length).toBeGreaterThan(0);
    }
    expect((await owner`SELECT count(*)::int AS n FROM control.tenant WHERE slug=${`${label}-t`}`)[0]?.n).toBe(0);
  });

  it("mints over HTTP with the CLI and refuses conflicting options before any request", async () => {
    const mdp = (key: string, ...args: string[]) =>
      promisify(execFile)("pnpm", ["--silent", "mdp", ...args], {
        env: { PATH: process.env.PATH, HOME: process.env.HOME, MDP_CONTROL_API_URL: control.base, MDP_API_KEY: key },
        timeout: 20000,
      });
    const cli = (...args: string[]) => mdp(adminKey, "keys", "create", ...args);
    const minted = await cli("--role", "reader", "--global", "--label", `${label}-cli`);
    expect(JSON.parse(minted.stdout)).toMatchObject({ role: "reader", tenant_id: null, label: `${label}-cli` });
    expect(minted.stderr).toContain("MDP_SHOWCASE_READER_KEY");
    const conflict = cli("--role", "reader", "--global", "--tenant", "acme");
    await expect(conflict).rejects.toMatchObject({ code: 1, stderr: expect.stringContaining("Use --tenant or --global, not both.") });
    await expect(conflict).rejects.toMatchObject({ stderr: expect.not.stringContaining("MDP_CONTROL_API_URL") });
    // The CLI refuses locally with the contract's own message: the admin client below is never
    // called, so no audit row appears.
    const client = admin();
    const audited = async () => (await owner`SELECT count(*)::int AS n FROM control.audit_log WHERE actor=${label}`)[0]?.n;
    const before = await audited();
    const refusals: [string[], string][] = [
      [["--role", "reader"], "A reader key needs --tenant <slug> or --global."],
      [["--global", "--role", "staff", "--label", label], "Staff keys take no --tenant or --global."],
      [["--global", "--warehouse-role", "analyst_fixture"], "Only staff keys take --warehouse-role."],
      [["--global", "--role", "admin"], "Check --role. Use --role reader or --role staff."],
      [["--global", "--expires", "tomorrow"], "Check --expires. Use an ISO time with a zone"],
      [["--tenant", "ACME"], "Check --tenant. Run pnpm --dir control mdp tenants list"],
    ];
    for (const [args, message] of refusals) await expect(keys(client, "create", args)).rejects.toThrow(message);
    expect(await audited()).toBe(before);
    // A reader key on the operator CLI gets the refusal's own next step, not the connection hint.
    await expect(mdp(readerKey, "status")).rejects.toMatchObject({
      code: 1,
      stderr: expect.stringContaining("operator actions need admin"),
    });
    await expect(keys(client, "rotate", [])).rejects.toThrow("keys create --role reader --global");
  }, 60000);
});
