import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { marks } from "@mdp/contracts/marks";
import { markLicenses } from "../scripts/mark-licenses";
const folder = new URL("../public/brand/third-party/", import.meta.url);
const licenses = readFileSync(new URL("LICENSES.md", folder), "utf8");
describe("provider files", () => {
  it("keeps each file below 12 KB and credited", () => {
    for (const file of readdirSync(folder).filter(
      (name) => name !== "LICENSES.md",
    )) {
      expect(statSync(new URL(file, folder)).size, file).toBeLessThan(
        12 * 1024,
      );
      expect(licenses).toContain(file);
    }
  });
  it("matches the registry and ships every named file", () => {
    expect(licenses).toBe(markLicenses());
    for (const mark of Object.values(marks))
      for (const file of Object.values(mark.files))
        expect(existsSync(new URL(file, folder)), file).toBe(true);
  });
});
