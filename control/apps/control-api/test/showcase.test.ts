import { createHash, randomBytes } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { serve, type ServerType } from "@hono/node-server";
import { createRouterClient } from "@orpc/server";
import { z } from "zod";
import { redeem, verifyLink } from "@mdp/showcase-auth";
import { isolatedControl } from "./isolated-control.js";
import { app } from "../src/app.js";
import { router } from "../src/router.js";
import { database } from "../src/db.js";

const testUrl = process.env.MDP_TENANTS_TEST_URL;
describe.skipIf(!testUrl)("showcase links through control-api", () => {
  let isolated: Awaited<ReturnType<typeof isolatedControl>>;
  let server: ServerType;
  let base: string;
  const adminKey = randomBytes(32).toString("hex");
  const staffKey = randomBytes(32).toString("hex");
  const identity = {
    actor: "access-operator",
    admin: true,
    tenant_id: null,
    tenant_slug: null,
  };
  const person = {
    handle: "access-fixture",
    display_name: "Access fixture",
    email: "access@example.invalid",
    admin_key: "fixture-only",
    api_key_id: "00000000-0000-4000-8000-000000000071",
  };
  const client = (admin = true, staff = false, promoter = false) =>
    createRouterClient(router, {
      context: {
        identity: { ...identity, admin, staff, promoter },
        db: database(),
      },
    });
  beforeAll(async () => {
    if (
      !testUrl ||
      !["localhost", "127.0.0.1"].includes(new URL(testUrl).hostname)
    )
      throw new Error("Use a disposable loopback database.");
    isolated = await isolatedControl(testUrl);
    const [row] = await isolated.db`SELECT current_database() AS name`;
    const roleUrl = new URL(testUrl);
    roleUrl.pathname = `/${z.object({ name: z.string() }).parse(row).name}`;
    roleUrl.username = "control_rt";
    roleUrl.password = "control_rt";
    vi.stubEnv("MDP_CONTROL_RT_URL", roleUrl.toString());
    vi.stubEnv("MDP_AUTH_MODE", "production");
    vi.stubEnv("CLERK_SECRET_KEY", "");
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([person]));
    vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "l".repeat(32));
    vi.stubEnv("MDP_SHOWCASE_ORIGIN", "http://localhost:3100");
    for (const [key, role] of [
      [adminKey, "admin"],
      [staffKey, "staff"],
    ]) {
      await isolated.db`INSERT INTO control.api_key (key_hash, label, role) VALUES (${createHash("sha256").update(key!).digest("hex")}, ${`access-${role}`}, ${role!})`;
    }
    base = await new Promise((resolve) => {
      server = serve(
        { fetch: app.fetch, hostname: "127.0.0.1", port: 0 },
        (info) => resolve(`http://127.0.0.1:${info.port}`),
      );
    });
  }, 30000);
  afterAll(async () => {
    if (server)
      await new Promise<void>((resolve) => server.close(() => resolve()));
    await database().end();
    await isolated?.close();
    vi.unstubAllEnvs();
  });
  it("mints the unchanged token, attributes the operator and redacts its one-time URL", async () => {
    const result = await client().showcase.link({ person: person.handle });
    const token = new URL(result.link_url).searchParams.get("t")!;
    const link = verifyLink(token)!;
    expect(link).toMatchObject({
      handle: person.handle,
      nonce: expect.stringMatching(/^[A-Za-z0-9_-]{22}$/),
    });
    expect(
      await isolated.db`SELECT created_by, used_at FROM control.showcase_link WHERE nonce=${link.nonce}`,
    ).toMatchObject([{ created_by: identity.actor, used_at: null }]);
    const logs =
      await isolated.db`SELECT subject, after FROM control.audit_log WHERE actor=${identity.actor} AND action='showcase.link'`;
    expect(logs).toMatchObject([
      {
        subject: person.handle,
        after: { state: "succeeded", result: { link_url: "[REDACTED]" } },
      },
    ]);
    expect(JSON.stringify(logs)).not.toContain(token);
    expect(await redeem(database(), token, null)).toBeTruthy();
    expect(await redeem(database(), token, null)).toBeNull();
  });
  it("revokes a first RPC link after removing its person before redemption", async () => {
    const newcomer = {
      ...person,
      handle: "access-unredeemed",
      api_key_id: "00000000-0000-4000-8000-000000000072",
    };
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([person, newcomer]));
    try {
      expect(
        await isolated.db`SELECT api_key_id FROM control.showcase_actor WHERE handle=${newcomer.handle}`,
      ).toHaveLength(0);
      const { link_url } = await client().showcase.link({
        person: newcomer.handle,
      });
      const token = new URL(link_url).searchParams.get("t")!;
      const link = verifyLink(token)!;
      expect(
        await isolated.db`SELECT api_key_id FROM control.showcase_actor WHERE handle=${newcomer.handle}`,
      ).toMatchObject([{ api_key_id: newcomer.api_key_id }]);
      expect(
        await isolated.db`SELECT id_hash FROM control.showcase_session WHERE handle=${newcomer.handle}`,
      ).toHaveLength(0);
      vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([person]));
      await promisify(execFile)(
        "pnpm",
        ["mdp", "showcase", "revoke", "--person", newcomer.handle],
        {
          env: {
            PATH: process.env.PATH,
            HOME: process.env.HOME,
            MDP_CONTROL_RT_URL: process.env.MDP_CONTROL_RT_URL,
            MDP_SHOWCASE_PEOPLE: process.env.MDP_SHOWCASE_PEOPLE,
          },
          timeout: 15000,
        },
      );
      expect(
        await isolated.db`SELECT revoked_at IS NOT NULL AS revoked, used_at FROM control.showcase_link WHERE nonce=${link.nonce}`,
      ).toMatchObject([{ revoked: true, used_at: null }]);
      expect(
        await isolated.db`SELECT actor FROM control.audit_log WHERE action='showcase.revoke' AND subject=${newcomer.handle}`,
      ).toMatchObject([{ actor: `api-key:${newcomer.api_key_id}` }]);
      vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([person, newcomer]));
      expect(await redeem(database(), token, null)).toBeNull();
    } finally {
      vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([person]));
    }
  });
  it("refuses a conflicting actor without committing a link", async () => {
    await client().showcase.link({ person: person.handle });
    const conflicting = { ...person, handle: "access-conflict" };
    vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([conflicting]));
    try {
      await expect(
        client().showcase.link({ person: conflicting.handle }),
      ).rejects.toThrow("Admin key belongs to another handle");
      expect(
        await isolated.db`SELECT nonce FROM control.showcase_link WHERE handle=${conflicting.handle}`,
      ).toHaveLength(0);
      expect(
        await isolated.db`SELECT after->>'state' AS state FROM control.audit_log WHERE subject=${conflicting.handle}`,
      ).toMatchObject([{ state: "failed" }]);
    } finally {
      vi.stubEnv("MDP_SHOWCASE_PEOPLE", JSON.stringify([person]));
    }
  });
  it("refuses reader, staff and promoter clients", async () => {
    for (const denied of [
      client(false),
      client(false, true),
      client(false, false, true),
    ]) {
      await expect(
        denied.showcase.link({ person: person.handle }),
      ).rejects.toMatchObject({ status: 403 });
    }
  });
  it("validates TTL boundaries and handles before writing", async () => {
    for (const ttl_seconds of [60, 604800]) {
      const { link_url } = await client().showcase.link({
        person: person.handle,
        ttl_seconds,
      });
      const link = verifyLink(new URL(link_url).searchParams.get("t")!)!;
      expect(link.exp - Math.floor(Date.now() / 1000)).toBeGreaterThanOrEqual(
        ttl_seconds - 1,
      );
    }
    for (const ttl_seconds of [59, 604801, 1.5]) {
      await expect(
        client().showcase.link({ person: person.handle, ttl_seconds }),
      ).rejects.toMatchObject({ status: 400 });
    }
    await expect(
      client().showcase.link({ person: "../invalid" }),
    ).rejects.toMatchObject({ status: 400 });
    await expect(
      client().showcase.link({ person: "absent" }),
    ).rejects.toMatchObject({
      status: 503,
      data: { error_class: "showcase_link_unavailable" },
    });
    expect(
      await isolated.db`SELECT nonce FROM control.showcase_link WHERE handle='absent'`,
    ).toHaveLength(0);
    expect(
      await isolated.db`SELECT after->>'state' AS state FROM control.audit_log WHERE subject='absent'`,
    ).toMatchObject([{ state: "failed" }]);
  });
  it("fails closed without signing configuration and never writes an unusable nonce", async () => {
    const before = await isolated.db`SELECT nonce FROM control.showcase_link`;
    vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "short");
    try {
      await expect(
        client().showcase.link({ person: person.handle }),
      ).rejects.toMatchObject({ status: 503 });
      expect(
        await isolated.db`SELECT nonce FROM control.showcase_link`,
      ).toHaveLength(before.length);
    } finally {
      vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "l".repeat(32));
    }
  });
  it("serves HTTP only to an admin and sends no cacheable link", async () => {
    const post = (key?: string) =>
      fetch(`${base}/api/showcase/link`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(key ? { "x-api-key": key } : {}),
        },
        body: JSON.stringify({ person: person.handle }),
      });
    expect((await post()).status).toBe(401);
    expect((await post(staffKey)).status).toBe(403);
    const response = await post(adminKey);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const result = z.object({ link_url: z.url() }).parse(await response.json());
    expect(
      verifyLink(new URL(result.link_url).searchParams.get("t")!),
    ).not.toBeNull();
  });
  it("runs the CLI with only an API URL and admin key", async () => {
    const cli = (key: string, ...args: string[]) =>
      promisify(execFile)(
        "pnpm",
        ["mdp", "showcase", "link", "--person", person.handle, ...args],
        {
          env: {
            PATH: process.env.PATH,
            HOME: process.env.HOME,
            MDP_CONTROL_API_URL: base,
            MDP_API_KEY: key,
          },
          timeout: 15000,
        },
      );
    const { stdout } = await cli(adminKey, "--ttl", "1m");
    const url = stdout
      .split("\n")
      .find((line) => line.startsWith("http://localhost:3100/sign-in?t="))!;
    expect(verifyLink(new URL(url).searchParams.get("t")!)).not.toBeNull();
    expect(stdout).toContain("select Sign in");
    await expect(cli(staffKey)).rejects.toMatchObject({ code: 1 });
    await expect(cli(adminKey, "--ttl", "0m")).rejects.toMatchObject({
      code: 1,
    });
  }, 30000);
});
