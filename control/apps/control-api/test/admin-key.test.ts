import { afterAll, describe, expect, it } from "vitest";
import { createHash, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import postgres from "postgres";
import { authenticate } from "../src/auth.js";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const url = process.env.MDP_ADMIN_TEST_URL;

// control-ci supplies a disposable, migrated control database; never infer a deployed URL.
describe.skipIf(!url)("admin key ceremonies", () => {
  const db = postgres(url ?? "postgresql://127.0.0.1:1/unused", { max: 1 });
  const prefix = `admin-test-${randomUUID()}`;
  const scratch = mkdtempSync(join(tmpdir(), "mdp-admin-test-"));
  afterAll(async () => {
    await db`DELETE FROM control.api_key WHERE label LIKE ${prefix + '%'}`;
    await db.end();
    rmSync(scratch, { recursive: true, force: true });
  });
  function run(action: "add" | "revoke", label: string, args: string[] = [], env = {}) {
    return spawnSync("bash", [join(root, `ops/fly/postgres/admin-${action}.sh`), label, ...args], {
      cwd: scratch, encoding: "utf8", timeout: 20_000,
      env: { ...process.env, MDP_CONTROL_DATABASE_URL: url, ...env },
    });
  }
  function auth(key: string) {
    return authenticate(new Request("http://localhost/ops", { headers: { "x-api-key": key } }), db);
  }
  it("prints one key, stores only its hash, authenticates as admin, and revokes immediately", async () => {
    // Quotes stay data; labels are not interpolated into SQL.
    const label = `${prefix}-operator's`;
    const added = run("add", label);
    expect(added.status).toBe(0);
    expect(/^mdp_[A-Za-z0-9_-]{43}\n$/.test(added.stdout)).toBe(true);
    expect(added.stderr).toBe("");
    const key = added.stdout.trim();
    const stored = await db`SELECT * FROM control.api_key WHERE label=${label}`;
    expect(stored).toHaveLength(1);
    expect(stored[0]?.key_hash === createHash("sha256").update(key).digest("hex")).toBe(true);
    expect(JSON.stringify(stored).includes(key)).toBe(false);
    expect(await auth(key)).toMatchObject({ admin: true, tenant_id: null, tenant_slug: null });
    const duplicate = run("add", label);
    expect(duplicate.status).toBe(1);
    expect(duplicate.stdout).toBe("");
    expect(await db`SELECT id FROM control.api_key WHERE label=${label}`).toHaveLength(1);
    const revoked = run("revoke", label);
    expect(revoked.status).toBe(0);
    expect(revoked.stdout).toBe("");
    await expect(auth(key)).rejects.toMatchObject({ error_class: "unauthorized", status: 401 });
    expect(run("revoke", label).status).toBe(0);
    const replacement = run("add", label);
    expect(replacement.status).toBe(0);
    expect(await auth(replacement.stdout.trim())).toMatchObject({ admin: true });
    await expect(auth(key)).rejects.toMatchObject({ status: 401 });
  }, 30_000);
  it("leaves reader and promoter keys with the same label alone", async () => {
    const label = `${prefix}-roles`;
    for (const role of ["reader", "promoter"])
      await db`INSERT INTO control.api_key(key_hash,label,role)
        VALUES (${createHash("sha256").update(randomUUID()).digest("hex")},${label},${role})`;
    expect(run("revoke", label).status).toBe(1);
    expect(run("add", label).status).toBe(0);
    expect(run("revoke", label).status).toBe(0);
    const untouched = await db`SELECT role FROM control.api_key WHERE label=${label} AND revoked_at IS NULL`;
    expect(untouched.map(row => row.role).sort()).toEqual(["promoter", "reader"]);
  }, 30_000);
  it("delivers through secret store stdin only and rolls back a failed delivery", async () => {
    // This executable is the only secret store used by the tests. No external writes.
    writeFileSync(join(scratch, "secret_store"), `#!/usr/bin/env node
const fs = require('node:fs');
const key = fs.readFileSync(0, 'utf8');
fs.writeFileSync(process.env.ADMIN_TEST_CAPTURE, JSON.stringify({args: process.argv.slice(2), key}));
console.log(key); console.error(key);
process.exit(Number(process.env.ADMIN_TEST_FAIL || 0));
`, { mode: 0o700 });
    const capture = join(scratch, "capture.json");
    const env = { PATH: `${scratch}:${process.env.PATH}`, ADMIN_TEST_CAPTURE: capture };
    const label = `${prefix}-secret_store`;
    const added = run("add", label, ["--secret_store", "test-project/test-config/MDP_API_KEY"], env);
    expect(added.status).toBe(0);
    expect(added.stdout).toBe("");
    const delivered = JSON.parse(readFileSync(capture, "utf8")) as { args: string[]; key: string };
    expect(delivered.args).toEqual(["set", "MDP_API_KEY"]);
    expect(added.stderr.includes(delivered.key)).toBe(false);
    expect(await auth(delivered.key)).toMatchObject({ admin: true });
    const failed = run("add", `${label}-failed`, ["--secret_store", "test-project/test-config/MDP_API_KEY"],
      { ...env, ADMIN_TEST_FAIL: "1" });
    expect(failed.status).toBe(1);
    expect(failed.stdout).toBe("");
    const lost = JSON.parse(readFileSync(capture, "utf8")) as { key: string };
    expect(failed.stderr.includes(lost.key)).toBe(false);
    expect(await db`SELECT id FROM control.api_key WHERE label=${label + '-failed'}`).toHaveLength(0);
    await expect(auth(lost.key)).rejects.toMatchObject({ status: 401 });
  }, 30_000);
  it("refuses missing URLs, wrong databases, malformed options, and empty labels without output", () => {
    const wrongDB = new URL(url!); wrongDB.pathname = "/postgres";
    for (const result of [
      run("add", prefix, [], { MDP_CONTROL_DATABASE_URL: "" }),
      run("add", prefix, [], { MDP_CONTROL_DATABASE_URL: wrongDB.toString() }),
      run("add", prefix, ["--secret_store", "invalid"]),
      run("revoke", prefix, ["--secret_store", "test/test/KEY"]),
      run("add", ""),
    ]) {
      expect(result.status).toBe(1);
      expect(result.stdout).toBe("");
    }
  }, 30_000);
});
