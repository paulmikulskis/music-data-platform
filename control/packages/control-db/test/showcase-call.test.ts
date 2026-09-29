import { expect, it } from "vitest";
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { mkdtemp, cp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { z } from "zod";
const url = process.env.MDP_CALL_TEST_URL ?? "";
const migrations = path.resolve("packages/control-db/drizzle");
for (const upgraded of [false, true])
  it.skipIf(!url)(
    `call grants on ${upgraded ? "upgraded" : "fresh"} install`,
    async () => {
      const adminURL = new URL(url);
      expect(["127.0.0.1", "localhost"]).toContain(adminURL.hostname);
      adminURL.pathname = "/postgres";
      const admin = postgres(adminURL.toString(), { onnotice: () => {} });
      const name = `call_grants_${upgraded ? "upgrade" : "fresh"}_${Date.now()}`;
      await admin.unsafe(`CREATE DATABASE ${name} OWNER migrator`);
      const local = new URL(adminURL);
      local.pathname = `/${name}`;
      const db = postgres(local.toString(), { onnotice: () => {} });
      const folder = await mkdtemp(path.join(tmpdir(), "call-migrations-"));
      try {
        const migratorURL = new URL(local);
        migratorURL.username = "migrator";
        migratorURL.password = "migrator";
        const migrator = postgres(migratorURL.toString(), {
          onnotice: () => {},
        });
        try {
          if (upgraded) {
            await cp(migrations, folder, { recursive: true });
            const file = path.join(folder, "meta/_journal.json");
            const journal = z
              .object({
                version: z.string(),
                dialect: z.string(),
                entries: z.array(
                  z.object({
                    idx: z.number(),
                    version: z.string(),
                    when: z.number(),
                    tag: z.string(),
                    breakpoints: z.boolean(),
                  }),
                ),
              })
              .parse(JSON.parse(await readFile(file, "utf8")));
            const callIndex = journal.entries.findIndex((entry) =>
              entry.tag.endsWith("_showcase_call"),
            );
            expect(callIndex).toBeGreaterThan(0);
            journal.entries = journal.entries.slice(0, callIndex);
            await writeFile(file, JSON.stringify(journal));
            await migrate(drizzle(migrator), { migrationsFolder: folder });
          }
          await migrate(drizzle(migrator), { migrationsFolder: migrations });
        } finally {
          await migrator.end();
        }
        const [grants] = await db`SELECT
      has_table_privilege('functions_rt','control.showcase_call','SELECT') AS functions_read,
      has_table_privilege('control_rt','control.showcase_call','SELECT') AS control_read,
      has_table_privilege('control_rt','control.showcase_call','INSERT') AS control_insert,
      has_table_privilege('control_rt','control.showcase_call','DELETE') AS control_delete,
      has_column_privilege('control_rt','control.showcase_call','snapshot','UPDATE') AS facts_update,
      has_column_privilege('control_rt','control.showcase_call','undone_at','UPDATE') AS undo,
      has_column_privilege('control_rt','control.showcase_call','hidden_at','UPDATE') AS hide,
      has_column_privilege('control_rt','control.showcase_call','hidden_by','UPDATE') AS hidden_by`;
        expect(grants).toEqual({
          functions_read: false,
          control_read: true,
          control_insert: true,
          control_delete: false,
          facts_update: false,
          undo: true,
          hide: true,
          hidden_by: true,
        });
        for (const role of ["functions_rt", "control_rt"]) {
          const roleURL = new URL(local);
          roleURL.username = role;
          roleURL.password = role;
          const restricted = postgres(roleURL.toString());
          try {
            if (role === "functions_rt")
              await expect(
                restricted`SELECT * FROM control.showcase_call`,
              ).rejects.toThrow(/permission denied/);
            await expect(
              restricted`DELETE FROM control.showcase_call`,
            ).rejects.toThrow(/permission denied/);
            await expect(
              restricted`UPDATE control.showcase_call SET snapshot='changed'`,
            ).rejects.toThrow(/permission denied/);
          } finally {
            await restricted.end();
          }
        }
      } finally {
        await db.end();
        await admin.unsafe(`DROP DATABASE ${name} WITH (FORCE)`);
        await admin.end();
        await rm(folder, { recursive: true, force: true });
      }
    },
    30000,
  );
