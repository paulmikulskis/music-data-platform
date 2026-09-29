import { readFileSync } from "node:fs";
import { expect, it, vi } from "vitest";
import { dataContract } from "@mdp/data-sdk";
vi.mock("server-only", () => ({}));
import { dataApiReads } from "../server/reads";
// The generated registry the data API serves: `export const router={mart_a,mart_b};`.
const registry = readFileSync(
  new URL("../../data-api/src/generated/router.ts", import.meta.url),
  "utf8",
);
const served = registry.match(/export const router=\{([^}]*)\}/)?.[1]?.split(",") ?? [];
it("has a data API procedure for every mart the showcase reads", () => {
  expect(served.length).toBeGreaterThan(0);
  expect(dataApiReads.length).toBeGreaterThan(0);
  const missing = dataApiReads.filter((name) => !served.includes(name));
  expect(
    missing,
    `Scaffold each with pnpm --dir control mdp scaffold api <mart>: ${missing.join(", ")}`,
  ).toEqual([]);
  expect(dataApiReads.filter((name) => !(name in dataContract))).toEqual([]);
});
