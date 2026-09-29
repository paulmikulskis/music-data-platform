import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { serve, type ServerType } from "@hono/node-server";
import postgres from "postgres";
import { createRouterClient } from "@orpc/server";
import { app } from "../src/app.js";
import { router } from "../src/router.js";
import { database } from "../src/db.js";

// control-ci supplies a disposable database; the router and CLI use control_rt throughout.
const testUrl = process.env.MDP_TENANTS_TEST_URL;
describe.skipIf(!testUrl)("tenant onboarding on the local control database", () => {
  const prefix = `tenant-test-${randomUUID().slice(0, 8)}`;
  const identity = { actor: prefix, admin: true, tenant_id: null, tenant_slug: null };
  const client = () => createRouterClient(router, { context: { identity, db: database() } });
  let admin: postgres.Sql;
  let server: ServerType;
  let url: string;
  beforeAll(async () => {
    const roleUrl = new URL(testUrl!);
    roleUrl.username = "control_rt"; roleUrl.password = "control_rt";
    vi.stubEnv("MDP_CONTROL_RT_URL", roleUrl.toString());
    vi.stubEnv("MDP_AUTH_MODE", "dev");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    admin = postgres(testUrl!, { onnotice: () => {} });
    url = await new Promise<string>(resolve => {
      server = serve({ fetch: app.fetch, hostname: "127.0.0.1", port: 0 }, info => resolve(`http://127.0.0.1:${info.port}`));
    });
  });
  afterAll(async () => {
    if (server) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    if (admin) {
      await admin`DELETE FROM control.target_set WHERE tenant_id IN (SELECT id FROM control.tenant WHERE slug LIKE ${prefix + "%"})`;
      await admin`DELETE FROM control.audit_log WHERE actor=${prefix} OR after->'input'->>'slug' LIKE ${prefix + "%"} OR subject IN (SELECT id::text FROM control.tenant WHERE slug LIKE ${prefix + "%"})`;
      await admin`DELETE FROM control.tenant WHERE slug LIKE ${prefix + "%"}`;
      await admin.end();
      await database().end();
    }
    vi.unstubAllEnvs();
  });
  const cli = (...args: string[]) => promisify(execFile)("pnpm", ["mdp", "tenants", ...args], {
    env: { PATH: process.env.PATH, HOME: process.env.HOME, MDP_AUTH_MODE: "dev", MDP_CONTROL_API_URL: url },
    timeout: 15000,
  });
  const form = (action: string, fields: Record<string, string>) => app.request(`${url}/actions/${action}`, {
    method: "POST", body: new URLSearchParams({ back: "/tenants", ...fields }),
  });
  it("creates, lists and patches through the router with atomic target sets and plain audited refusals", async () => {
    const tenant = await client().tenants.create({ slug: prefix, name: "Synthetic tenant" });
    expect(tenant.status).toBe("active");
    expect((await client().targets.listSets({})).filter(s => s.tenant_id === tenant.id).map(s => s.kind).sort())
      .toEqual([]);
    await expect(client().tenants.create({ slug: prefix, name: "Duplicate" })).rejects.toMatchObject({
      status: 409, message: `Tenant ${prefix} already exists.`, data: { error_class: "tenant_exists" },
    });
    expect((await client().tenants.list({})).filter(t => t.slug === prefix)).toEqual([tenant]);
    expect(await client().tenants.patch({ id: tenant.id, name: "Updated tenant" })).toMatchObject({ name: "Updated tenant", status: "active" });
    expect(await client().tenants.patch({ id: tenant.id, status: "inactive" })).toMatchObject({ name: "Updated tenant", status: "inactive" });
    await expect(client().tenants.patch({ id: randomUUID(), status: "active" })).rejects.toMatchObject({ status: 404, message: "Tenant does not exist." });
    const logs = await admin`SELECT action,after->>'state' AS state,after->>'error_class' AS error_class FROM control.audit_log WHERE actor=${prefix} ORDER BY at`;
    expect(logs.map(r => [r.action, r.state, r.error_class])).toEqual([
      ["tenants.create", "succeeded", null], ["tenants.create", "failed", "tenant_exists"],
      ["tenants.patch", "succeeded", null], ["tenants.patch", "succeeded", null], ["tenants.patch", "failed", "not_found"],
    ]);
  });
  it("dispatches CLI create/list/patch over HTTP, exits plainly on duplicate and missing tenants", async () => {
    const slug = `${prefix}-cli`;
    expect((await cli("create", "--slug", slug, "--name", "CLI tenant")).stdout).toContain(`"slug": "${slug}"`);
    expect((await cli("patch", "--tenant", slug, "--status", "inactive", "--name", "CLI updated")).stdout).toContain('"status": "inactive"');
    expect((await cli("patch", "--tenant", slug, "--status", "active")).stdout).toContain('"name": "CLI updated"');
    expect((await cli("list")).stdout).toContain(`"slug": "${slug}"`);
    await expect(cli("create", "--slug", slug, "--name", "Duplicate")).rejects.toMatchObject({ code: 1, stderr: expect.stringContaining(`Tenant ${slug} already exists.`) });
    await expect(cli("patch", "--tenant", `${prefix}-missing`, "--status", "inactive")).rejects.toMatchObject({ code: 1, stderr: expect.stringContaining(`Tenant ${prefix}-missing does not exist.`) });
    await expect(cli("patch", "--tenant", slug, "--status", "paused")).rejects.toMatchObject({ code: 1, stderr: expect.stringContaining("Invalid status. Use --status active or --status inactive.") });
    await expect(cli("patch", "--tenant", slug)).rejects.toMatchObject({ code: 1, stderr: expect.stringContaining("Usage:") });
  }, 30000);
  it("creates and edits via forms with 303 results, links to the existing keys command", async () => {
    const slug = `${prefix}-form`;
    const created = await form("create-tenant", { slug, name: "Form tenant" });
    expect(created.status).toBe(303);
    expect(created.headers.get("location")).toContain("/tenants?result=");
    const tenant = (await client().tenants.list({})).find(t => t.slug === slug)!;
    expect((await form("patch-tenant", { id: tenant.id, name: "Form updated", status: "inactive" })).status).toBe(303);
    const page = await app.request(`${url}/tenants?key=${slug}`);
    expect(page.status).toBe(200);
    const html = await page.text();
    for (const text of ["Form updated", "inactive", `pnpm --dir control mdp keys create --tenant ${slug}`, `href="/tenants?key=${slug}#issue-key"`]) expect(html).toContain(text);
    const duplicate = await form("create-tenant", { slug, name: "Duplicate" });
    const failure = await app.request(duplicate.headers.get("location")!);
    expect(await failure.text()).toContain(`Tenant ${slug} already exists.`);
    const foreign = await app.request(`${url}/actions/create-tenant`, { method: "POST", headers: { origin: "https://foreign.invalid" }, body: new URLSearchParams({ slug: `${prefix}-foreign`, name: "Foreign" }) });
    expect(foreign.status).toBe(403);
  });
  it("refuses tenant administration to a reader", async () => {
    const reader = createRouterClient(router, { context: { identity: { ...identity, admin: false }, db: database() } });
    await expect(reader.tenants.list({})).rejects.toMatchObject({ status: 403 });
    await expect(reader.tenants.create({ slug: `${prefix}-reader`, name: "Reader" })).rejects.toMatchObject({ status: 403 });
    await expect(reader.tenants.patch({ id: randomUUID(), status: "inactive" })).rejects.toMatchObject({ status: 403 });
  });
});
