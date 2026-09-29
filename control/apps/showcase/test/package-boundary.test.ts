import { readdirSync, readFileSync } from "node:fs";
import { expect, it } from "vitest";

it("keeps application imports inside package boundaries", () => {
  const root = new URL("../", import.meta.url);
  for (const directory of ["app", "components", "server", "lib"]) {
    for (const file of readdirSync(new URL(`${directory}/`, root), { recursive: true, encoding: "utf8" })) {
      if (!/\.tsx?$/.test(file)) continue;
      const source = readFileSync(new URL(`${directory}/${file}`, root), "utf8");
      expect(source, `${directory}/${file}: use @mdp/contracts for shared reads`).not.toMatch(/(?:from\s*|import\s*\()["'][^"']*control-api\/src/);
    }
  }
});
