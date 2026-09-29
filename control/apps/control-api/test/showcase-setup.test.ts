import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { serve, type ServerType } from "@hono/node-server";
import { z } from "zod";
import postgres from "postgres";
import { people, redeem, verifyLink } from "@mdp/showcase-auth";
import { app } from "../src/app.js";
import { database } from "../src/db.js";

const url = process.env.MDP_SHOWCASE_SETUP_TEST_URL;
describe.skipIf(!url)(
  "Give a viewer access on a disposable local stack",
  () => {
    let db: postgres.Sql;
    let server: ServerType;
    let apiUrl: string;
    const handle = `access-${randomUUID().slice(0, 8)}`;
    const run = promisify(execFile);
    const env = { PATH: process.env.PATH, HOME: process.env.HOME };
    beforeAll(async () => {
      if (
        !url ||
        !["127.0.0.1", "localhost"].includes(new URL(url).hostname) ||
        new URL(url).pathname !== "/control"
      ) {
        throw new Error("Use a disposable loopback control database.");
      }
      db = postgres(url);
      const roleUrl = new URL(url);
      roleUrl.username = "control_rt";
      roleUrl.password = "control_rt";
      vi.stubEnv("MDP_CONTROL_RT_URL", roleUrl.toString());
      vi.stubEnv("MDP_AUTH_MODE", "production");
      vi.stubEnv("CLERK_SECRET_KEY", "");
      apiUrl = await new Promise((resolve) => {
        server = serve(
          { fetch: app.fetch, hostname: "127.0.0.1", port: 0 },
          (info) => resolve(`http://127.0.0.1:${info.port}`),
        );
      });
    });
    afterAll(async () => {
      if (server)
        await new Promise<void>((resolve) => server.close(() => resolve()));
      await database().end();
      if (db) {
        await db`DELETE FROM control.showcase_session WHERE handle=${handle}`;
        await db`DELETE FROM control.showcase_link WHERE handle=${handle}`;
        await db`DELETE FROM control.showcase_actor WHERE handle=${handle}`;
        await db`DELETE FROM control.audit_log WHERE subject=${handle}`;
        await db`DELETE FROM control.audit_log WHERE subject IN (SELECT id::text FROM control.api_key WHERE label=${`showcase-reader-${handle}`})`;
        await db`DELETE FROM control.api_key WHERE label IN (${`showcase-${handle}`}, ${`showcase-reader-${handle}`})`;
        await db.end();
      }
      vi.unstubAllEnvs();
    });
    it("runs issuance, id lookup, people configuration, reader key, deploy preview and first-link redemption", async () => {
      const issued = await run(
        "bash",
        ["../ops/fly/postgres/admin-add.sh", `showcase-${handle}`],
        {
          env: { ...env, MDP_CONTROL_DATABASE_URL: url },
        },
      );
      const key = issued.stdout.trim();
      expect(key.startsWith("mdp_")).toBe(true);
      const cliEnv = { ...env, MDP_CONTROL_API_URL: apiUrl, MDP_API_KEY: key };
      const listed = await run("pnpm", ["--silent", "mdp", "keys", "list"], {
        env: cliEnv,
      });
      const keys = z
        .array(z.object({ id: z.uuid(), label: z.string() }))
        .parse(JSON.parse(listed.stdout));
      const id = keys.find((row) => row.label === `showcase-${handle}`)?.id;
      expect(id).toBeTruthy();
      vi.stubEnv(
        "MDP_SHOWCASE_PEOPLE",
        JSON.stringify([
          {
            handle,
            display_name: "Access fixture",
            email: "access@example.invalid",
            admin_key: key,
            api_key_id: id,
          },
        ]),
      );
      const reader = await run(
        "pnpm",
        ["--silent", "mdp", "keys", "create", "--role", "reader", "--global", "--label", `showcase-reader-${handle}`],
        { env: cliEnv },
      );
      expect(JSON.parse(reader.stdout)).toMatchObject({ role: "reader", tenant_id: null, api_key: expect.stringMatching(/^mdp_/) });
      vi.stubEnv("MDP_SHOWCASE_LINK_SECRET", "l".repeat(32));
      vi.stubEnv("MDP_SHOWCASE_ORIGIN", "http://localhost:3100");
      expect(people()).toHaveLength(1);
      const preview = await run(
        "bash",
        [
          "../ops/deploy.sh",
          "--dry-run",
          "--app",
          "mdp-control-api",
          "--app",
          "mdp-showcase",
        ],
        {
          env: {
            ...env,
            FLY_ORG: "example-org",
            MDP_SHOWCASE_PEOPLE: process.env.MDP_SHOWCASE_PEOPLE,
          },
        },
      );
      expect(preview.stdout).toContain("mdp-showcase");
      expect(preview.stdout).toContain("mdp-control-api");
      const minted = await run(
        "pnpm",
        ["--silent", "mdp", "showcase", "link", "--person", handle],
        { env: cliEnv },
      );
      const linkUrl = minted.stdout
        .split("\n")
        .find((line) => line.startsWith("http://localhost:3100/sign-in?t="))!;
      const token = new URL(linkUrl).searchParams.get("t")!;
      expect(verifyLink(token)?.handle).toBe(handle);
      expect(await redeem(database(), token, null)).toBeTruthy();
      expect(await redeem(database(), token, null)).toBeNull();
    }, 30000);
  },
);
