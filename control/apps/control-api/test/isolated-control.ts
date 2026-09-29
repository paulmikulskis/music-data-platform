import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import postgres from "postgres";

// Use the committed schema without locking another test's control tables.
export async function isolatedControl(url: string) {
  const admin = postgres(url, { max: 1, onnotice: () => {} });
  const name = `mdp_resilience_${randomUUID().replaceAll("-", "")}`;
  await admin.unsafe(`CREATE DATABASE ${name}`);
  const target = new URL(url);
  target.pathname = `/${name}`;
  const db = postgres(target.toString(), { max: 1, onnotice: () => {} });
  const close = async () => {
    await db.end();
    await admin.unsafe(`DROP DATABASE ${name}`);
    await admin.end();
  };
  try {
    const folder = new URL("../../../packages/control-db/drizzle/", import.meta.url);
    const journal = JSON.parse(readFileSync(new URL("meta/_journal.json", folder), "utf8")) as { entries: { tag: string }[] };
    for (const entry of journal.entries) {
      const sql = readFileSync(new URL(`${entry.tag}.sql`, folder), "utf8");
      await db.begin(async tx => {
        for (const statement of sql.split("--> statement-breakpoint"))
          if (statement.trim()) await tx.unsafe(statement);
      });
    }
    await db`INSERT INTO control.warehouse(adapter,database,dsn_secret_ref,is_production) VALUES ('postgres','fixture','fixture',true)`;
    await db`INSERT INTO control.runner_mode(id,runner) VALUES (true,'core')`;
    return { db, close };
  } catch (error) {
    await close();
    throw error;
  }
}
