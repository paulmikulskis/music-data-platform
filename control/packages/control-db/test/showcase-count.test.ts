import { expect, it } from "vitest";
import postgres from "postgres";
import { drizzle } from "drizzle-orm/postgres-js";
import { migrate } from "drizzle-orm/postgres-js/migrator";
import { mkdtemp, cp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { z } from "zod";
import {
  readSources,
  readRelationCounts,
} from "../../../apps/control-api/src/platform.js";
const url = process.env.MDP_CALL_TEST_URL;
it.skipIf(!url)(
  "upgrades count storage after 0035 with old and new readers and narrow bootstrap grants",
  async () => {
    const adminURL = new URL(url!);
    expect(["127.0.0.1", "localhost"]).toContain(adminURL.hostname);
    adminURL.pathname = "/postgres";
    const admin = postgres(adminURL.toString(), { onnotice: () => {} });
    const name = `count_upgrade_${Date.now()}`;
    await admin.unsafe(`CREATE DATABASE ${name} OWNER migrator`);
    adminURL.pathname = "/" + name;
    adminURL.username = "migrator";
    adminURL.password = "migrator";
    const db = postgres(adminURL.toString(), { onnotice: () => {} });
    const folder = await mkdtemp(path.join(tmpdir(), "count-migrations-"));
    const migrations = path.resolve("packages/control-db/drizzle");
    try {
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
      const index = journal.entries.findIndex((row) =>
        row.tag.endsWith("_showcase_relation_count"),
      );
      expect(journal.entries[index]?.idx).toBeGreaterThan(35);
      expect(journal.entries[index - 1]?.tag).toBe("0035_run_settlement");
      journal.entries = journal.entries.slice(0, index);
      await writeFile(file, JSON.stringify(journal));
      await migrate(drizzle(db), { migrationsFolder: folder });
      expect(
        (
          await db`SELECT to_regclass('control.showcase_relation_count') AS relation`
        )[0]?.relation,
      ).toBeNull();
      expect((await readSources(db)).sources).toEqual([]);
      await migrate(drizzle(db), { migrationsFolder: migrations });
      // The same reviewed grant file runs in local init and deployed bootstrap.
      await db.unsafe(
        await readFile(
          path.resolve("packages/control-db/sql/showcase-count.sql"),
          "utf8",
        ),
      );
      expect((await readSources(db)).sources).toEqual([]);
      const warehouse = String(
        (
          await db`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres','warehouse','unused',true) RETURNING id`
        )[0]!.id,
      );
      const result = await readRelationCounts(db, {
        keys: [
          {
            relation: "marts.mart_top_movers_current",
            build_key: "missed",
            input_hash: "a".repeat(64),
          },
        ],
      });
      expect(result.counts[0]).toMatchObject({
        denominator: null,
        capture: null,
        last_good: null,
        suppression: "not_captured",
      });
      const grants =
        await db`SELECT has_table_privilege('control_rt','control.showcase_relation_count','INSERT') AS insert,
      has_column_privilege('control_rt','control.showcase_relation_count','row_count','UPDATE') AS update,
      has_column_privilege('control_rt','control.showcase_relation_count','input_hash','UPDATE') AS identity,
      has_table_privilege('control_rt','control.showcase_relation_count','DELETE') AS delete,
      has_table_privilege('functions_rt','control.showcase_relation_count','SELECT') AS service,
      has_table_privilege('showcase_wh','control.showcase_relation_count','SELECT') AS warehouse`;
      expect(grants[0]).toEqual({
        insert: true,
        update: true,
        identity: false,
        delete: false,
        service: false,
        warehouse: false,
      });
      for (const relation of [
        "tenant_demo_marts.song",
        "raw.accounts",
        "marts.bad-name",
      ]) {
        await expect(
          db`INSERT INTO control.showcase_relation_count VALUES (${warehouse},${relation},'build',now(),1,NULL,'exact',${"a".repeat(64)})`,
        ).rejects.toMatchObject({ code: "23514" });
      }
      // Rolling readers back leaves additive storage in place.
      expect((await readSources(db)).sources).toEqual([]);
      expect(
        (
          await db`SELECT count(*)::int AS count FROM control.showcase_relation_count`
        )[0]?.count,
      ).toBe(0);
    } finally {
      await db.end();
      await admin.unsafe(`DROP DATABASE ${name} WITH (FORCE)`);
      await admin.end();
      await rm(folder, { recursive: true, force: true });
    }
  },
  30000,
);
