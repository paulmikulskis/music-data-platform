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
    `draft grants on ${upgraded ? "upgraded" : "fresh"} install`,
    async () => {
      const adminURL = new URL(url);
      expect(["127.0.0.1", "localhost"]).toContain(adminURL.hostname);
      adminURL.pathname = "/postgres";
      const admin = postgres(adminURL.toString(), { onnotice: () => {} });
      const name = `draft_grants_${upgraded ? "upgrade" : "fresh"}_${Date.now()}`;
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
            const draftIndex = journal.entries.findIndex(
              (entry) => entry.tag === "0034_showcase_draft",
            );
            expect(draftIndex).toBeGreaterThan(0);
            expect(journal.entries[draftIndex]?.idx).toBe(34);
            expect(journal.entries[draftIndex - 1]?.tag).toBe(
              "0033_alert_attempt_baseline",
            );
            journal.entries = journal.entries.slice(0, draftIndex);
            await writeFile(file, JSON.stringify(journal));
            await migrate(drizzle(migrator), { migrationsFolder: folder });
            const [before] =
              await db`SELECT to_regclass('control.showcase_draft') AS draft,
              EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='control' AND table_name='alert' AND column_name='attempt_no') AS attempt`;
            expect(before).toEqual({ draft: null, attempt: true });
          }
          await migrate(drizzle(migrator), { migrationsFolder: migrations });
        } finally {
          await migrator.end();
        }
        const [alert] =
          await db`SELECT EXISTS(SELECT 1 FROM information_schema.columns
          WHERE table_schema='control' AND table_name='alert' AND column_name='attempt_no') AS attempt,
          to_regclass('control.alert_run_attempt_class_unique') IS NOT NULL AS dedup`;
        expect(alert).toEqual({ attempt: true, dedup: true });
        for (const table of [
          "showcase_draft",
          "showcase_rule",
          "showcase_call_rule",
        ]) {
          const [grants] = await db`SELECT
            has_table_privilege('functions_rt',${`control.${table}`},'SELECT') AS functions_read,
            has_table_privilege('control_rt',${`control.${table}`},'SELECT') AS control_read,
            has_table_privilege('control_rt',${`control.${table}`},'INSERT') AS control_insert,
            has_table_privilege('control_rt',${`control.${table}`},'DELETE') AS control_delete`;
          expect(grants).toEqual({
            functions_read: false,
            control_read: true,
            control_insert: true,
            control_delete: false,
          });
          const columns =
            await db`SELECT column_name FROM information_schema.columns WHERE table_schema='control' AND table_name=${table}`;
          const mutable =
            table === "showcase_draft"
              ? ["rules", "closed_at", "closed_by", "close_key"]
              : table === "showcase_rule"
                ? ["backed_by", "backed_at"]
                : [];
          for (const column of columns) {
            const [permission] =
              await db`SELECT has_column_privilege('control_rt',${`control.${table}`},${column.column_name},'UPDATE') AS allowed`;
            expect(permission.allowed, `${table}.${column.column_name}`).toBe(
              mutable.includes(column.column_name),
            );
          }
        }
        const [draftWeek] =
          await db`SELECT has_column_privilege('control_rt','control.showcase_call','draft_week','UPDATE') AS allowed`;
        expect(draftWeek.allowed).toBe(false);
      } finally {
        await db.end();
        await admin.unsafe(`DROP DATABASE ${name} WITH (FORCE)`);
        await admin.end();
        await rm(folder, { recursive: true, force: true });
      }
    },
    30000,
  );
