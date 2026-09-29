import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { database } from "./db.js";
const dir = new URL("../../../../ops/runbooks/", import.meta.url);
const db = database();
await db.begin(async (tx) => {
  for (const file of readdirSync(dir).filter((f) => f.endsWith(".md"))) {
    const body = readFileSync(new URL(file, dir), "utf8");
    const slug = file.slice(0, -3).replaceAll("_", "-");
    await tx.unsafe(
      "INSERT INTO control.runbook(slug,title,body_md) VALUES ($1,$2,$3) ON CONFLICT(slug) DO UPDATE SET title=EXCLUDED.title,body_md=EXCLUDED.body_md",
      [slug, body.split("\n")[0]?.replace(/^#\s*/, "") ?? slug, body],
    );
  }
  await tx.unsafe(
    "INSERT INTO control.audit_log(actor,action,subject,after) VALUES ('runbook-seeder','runbooks.seed','ops/runbooks',$1::text::jsonb)",
    [JSON.stringify({ source: "ops/runbooks" })],
  );
});
await db.end();
console.log("Runbooks seeded from ops/runbooks; audit recorded");
