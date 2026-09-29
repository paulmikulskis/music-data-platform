import { createHash, randomBytes } from "node:crypto";
import { spawnSync } from "node:child_process";
import postgres from "postgres";

class Refusal extends Error {}

async function main() {
  const [action, label, flag, destination, ...extra] = process.argv.slice(2);
  if (!label || !["add", "revoke"].includes(action ?? "") || extra.length ||
      (flag !== undefined && (action !== "add" || flag !== "--secret_store" || !destination)))
    throw new Refusal("usage: admin-add.sh <label> [--secret_store <project>/<config>/<NAME>] | admin-revoke.sh <label>");
  if (label.trim() !== label || label.length > 128 || /[\x00-\x1f\x7f]/.test(label))
    throw new Refusal("label must be 1-128 characters without surrounding whitespace or control characters");
  const target = destination?.match(/^([a-zA-Z0-9_-]+)\/([a-zA-Z0-9_-]+)\/([A-Z_][A-Z0-9_]*)$/);
  if (destination && !target)
    throw new Refusal("secret store destination must be <project>/<config>/<NAME>");
  const url = process.env.MDP_CONTROL_DATABASE_URL;
  if (!url) throw new Refusal("MDP_CONTROL_DATABASE_URL must select control as migrator or administrator");
  const db = postgres(url, { max: 1, connect_timeout: 5, onnotice: () => {} });
  try {
    const [database] = await db`SELECT current_database() AS name`;
    if (database?.name !== "control") throw new Refusal("connection must select the control database");
    // Match apiKeys.create and auth.ts: 32 random bytes, mdp_ prefix, SHA-256 hex only in storage.
    const key = action === "add" ? `mdp_${randomBytes(32).toString("base64url")}` : "";
    await db.begin(async tx => {
      // Serialize this ceremony by label without changing the API-key schema.
      await tx`SELECT pg_advisory_xact_lock(hashtextextended(${'admin-key:' + label}, 0))`;
      if (action === "revoke") {
        const revoked = await tx`UPDATE control.api_key SET revoked_at=coalesce(revoked_at,now())
          WHERE label=${label} AND role='admin' AND tenant_id IS NULL RETURNING id`;
        if (!revoked.length) throw new Refusal("no global admin key has that label");
        return;
      }
      const existing = await tx`SELECT id FROM control.api_key
        WHERE label=${label} AND role='admin' AND revoked_at IS NULL`;
      if (existing.length) throw new Refusal("an unrevoked admin key has that label; revoke it before issuing another");
      await tx`INSERT INTO control.api_key(key_hash,label,role)
        VALUES (${createHash("sha256").update(key).digest("hex")},${label},'admin')`;
      if (target) {
        // Never put the credential in argv or forward secret store output (including errors).
        const result = spawnSync("secret_store", ["set", target[3]!], {
          env: { ...process.env, SECRET_STORE_PROJECT: target[1]!, SECRET_STORE_CONFIG: target[2]! },
          input: key, stdio: ["pipe", "ignore", "ignore"], timeout: 60_000,
        });
        if (result.error || result.status !== 0)
          throw new Refusal("secret store write failed; no admin key committed; check the destination before retrying");
      }
    });
    // Output follows commit, and the stdout mode emits exactly one credential line.
    if (action === "add" && !target) process.stdout.write(`${key}\n`);
    else process.stderr.write(action === "add" ? "Admin key stored in secret store.\n" : "Admin key revoked.\n");
  } finally {
    await db.end();
  }
}

main().catch((error: unknown) => {
  // Database/child-process errors can include connection details or SQL parameters.
  process.stderr.write(`${error instanceof Refusal ? error.message : "Admin key operation failed; no credential printed; verify the label and destination before retrying"}\n`);
  process.exitCode = 1;
});
