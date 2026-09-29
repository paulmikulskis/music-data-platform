import { it, expect } from "vitest";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
it("rejects a supplied catalog missing an entire mart and requires explicit contract-only generation", () => {
  const dir = mkdtempSync(join(tmpdir(),"mdp-catalog-"));
  try {
    const catalog = join(dir,"catalog.json"); writeFileSync(catalog, JSON.stringify({nodes:{}}));
    const stale = spawnSync("pnpm",["exec","tsx","packages/data-sdk/scripts/catalog-to-zod.ts",catalog],{encoding:"utf8"});
    expect(stale.status).not.toBe(0); expect(stale.stderr).toContain("Catalog missing mart");
    const missing = spawnSync("pnpm",["exec","tsx","packages/data-sdk/scripts/catalog-to-zod.ts",join(dir,"missing.json")],{encoding:"utf8"});
    expect(missing.status).not.toBe(0);expect(missing.stderr).toContain("--contract-only");
  } finally { rmSync(dir,{recursive:true,force:true}); }
});
